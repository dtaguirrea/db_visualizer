"""Persistencia de tablas dinámicas con auditoría y control de concurrencia."""
from datetime import date, datetime, time
from decimal import Decimal

import pandas as pd
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from src.audit import log_action
from src.database import get_primary_key_column, get_dynamic_column_metadata, is_postgres, utc_now
from src.field_values import normalize_field, column_options

AUDIT_COLUMNS = ("created_by", "created_at", "updated_by", "updated_at")


class ConcurrentEditError(RuntimeError):
    """La fila cambió en la base desde que el usuario abrió el editor."""


class InvalidRecordError(ValueError):
    """Un valor ingresado no se puede persistir de forma segura."""


class RecordInUseError(InvalidRecordError):
    """Otro registro todavía referencia la fila que se intenta eliminar."""


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        missing = pd.isna(value)
        return bool(missing) if not hasattr(missing, "__len__") else False
    except (TypeError, ValueError):
        return False


def _clean_value(value):
    if _is_missing(value):
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _pk_key(value):
    return _clean_value(value)


def _quote(conn: Connection, identifier: str) -> str:
    return conn.dialect.identifier_preparer.quote(identifier)


def _canonical_user_values(conn: Connection, table_name: str, values: dict, pk_column: str) -> dict:
    """Mapea las claves recibidas a los nombres físicos reales, ignorando diferencias de mayúsculas.

    Esto también protege instalaciones que alcanzaron a guardar metadatos duplicados como
    ``Nombre``/``nombre`` antes de la migración de v1.1.0.
    """
    physical_columns = {
        column["name"].casefold(): column["name"]
        for column in inspect(conn).get_columns(table_name)
    }
    protected = {pk_column.casefold(), *(column.casefold() for column in AUDIT_COLUMNS)}
    canonical: dict = {}

    for supplied_name, supplied_value in values.items():
        logical_name = str(supplied_name).casefold()
        if logical_name in protected:
            continue
        physical_name = physical_columns.get(logical_name)
        if physical_name is None:
            raise InvalidRecordError(
                f"La columna '{supplied_name}' no existe en la tabla '{table_name}'. Recarga la página."
            )

        cleaned = _clean_value(supplied_value)
        if physical_name not in canonical:
            canonical[physical_name] = cleaned
            continue

        previous = canonical[physical_name]
        if _is_missing(previous) and not _is_missing(cleaned):
            canonical[physical_name] = cleaned
        elif not _is_missing(previous) and not _is_missing(cleaned) and previous != cleaned:
            raise InvalidRecordError(
                f"El campo '{physical_name}' fue recibido dos veces con valores distintos. Recarga la página."
            )

    metadata = {c['column_name']: c for c in get_dynamic_column_metadata(conn, table_name)}
    for name, value in canonical.items():
        if name in metadata:
            column = metadata[name]
            try:
                canonical[name] = normalize_field(value, column['column_type'], column_options(column))
            except ValueError as exc:
                raise InvalidRecordError(f"{name}: {exc}") from exc
    return canonical


def insert_record(conn: Connection, table_name: str, values: dict, username: str, *, commit: bool = True):
    """Inserta una fila sin depender de cómo Streamlit represente una fila vacía."""
    pk_column = get_primary_key_column(conn, table_name)
    user_values = _canonical_user_values(conn, table_name, values, pk_column)
    if not user_values:
        raise InvalidRecordError("No hay datos para guardar.")

    now = utc_now()
    params = dict(user_values)
    params.update({
        "created_by": username,
        "created_at": now,
        "updated_by": username,
        "updated_at": now,
    })
    columns = list(user_values) + list(AUDIT_COLUMNS)
    placeholders = [f":{column}" for column in columns]
    q_table = _quote(conn, table_name)
    q_pk = _quote(conn, pk_column)
    q_columns = [_quote(conn, column) for column in columns]

    try:
        result = conn.execute(text(
            f"INSERT INTO {q_table} ({', '.join(q_columns)}) "
            f"VALUES ({', '.join(placeholders)}) RETURNING {q_pk}"
        ), params)
        new_pk = result.scalar_one()
        log_action(conn, table_name, new_pk, "INSERT", username,
                   details={"columns": list(user_values)})
        if commit:
            conn.commit()
        return new_pk
    except Exception:
        if commit:
            conn.rollback()
        raise


