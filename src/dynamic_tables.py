"""Creación y eliminación controlada de tablas personalizadas."""
import re

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

import config
from src.audit import log_action
from src.database import get_incoming_foreign_keys, get_primary_key_column, is_postgres, utc_now

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_TYPE_MAP = {
    "Texto": "TEXT",
    "Número entero": "BIGINT",
    "Número decimal": "DOUBLE PRECISION",
    "Fecha": "TEXT",
}


class InvalidIdentifierError(ValueError):
    pass


def validate_identifier(name: str, kind: str = "identificador") -> str:
    candidate = (name or "").strip().replace(" ", "_").lower()
    if not _IDENTIFIER_PATTERN.match(candidate):
        raise InvalidIdentifierError(
            f"El {kind} '{name}' no es válido. Usa letras, números y guiones bajos, "
            "sin comenzar por un número."
        )
    if candidate.lower().startswith(("sqlite_", "pg_")):
        raise InvalidIdentifierError(f"El {kind} '{name}' usa un prefijo reservado.")
    return candidate


def create_dynamic_table(conn: Connection, table_name: str, columns: list,
                         description: str, username: str) -> str:
    safe_table_name = validate_identifier(table_name, "nombre de tabla")
    if safe_table_name in config.PROTECTED_TABLES:
        raise InvalidIdentifierError(f"'{safe_table_name}' es un nombre reservado del sistema.")
    if safe_table_name in inspect(conn).get_table_names():
        raise InvalidIdentifierError(f"Ya existe una tabla llamada '{safe_table_name}'.")
    if not columns:
        raise ValueError("Define al menos una columna.")

    quote = conn.dialect.identifier_preparer.quote
    primary_key = "record_id BIGSERIAL PRIMARY KEY" if is_postgres(conn) else "record_id INTEGER PRIMARY KEY AUTOINCREMENT"
    definitions = [primary_key]
    foreign_keys = []
    normalized_columns = []
    seen = {"record_id", "created_by", "created_at", "updated_by", "updated_at"}

    for ordinal, column in enumerate(columns):
        column_name = validate_identifier(column["name"], "nombre de columna")
        if column_name in seen:
            raise InvalidIdentifierError(f"La columna '{column_name}' está repetida o reservada.")
        seen.add(column_name)

        foreign_table = column.get("foreign_key_table")
        column_type = column.get("type") or "Texto"
        if foreign_table:
            foreign_table = validate_identifier(foreign_table, "tabla referenciada")
            if foreign_table not in inspect(conn).get_table_names():
                raise InvalidIdentifierError(f"La tabla referenciada '{foreign_table}' no existe.")
            referenced_pk = get_primary_key_column(conn, foreign_table)
            referenced_column = next(
                col for col in inspect(conn).get_columns(foreign_table) if col["name"] == referenced_pk
            )
            referenced_type = str(referenced_column["type"])
            definitions.append(f"{quote(column_name)} {referenced_type}")
            foreign_keys.append(
                f"FOREIGN KEY ({quote(column_name)}) REFERENCES {quote(foreign_table)}({quote(referenced_pk)}) ON DELETE SET NULL"
            )
        else:
            definitions.append(f"{quote(column_name)} {_TYPE_MAP.get(column_type, 'TEXT')}")

        normalized_columns.append({
            "name": column_name,
            "type": column_type,
            "foreign_key_table": foreign_table,
            "ordinal_position": ordinal,
        })

    definitions.extend([
        "created_by TEXT NOT NULL",
        "created_at TEXT NOT NULL",
        "updated_by TEXT",
        "updated_at TEXT",
    ])
    ddl = f"CREATE TABLE {quote(safe_table_name)} (\n  " + ",\n  ".join(definitions + foreign_keys) + "\n)"
    conn.execute(text(ddl))
    conn.execute(text('''
        INSERT INTO dynamic_tables_registry (table_name, description, created_by, created_at)
        VALUES (:table_name, :description, :created_by, :created_at)
    '''), {
        "table_name": safe_table_name,
        "description": description.strip() if description else None,
        "created_by": username,
        "created_at": utc_now(),
    })
    for column in normalized_columns:
        conn.execute(text('''
            INSERT INTO dynamic_table_columns (
                table_name, column_name, column_type, ordinal_position, foreign_key_table
            ) VALUES (
                :table_name, :column_name, :column_type, :ordinal_position, :foreign_key_table
            )
        '''), {
            "table_name": safe_table_name,
            "column_name": column["name"],
            "column_type": column["type"],
            "ordinal_position": column["ordinal_position"],
            "foreign_key_table": column["foreign_key_table"],
        })
    log_action(conn, safe_table_name, None, "CREATE_TABLE", username,
               details={"columns": [c["name"] for c in normalized_columns]})
    conn.commit()
    return safe_table_name


def drop_dynamic_table(conn: Connection, table_name: str, username: str) -> None:
    safe_table_name = validate_identifier(table_name, "nombre de tabla")
    if safe_table_name in config.PROTECTED_TABLES:
        raise InvalidIdentifierError(f"'{safe_table_name}' es una tabla del sistema y no puede eliminarse.")
    incoming = get_incoming_foreign_keys(conn, safe_table_name)
    if incoming:
        sources = ", ".join(sorted({item["table_name"] for item in incoming}))
        raise ValueError(
            f"No se puede eliminar '{safe_table_name}' porque está referenciada por: {sources}. "
            "Elimina primero esas relaciones."
        )
    log_action(conn, safe_table_name, None, "DROP_TABLE", username)
    quote = conn.dialect.identifier_preparer.quote
    conn.execute(text(f"DROP TABLE IF EXISTS {quote(safe_table_name)}"))
    conn.execute(text("DELETE FROM dynamic_table_columns WHERE table_name = :table_name"),
                 {"table_name": safe_table_name})
    conn.execute(text("DELETE FROM dynamic_tables_registry WHERE table_name = :table_name"),
                 {"table_name": safe_table_name})
    conn.commit()


def list_registered_tables(conn: Connection) -> list:
    rows = conn.execute(text(
        "SELECT * FROM dynamic_tables_registry ORDER BY created_at DESC"
    )).mappings().all()
    return [dict(row) for row in rows]
