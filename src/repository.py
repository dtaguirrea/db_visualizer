"""Persistencia de tablas dinámicas con auditoría y control de concurrencia."""
from datetime import date, datetime, time
from decimal import Decimal

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.audit import log_action
from src.database import get_primary_key_column, utc_now

AUDIT_COLUMNS = ("created_by", "created_at", "updated_by", "updated_at")


class ConcurrentEditError(RuntimeError):
    """La fila cambió en la base desde que el usuario abrió el editor."""


class InvalidRecordError(ValueError):
    """Un valor ingresado no se puede persistir de forma segura."""


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


def insert_record(conn: Connection, table_name: str, values: dict, username: str, *, commit: bool = True):
    """Inserta una fila sin depender de cómo Streamlit represente una fila vacía."""
    pk_column = get_primary_key_column(conn, table_name)
    user_values = {
        column: _clean_value(value)
        for column, value in values.items()
        if column not in AUDIT_COLUMNS and column != pk_column
    }
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

    try:
        result = conn.execute(text(
            f"INSERT INTO {table_name} ({', '.join(columns)}) "
            f"VALUES ({', '.join(placeholders)}) RETURNING {pk_column}"
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
    """Sincroniza cambios sobre filas existentes y eliminaciones.

    Las nuevas filas también se aceptan por compatibilidad, aunque la interfaz
    principal utiliza ``insert_record`` desde un formulario dedicado.
    """
    pk_column = get_primary_key_column(conn, table_name)
    editable_columns = [
        c for c in edited_df.columns
        if c not in AUDIT_COLUMNS and c != pk_column
    ]
    summary = {"inserted": 0, "updated": 0, "deleted": 0}
    now = utc_now()

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
                new_pk = insert_record(conn, table_name, params, username, commit=False)
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
                params = {c: _clean_value(edited_row.get(c)) for c in changed_columns}
                params.update({
                    "updated_by": username,
                    "updated_at": now,
                    "pk": pk_value,
                    "original_updated_at": _clean_value(original_row.get("updated_at")),
                })
                assignments = [f"{c} = :{c}" for c in changed_columns]
                assignments += ["updated_by = :updated_by", "updated_at = :updated_at"]
                concurrency_clause = (
                    "updated_at IS NULL" if params["original_updated_at"] is None
                    else "updated_at = :original_updated_at"
                )
                result = conn.execute(text(
                    f"UPDATE {table_name} SET {', '.join(assignments)} "
                    f"WHERE {pk_column} = :pk AND {concurrency_clause}"
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
            original_row = original_by_pk[pk_value]
            original_updated_at = _clean_value(original_row.get("updated_at"))
            params = {"pk": pk_value, "original_updated_at": original_updated_at}
            concurrency_clause = (
                "updated_at IS NULL" if original_updated_at is None
                else "updated_at = :original_updated_at"
            )
            result = conn.execute(text(
                f"DELETE FROM {table_name} WHERE {pk_column} = :pk AND {concurrency_clause}"
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