def sync_table_edits(conn: Connection, table_name: str, original_df: pd.DataFrame,
                     edited_df: pd.DataFrame, username: str) -> dict:
    """Sincroniza cambios sobre filas existentes y eliminaciones."""
    pk_column = get_primary_key_column(conn, table_name)
    editable_columns = [
        c for c in edited_df.columns
        if c not in AUDIT_COLUMNS and c != pk_column
    ]
    summary = {"inserted": 0, "updated": 0, "deleted": 0}
    now = utc_now()
    q_table = _quote(conn, table_name)
    q_pk = _quote(conn, pk_column)

    original_by_pk = {
        _pk_key(row[pk_column]): row
        for row in original_df.to_dict("records")
        if not _is_missing(row.get(pk_column))
    }
    seen_pks = set()

    try:
        for _, edited_row in edited_df.iterrows():
            raw_pk = edited_row.get(pk_column)
            if _is_missing(raw_pk):
                params = {c: _clean_value(edited_row.get(c)) for c in editable_columns}
                insert_record(conn, table_name, params, username, commit=False)
                summary["inserted"] += 1
                continue

            pk_value = _pk_key(raw_pk)
            seen_pks.add(pk_value)
            original_row = original_by_pk.get(pk_value)
            if original_row is None:
                raise ConcurrentEditError(
                    f"El registro {pk_value} ya no coincide con la copia abierta. Recarga la tabla."
                )

            changed_columns = [
                c for c in editable_columns
                if _clean_value(edited_row.get(c)) != _clean_value(original_row.get(c))
            ]
            if changed_columns:
                params = _canonical_user_values(conn, table_name, {c: edited_row.get(c) for c in changed_columns}, pk_column)
                params.update({
                    "updated_by": username,
                    "updated_at": now,
                    "pk": pk_value,
                    "original_updated_at": _clean_value(original_row.get("updated_at")),
                })
                assignments = [f"{_quote(conn, c)} = :{c}" for c in changed_columns]
                assignments += ["updated_by = :updated_by", "updated_at = :updated_at"]
                concurrency_clause = (
                    "updated_at IS NULL" if params["original_updated_at"] is None
                    else "updated_at = :original_updated_at"
                )
                result = conn.execute(text(
                    f"UPDATE {q_table} SET {', '.join(assignments)} "
                    f"WHERE {q_pk} = :pk AND {concurrency_clause}"
                ), params)
                if result.rowcount != 1:
                    raise ConcurrentEditError(
                        f"El registro {pk_value} cambió desde que abriste la tabla. "
                        "Recarga la página antes de guardar nuevamente."
                    )
                log_action(conn, table_name, pk_value, "UPDATE", username,
                           details={"columns": changed_columns})
                summary["updated"] += 1

        for pk_value in set(original_by_pk) - seen_pks:
            _check_references(conn, table_name, pk_value)
            original_row = original_by_pk[pk_value]
            original_updated_at = _clean_value(original_row.get("updated_at"))
            params = {"pk": pk_value, "original_updated_at": original_updated_at}
            concurrency_clause = (
                "updated_at IS NULL" if original_updated_at is None
                else "updated_at = :original_updated_at"
            )
            result = conn.execute(text(
                f"DELETE FROM {q_table} WHERE {q_pk} = :pk AND {concurrency_clause}"
            ), params)
            if result.rowcount != 1:
                raise ConcurrentEditError(
                    f"El registro {pk_value} cambió antes de ser eliminado. Recarga la tabla."
                )
            log_action(conn, table_name, pk_value, "DELETE", username)
            summary["deleted"] += 1

        conn.commit()
        return summary
    except Exception:
        conn.rollback()
        raise


def _check_references(conn, table_name, pk_value):
    if is_postgres(conn):
        pk = get_primary_key_column(conn, table_name)
        # Impide que aparezca un vínculo nuevo entre la comprobación y el DELETE.
        conn.execute(text(f"SELECT {_quote(conn, pk)} FROM {_quote(conn, table_name)} WHERE {_quote(conn, pk)}=:pk FOR UPDATE"), {'pk': pk_value})
    for source in inspect(conn).get_table_names():
        for fk in inspect(conn).get_foreign_keys(source):
            if fk.get('referred_table') != table_name:
                continue
            for column in fk.get('constrained_columns', []):
                if conn.execute(text(f"SELECT 1 FROM {_quote(conn, source)} WHERE {_quote(conn, column)} = :pk LIMIT 1"), {'pk': pk_value}).first():
                    raise RecordInUseError(f"El registro {pk_value} está referenciado por {source}. Elimina primero ese vínculo.")


def delete_records(conn: Connection, table_name: str, records: list[dict], username: str) -> int:
    """Elimina explícitamente varias filas con auditoría y control de concurrencia."""
    if not records:
        return 0
    pk_column = get_primary_key_column(conn, table_name)
    q_table = _quote(conn, table_name)
    q_pk = _quote(conn, pk_column)
    deleted = 0
    try:
        for record in records:
            _check_references(conn, table_name, record["pk"])
            pk_value = _clean_value(record.get("pk"))
            expected_updated_at = _clean_value(record.get("updated_at"))
            params = {"pk": pk_value, "expected_updated_at": expected_updated_at}
            concurrency_clause = (
                "updated_at IS NULL" if expected_updated_at is None
                else "updated_at = :expected_updated_at"
            )
            result = conn.execute(text(
                f"DELETE FROM {q_table} WHERE {q_pk} = :pk AND {concurrency_clause}"
            ), params)
            if result.rowcount != 1:
                raise ConcurrentEditError(
                    f"El registro {pk_value} cambió o ya fue eliminado. Recarga la tabla."
                )
            log_action(conn, table_name, pk_value, "DELETE", username)
            deleted += 1
        conn.commit()
        return deleted
    except IntegrityError as exc:
        conn.rollback()
        raise RecordInUseError(
            "No se puede eliminar la selección porque otro registro todavía la referencia. "
            "Elimina primero los registros dependientes."
        ) from exc
    except Exception:
        conn.rollback()
        raise
