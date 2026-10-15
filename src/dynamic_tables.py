"""Creación y eliminación controlada de tablas personalizadas."""
import re
import json
from src.field_values import normalize_options

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
    "Opciones": "TEXT",
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


def prepare_column(conn, column, seen, ordinal):
    quote = conn.dialect.identifier_preparer.quote
    name = validate_identifier(column['name'], 'nombre de columna')
    if name.casefold() in {value.casefold() for value in seen}:
        raise InvalidIdentifierError(f"La columna '{name}' está repetida o reservada.")
    seen.add(name)
    kind = column.get('type') or 'Texto'
    if kind not in {*_TYPE_MAP, 'Documento', 'Llave foránea'}:
        raise ValueError('Tipo de columna no válido.')
    target = column.get('foreign_key_table')
    if kind == 'Documento':
        target = 'document_records'
    if kind == 'Llave foránea' and not target:
        raise ValueError('Selecciona una tabla relacionada.')
    if target and kind not in ('Documento', 'Llave foránea'):
        raise ValueError('Elige Llave foránea para definir una relación.')
    options = normalize_options(column.get('options', [])) if kind == 'Opciones' else None
    if target:
        allowed = {'document_records'} | {row['table_name'] for row in list_registered_tables(conn)}
        if target not in allowed:
            raise ValueError('La tabla relacionada debe ser Documentos o una tabla personalizada.')
        pk = get_primary_key_column(conn, target)
        physical = next(c for c in inspect(conn).get_columns(target) if c['name'] == pk)
        definition = f"{quote(name)} {physical['type']} REFERENCES {quote(target)}({quote(pk)}) ON DELETE RESTRICT"
    else:
        definition = f"{quote(name)} {_TYPE_MAP[kind]}"
    if options:
        literals = ', '.join("'" + value.replace("'", "''") + "'" for value in options)
        definition += f" CHECK ({quote(name)} IN ({literals}))"
    return definition, dict(name=name, type=kind, foreign_key_table=target,
                            ordinal_position=ordinal,
                            options_json=json.dumps(options, ensure_ascii=False) if options else None)


def add_dynamic_column(conn, table_name, column, username):
    """Amplía el esquema y sus metadatos en una única transacción, dejando NULL en filas previas."""
    try:
        if is_postgres(conn):
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('db_visualizer_schema'))"))
        elif not conn.connection.driver_connection.in_transaction:
            conn.exec_driver_sql('BEGIN')
        if table_name not in {row['table_name'] for row in list_registered_tables(conn)}:
            raise ValueError('Selecciona una tabla personalizada registrada.')
        quote = conn.dialect.identifier_preparer.quote
        physical = inspect(conn).get_columns(table_name)
        seen = {c['name'] for c in physical}
        ordinal = conn.execute(text('SELECT COALESCE(MAX(ordinal_position), -1) + 1 FROM dynamic_table_columns WHERE table_name=:table'),
                               {'table': table_name}).scalar_one()
        definition, normalized = prepare_column(conn, column, seen, ordinal)
        # DDL es generado desde identificadores validados y literales escapados.
        conn.execution_options(no_parameters=True).exec_driver_sql(f"ALTER TABLE {quote(table_name)} ADD COLUMN {definition}")
        conn.execute(text("""INSERT INTO dynamic_table_columns
            (table_name, column_name, column_type, ordinal_position, foreign_key_table, options_json)
            VALUES (:table, :name, :type, :ordinal_position, :foreign_key_table, :options_json)"""),
            dict(table=table_name, **normalized))
        log_action(conn, table_name, None, 'ADD_COLUMN', username,
                   details={'column': normalized['name'], 'type': normalized['type']})
        conn.commit()
        return normalized['name']
    except Exception:
        conn.rollback()
        raise


def create_dynamic_table(conn: Connection, table_name: str, columns: list,
                         description: str, username: str, *, commit: bool = True) -> str:
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
        definition, normalized = prepare_column(conn, column, seen, ordinal)
        definitions.append(definition)
        normalized_columns.append(normalized)

    definitions.extend([
        "created_by TEXT NOT NULL",
        "created_at TEXT NOT NULL",
        "updated_by TEXT",
        "updated_at TEXT",
    ])
    ddl = f"CREATE TABLE {quote(safe_table_name)} (\n  " + ",\n  ".join(definitions + foreign_keys) + "\n)"
    conn.execution_options(no_parameters=True).exec_driver_sql(ddl)
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
                table_name, column_name, column_type, ordinal_position, foreign_key_table, options_json
            ) VALUES (
                :table_name, :column_name, :column_type, :ordinal_position, :foreign_key_table, :options_json
            )
        '''), {
            "table_name": safe_table_name,
            "column_name": column["name"],
            "column_type": column["type"],
            "ordinal_position": column["ordinal_position"],
            "foreign_key_table": column["foreign_key_table"],
            "options_json": column["options_json"],
        })
    log_action(conn, safe_table_name, None, "CREATE_TABLE", username,
               details={"columns": [c["name"] for c in normalized_columns]})
    if commit:
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
