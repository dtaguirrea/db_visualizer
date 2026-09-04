"""Acceso a datos de db_visualizer con PostgreSQL y SQLite para pruebas locales."""
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Connection, Engine

import config


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    kwargs = {"pool_pre_ping": True}
    if config.DATABASE_URL.startswith("sqlite:"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(config.DATABASE_URL, future=True, **kwargs)

    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def get_connection() -> Connection:
    return get_engine().connect()


def is_postgres(conn: Connection | None = None) -> bool:
    dialect = conn.dialect.name if conn is not None else get_engine().dialect.name
    return dialect == "postgresql"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _pk_sql(name: str) -> str:
    return f"{name} BIGSERIAL PRIMARY KEY" if is_postgres() else f"{name} INTEGER PRIMARY KEY AUTOINCREMENT"


def _add_document_columns_if_needed(conn: Connection) -> None:
    """Migra document_records sin eliminar datos de instalaciones anteriores."""
    columns = {c["name"] for c in inspect(conn).get_columns("document_records")}
    additions = {
        "title": "TEXT",
        "document_date": "TEXT",
        "original_file_path": "TEXT",
        "ocr_file_path": "TEXT",
        "ocr_error": "TEXT",
        "queued_at": "TEXT",
        "processing_started_at": "TEXT",
        "processing_finished_at": "TEXT",
        "ocr_attempts": "INTEGER NOT NULL DEFAULT 0",
        "ocr_language": "TEXT",
    }
    for column_name, column_type in additions.items():
        if column_name not in columns:
            conn.execute(text(f"ALTER TABLE document_records ADD COLUMN {column_name} {column_type}"))

    # source_reference fue obligatorio en las primeras versiones. Desde 1.1.0
    # la procedencia es un metadato opcional, al igual que el título y la fecha.
    if is_postgres(conn):
        conn.execute(text(
            "ALTER TABLE document_records ALTER COLUMN source_reference DROP NOT NULL"
        ))

    conn.execute(text("""
        UPDATE document_records
        SET original_file_path = file_path
        WHERE original_file_path IS NULL AND file_path IS NOT NULL
    """))
    conn.execute(text("""
        UPDATE document_records
        SET ocr_status = 'en_cola', queued_at = COALESCE(queued_at, created_at)
        WHERE ocr_status = 'pendiente'
    """))
    conn.execute(text("""
        UPDATE document_records
        SET ocr_language = :default_language
        WHERE ocr_language IS NULL OR TRIM(ocr_language) = ''
    """), {"default_language": config.OCR_LANGUAGE})


def _add_extraction_columns_if_needed(conn: Connection) -> None:
    """Agrega campos de fichaje sin alterar extracciones existentes."""
    columns = {c["name"] for c in inspect(conn).get_columns("extraction_records")}
    if "page_number" not in columns:
        conn.execute(text("ALTER TABLE extraction_records ADD COLUMN page_number INTEGER"))


def _infer_column_type(column: dict, referred_table: str | None, candidates: list[dict]) -> str:
    if referred_table == "document_records":
        return "Documento"
    if referred_table:
        return "Llave foránea"

    # Conserva el tipo funcional registrado anteriormente (por ejemplo Fecha),
    # aunque PostgreSQL haya convertido el nombre de la columna a minúsculas.
    for candidate in sorted(candidates, key=lambda row: row.get("ordinal_position", 0)):
        candidate_type = candidate.get("column_type")
        if candidate_type and candidate_type not in {"Documento", "Llave foránea"}:
            return candidate_type

    sql_type = str(column["type"]).upper()
    if "INT" in sql_type:
        return "Número entero"
    if any(token in sql_type for token in ("DOUBLE", "FLOAT", "REAL", "NUMERIC", "DECIMAL")):
        return "Número decimal"
    if "DATE" in sql_type:
        return "Fecha"
    return "Texto"


def _reconcile_dynamic_column_registry(conn: Connection) -> None:
    """Repara metadatos de tablas dinámicas usando el esquema físico como fuente de verdad.

    Versiones anteriores podían registrar ``Nombre`` mientras PostgreSQL creaba físicamente
    ``nombre``. En el siguiente inicio el backfill añadía también ``nombre`` y la interfaz
    terminaba intentando insertar ambas columnas. Esta migración agrupa nombres sin distinguir
    mayúsculas/minúsculas y reconstruye un único registro canónico por columna, sin tocar las
    filas de las tablas de usuario.
    """
    inspector = inspect(conn)
    physical_tables = inspector.get_table_names()
    physical_lookup = {name.casefold(): name for name in physical_tables}
    registry_rows = conn.execute(text(
        "SELECT table_name, description, created_by, created_at FROM dynamic_tables_registry"
    )).mappings().all()

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in registry_rows:
        actual_name = physical_lookup.get(row["table_name"].casefold())
        if actual_name:
            grouped[actual_name].append(dict(row))

    for actual_table, registry_group in grouped.items():
        registry_names = {row["table_name"] for row in registry_group}
        metadata_rows: list[dict] = []
        for registry_name in registry_names:
            rows = conn.execute(text("""
                SELECT table_name, column_name, column_type, ordinal_position, foreign_key_table
                FROM dynamic_table_columns
                WHERE table_name = :table_name
            """), {"table_name": registry_name}).mappings().all()
            metadata_rows.extend(dict(row) for row in rows)

        # Asegura que el registry use exactamente el nombre físico de la tabla.
        canonical_registry = next(
            (row for row in registry_group if row["table_name"] == actual_table),
            registry_group[0],
        )
        conn.execute(text("""
            INSERT INTO dynamic_tables_registry (table_name, description, created_by, created_at)
            VALUES (:table_name, :description, :created_by, :created_at)
            ON CONFLICT (table_name) DO NOTHING
        """), {
            "table_name": actual_table,
            "description": canonical_registry.get("description"),
            "created_by": canonical_registry.get("created_by") or "system",
            "created_at": canonical_registry.get("created_at") or utc_now(),
        })

        for registry_name in registry_names | {actual_table}:
            conn.execute(text(
                "DELETE FROM dynamic_table_columns WHERE table_name = :table_name"
            ), {"table_name": registry_name})

        for registry_name in registry_names:
            if registry_name != actual_table:
                conn.execute(text(
                    "DELETE FROM dynamic_tables_registry WHERE table_name = :table_name"
                ), {"table_name": registry_name})

        foreign_keys = get_foreign_keys(conn, actual_table)
        pk = get_primary_key_column(conn, actual_table)
        audit_columns = {"created_by", "created_at", "updated_by", "updated_at"}

        ordinal = 0
        for column in inspector.get_columns(actual_table):
            physical_name = column["name"]
            if physical_name == pk or physical_name in audit_columns:
                continue

            candidates = [
                row for row in metadata_rows
                if str(row.get("column_name", "")).casefold() == physical_name.casefold()
            ]
            referred_table = foreign_keys.get(physical_name)
            column_type = _infer_column_type(column, referred_table, candidates)

            conn.execute(text("""
                INSERT INTO dynamic_table_columns (
                    table_name, column_name, column_type, ordinal_position, foreign_key_table
                ) VALUES (
                    :table_name, :column_name, :column_type, :ordinal_position, :foreign_key_table
                )
            """), {
                "table_name": actual_table,
                "column_name": physical_name,
                "column_type": column_type,
                "ordinal_position": ordinal,
                "foreign_key_table": referred_table,
            })
            ordinal += 1


def init_db() -> None:
    with get_engine().begin() as conn:
        conn.execute(text(f'''CREATE TABLE IF NOT EXISTS users (
            {_pk_sql("user_id")},
            username TEXT NOT NULL UNIQUE,
            full_name TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'investigador',
            must_change_password INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_login_at TEXT
        )'''))

        conn.execute(text(f'''CREATE TABLE IF NOT EXISTS auth_sessions (
            {_pk_sql("session_id")},
            user_id BIGINT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )'''))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_auth_sessions_token_hash ON auth_sessions(token_hash)"))

        conn.execute(text(f'''CREATE TABLE IF NOT EXISTS document_records (
            {_pk_sql("document_id")},
            title TEXT,
            source_reference TEXT,
            document_date TEXT,
            original_format TEXT,
            file_path TEXT,
            original_file_path TEXT,
            ocr_file_path TEXT,
            ocr_text TEXT,
            ocr_status TEXT NOT NULL DEFAULT 'pendiente',
            ocr_error TEXT,
            queued_at TEXT,
            processing_started_at TEXT,
            processing_finished_at TEXT,
            ocr_attempts INTEGER NOT NULL DEFAULT 0,
            ocr_language TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_by TEXT,
            updated_at TEXT
        )'''))
        _add_document_columns_if_needed(conn)
        conn.execute(text('''
            CREATE INDEX IF NOT EXISTS idx_document_records_ocr_queue
            ON document_records(ocr_status, queued_at, document_id)
        '''))

        conn.execute(text(f'''CREATE TABLE IF NOT EXISTS extraction_records (
            {_pk_sql("extraction_id")},
            document_id BIGINT NOT NULL,
            element_category TEXT,
            key_information TEXT,
            literal_quote TEXT,
            cross_reference_notes TEXT,
            page_number INTEGER,
            review_status TEXT NOT NULL DEFAULT 'Pendiente',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_by TEXT,
            updated_at TEXT,
            FOREIGN KEY (document_id) REFERENCES document_records(document_id) ON DELETE CASCADE
        )'''))
        _add_extraction_columns_if_needed(conn)

        conn.execute(text('''CREATE TABLE IF NOT EXISTS dynamic_tables_registry (
            table_name TEXT PRIMARY KEY,
            description TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        )'''))

        conn.execute(text('''CREATE TABLE IF NOT EXISTS dynamic_table_columns (
            table_name TEXT NOT NULL,
            column_name TEXT NOT NULL,
            column_type TEXT NOT NULL,
            ordinal_position INTEGER NOT NULL,
            foreign_key_table TEXT,
            PRIMARY KEY (table_name, column_name),
            FOREIGN KEY (table_name) REFERENCES dynamic_tables_registry(table_name) ON DELETE CASCADE
        )'''))

        conn.execute(text(f'''CREATE TABLE IF NOT EXISTS audit_log (
            {_pk_sql("log_id")},
            table_name TEXT NOT NULL,
            record_id TEXT,
            action TEXT NOT NULL,
            username TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            details TEXT
        )'''))

        _reconcile_dynamic_column_registry(conn)

        if is_postgres(conn):
            conn.execute(text('''
                CREATE INDEX IF NOT EXISTS idx_document_records_ocr_fts
                ON document_records
                USING GIN (to_tsvector('spanish', COALESCE(ocr_text, '')))
            '''))
            conn.execute(text('''
                CREATE INDEX IF NOT EXISTS idx_document_records_search_fts
                ON document_records
                USING GIN (
                    to_tsvector(
                        'spanish',
                        COALESCE(title, '') || ' ' ||
                        COALESCE(source_reference, '') || ' ' ||
                        COALESCE(document_date, '') || ' ' ||
                        COALESCE(original_format, '') || ' ' ||
                        COALESCE(ocr_text, '')
                    )
                )
            '''))
        else:
            conn.execute(text('''CREATE VIRTUAL TABLE IF NOT EXISTS document_records_fts USING fts5(
                document_id UNINDEXED, ocr_text
            )'''))
            conn.execute(text('''CREATE TRIGGER IF NOT EXISTS trg_document_records_ai
                AFTER INSERT ON document_records BEGIN
                    INSERT INTO document_records_fts(document_id, ocr_text)
                    VALUES (new.document_id, new.ocr_text);
                END;'''))
            conn.execute(text('''CREATE TRIGGER IF NOT EXISTS trg_document_records_au
                AFTER UPDATE ON document_records BEGIN
                    UPDATE document_records_fts SET ocr_text = new.ocr_text
                    WHERE document_id = new.document_id;
                END;'''))
            conn.execute(text('''CREATE TRIGGER IF NOT EXISTS trg_document_records_ad
                AFTER DELETE ON document_records BEGIN
                    DELETE FROM document_records_fts WHERE document_id = old.document_id;
                END;'''))
            conn.execute(text("DELETE FROM document_records_fts"))
            conn.execute(text('''
                INSERT INTO document_records_fts(document_id, ocr_text)
                SELECT document_id, COALESCE(ocr_text, '') FROM document_records
            '''))


def get_primary_key_column(conn: Connection, table_name: str) -> str:
    pk = inspect(conn).get_pk_constraint(table_name).get("constrained_columns") or []
    if not pk:
        raise ValueError(f"La tabla '{table_name}' no tiene llave primaria.")
    return pk[0]


def get_foreign_keys(conn: Connection, table_name: str) -> dict[str, str]:
    result = {}
    for fk in inspect(conn).get_foreign_keys(table_name):
        constrained = fk.get("constrained_columns") or []
        if constrained:
            result[constrained[0]] = fk.get("referred_table")
    return result


def get_incoming_foreign_keys(conn: Connection, table_name: str) -> list[dict]:
    incoming = []
    inspector = inspect(conn)
    for source_table in inspector.get_table_names():
        if source_table == table_name:
            continue
        for fk in inspector.get_foreign_keys(source_table):
            if fk.get("referred_table") != table_name:
                continue
            incoming.append({
                "table_name": source_table,
                "columns": fk.get("constrained_columns") or [],
            })
    return incoming


def get_dynamic_column_metadata(conn: Connection, table_name: str) -> list[dict]:
    rows = conn.execute(text('''
        SELECT column_name, column_type, ordinal_position, foreign_key_table
        FROM dynamic_table_columns
        WHERE table_name = :table_name
        ORDER BY ordinal_position, column_name
    '''), {"table_name": table_name}).mappings().all()
    return [dict(row) for row in rows]


def list_user_tables(conn: Connection) -> list[str]:
    names = inspect(conn).get_table_names()
    return sorted(
        name for name in names
        if not name.startswith("sqlite_") and not name.endswith("_fts")
    )
