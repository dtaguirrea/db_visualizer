"""Acceso a datos de db_visualizer con PostgreSQL y SQLite para pruebas locales."""
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
    columns = {c["name"] for c in inspect(conn).get_columns("document_records")}
    additions = {
        "original_file_path": "TEXT",
        "ocr_file_path": "TEXT",
        "ocr_error": "TEXT",
    }
    for column_name, column_type in additions.items():
        if column_name not in columns:
            conn.execute(text(f"ALTER TABLE document_records ADD COLUMN {column_name} {column_type}"))
    conn.execute(text("""
        UPDATE document_records
        SET original_file_path = file_path
        WHERE original_file_path IS NULL AND file_path IS NOT NULL
    """))


def _backfill_dynamic_column_registry(conn: Connection) -> None:
    """Registra metadatos mínimos para tablas creadas en versiones anteriores."""
    inspector = inspect(conn)
    registered_tables = {
        row[0] for row in conn.execute(text("SELECT table_name FROM dynamic_tables_registry")).all()
    }
    for table_name in registered_tables:
        if table_name not in inspector.get_table_names():
            continue
        existing = {
            row[0] for row in conn.execute(text(
                "SELECT column_name FROM dynamic_table_columns WHERE table_name = :table_name"
            ), {"table_name": table_name}).all()
        }
        foreign_keys = get_foreign_keys(conn, table_name)
        pk = get_primary_key_column(conn, table_name)
        ordinal = 0
        for column in inspector.get_columns(table_name):
            name = column["name"]
            if name == pk or name in {"created_by", "created_at", "updated_by", "updated_at"}:
                continue
            if name in existing:
                ordinal += 1
                continue
            referred_table = foreign_keys.get(name)
            if referred_table == "document_records":
                column_type = "Documento"
            elif referred_table:
                column_type = "Llave foránea"
            else:
                sql_type = str(column["type"]).upper()
                if "INT" in sql_type:
                    column_type = "Número entero"
                elif any(token in sql_type for token in ("DOUBLE", "FLOAT", "REAL", "NUMERIC", "DECIMAL")):
                    column_type = "Número decimal"
                else:
                    column_type = "Texto"
            conn.execute(text("""
                INSERT INTO dynamic_table_columns (
                    table_name, column_name, column_type, ordinal_position, foreign_key_table
                ) VALUES (
                    :table_name, :column_name, :column_type, :ordinal_position, :foreign_key_table
                )
                ON CONFLICT (table_name, column_name) DO NOTHING
            """), {
                "table_name": table_name,
                "column_name": name,
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
            source_reference TEXT NOT NULL,
            original_format TEXT,
            file_path TEXT,
            original_file_path TEXT,
            ocr_file_path TEXT,
            ocr_text TEXT,
            ocr_status TEXT NOT NULL DEFAULT 'pendiente',
            ocr_error TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_by TEXT,
            updated_at TEXT
        )'''))
        _add_document_columns_if_needed(conn)

        conn.execute(text(f'''CREATE TABLE IF NOT EXISTS extraction_records (
            {_pk_sql("extraction_id")},
            document_id BIGINT NOT NULL,
            element_category TEXT,
            key_information TEXT,
            literal_quote TEXT,
            cross_reference_notes TEXT,
            review_status TEXT NOT NULL DEFAULT 'Pendiente',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_by TEXT,
            updated_at TEXT,
            FOREIGN KEY (document_id) REFERENCES document_records(document_id) ON DELETE CASCADE
        )'''))

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

        _backfill_dynamic_column_registry(conn)

        if is_postgres(conn):
            conn.execute(text('''
                CREATE INDEX IF NOT EXISTS idx_document_records_ocr_fts
                ON document_records
                USING GIN (to_tsvector('spanish', COALESCE(ocr_text, '')))
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
            # La tabla FTS puede haberse creado después de documentos existentes.
            # Se reconstruye al iniciar para que el buscador no omita registros anteriores.
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
