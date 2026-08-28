import os
import shutil
import sys

TEST_ROOT = "/tmp/db_visualizer_test"
shutil.rmtree(TEST_ROOT, ignore_errors=True)
os.makedirs(TEST_ROOT, exist_ok=True)
os.environ["DATA_DIR"] = TEST_ROOT
os.environ["UPLOAD_DIR"] = f"{TEST_ROOT}/uploads"
os.environ["DATABASE_PATH"] = f"{TEST_ROOT}/test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_ROOT}/test.db"
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = "admin"
os.environ["BOOTSTRAP_ADMIN_FULL_NAME"] = "Administrador de pruebas"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "ClaveInicialSegura123!"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from sqlalchemy import text

import config
from src.audit import fetch_audit_log
from src.auth import (
    authenticate,
    authenticate_session,
    change_password,
    create_login_session,
    revoke_session,
    bootstrap_admin_if_empty,
)
from src.database import (
    get_connection,
    get_dynamic_column_metadata,
    get_foreign_keys,
    get_primary_key_column,
    init_db,
    list_user_tables,
    utc_now,
)
from src.documents import document_display_label, document_text_snippets, safe_pdf_filename
from src.dynamic_tables import InvalidIdentifierError, create_dynamic_table, drop_dynamic_table, validate_identifier
from src.repository import ConcurrentEditError, insert_record, sync_table_edits
from src.search import global_search
from src.table_relations import document_viewer_url, get_foreign_key_choices


def test_v1_1_0_core_logic():
    init_db()

    with get_connection() as conn:
        assert "document_records" in list_user_tables(conn)
        assert "dynamic_table_columns" in list_user_tables(conn)
        assert "auth_sessions" in list_user_tables(conn)

    created_username = bootstrap_admin_if_empty()
    assert created_username == "admin"
    assert bootstrap_admin_if_empty() is None

    user = authenticate("admin", config.BOOTSTRAP_ADMIN_PASSWORD)
    assert user is not None and user["must_change_password"] == 1
    assert authenticate("admin", "incorrecta") is None
    change_password(user["user_id"], "NuevaClaveSegura123")
    assert authenticate("admin", "NuevaClaveSegura123")["must_change_password"] == 0

    token, expires_at = create_login_session(user["user_id"])
    assert expires_at is not None
    assert authenticate_session(token)["username"] == "admin"
    revoke_session(token)
    assert authenticate_session(token) is None

    revoked_by_password, _ = create_login_session(user["user_id"])
    assert authenticate_session(revoked_by_password) is not None
    change_password(user["user_id"], "OtraClaveSegura456")
    assert authenticate_session(revoked_by_password) is None

    try:
        validate_identifier("1invalido")
        raise AssertionError("Debió fallar")
    except InvalidIdentifierError:
        pass
    assert validate_identifier("Mi Columna") == "mi_columna"

    with get_connection() as conn:
        now = utc_now()
        document_id = conn.execute(text('''
            INSERT INTO document_records (
                title, source_reference, document_date, original_format, file_path, original_file_path,
                ocr_text, ocr_status, created_by, created_at, updated_by, updated_at
            ) VALUES (
                :title, :source_reference, :document_date, :original_format, :file_path, :original_file_path,
                :ocr_text, :ocr_status, :created_by, :created_at, :updated_by, :updated_at
            ) RETURNING document_id
        '''), {
            "title": "Tarjeta histórica",
            "source_reference": "Archivo Nacional",
            "document_date": "1973-09-11",
            "original_format": "PDF digitalizado",
            "file_path": "/tmp/fake.pdf",
            "original_file_path": "/tmp/fake.pdf",
            "ocr_text": "texto de prueba sobre una tarjeta histórica",
            "ocr_status": "completado",
            "created_by": "admin",
            "created_at": now,
            "updated_by": "admin",
            "updated_at": now,
        }).scalar_one()
        conn.commit()

        null_metadata_document_id = conn.execute(text('''
            INSERT INTO document_records (
                title, source_reference, document_date, original_format, file_path, original_file_path,
                ocr_text, ocr_status, created_by, created_at, updated_by, updated_at
            ) VALUES (
                NULL, NULL, NULL, 'PDF nativo', '/tmp/empty.pdf', '/tmp/empty.pdf',
                '', 'pendiente', 'admin', :now, 'admin', :now
            ) RETURNING document_id
        '''), {"now": utc_now()}).scalar_one()
        conn.commit()
        assert null_metadata_document_id > document_id

        label = document_display_label({
            "document_id": document_id,
            "title": "Tarjeta histórica",
            "source_reference": "Archivo Nacional",
            "document_date": "1973-09-11",
        })
        assert label == f"{document_id} · Tarjeta histórica · Archivo Nacional · 1973-09-11"

        table_name = create_dynamic_table(
            conn,
            "hardware_items",
            [
                {"name": "nombre", "type": "Texto", "foreign_key_table": None},
                {"name": "fecha", "type": "Fecha", "foreign_key_table": None},
                {"name": "documento_id", "type": "Documento", "foreign_key_table": "document_records"},
            ],
            "Inventario",
            "admin",
        )
        assert get_primary_key_column(conn, table_name) == "record_id"
        assert get_foreign_keys(conn, table_name)["documento_id"] == "document_records"
        metadata = get_dynamic_column_metadata(conn, table_name)
        assert [item["column_type"] for item in metadata] == ["Texto", "Fecha", "Documento"]

        choices = get_foreign_key_choices(conn, table_name)["documento_id"]
        assert choices["labels"][document_id] == label
        assert document_viewer_url(document_id) == f"/documento?document_id={document_id}"

        new_pk = insert_record(conn, table_name, {
            "nombre": "Tarjeta perforada IBM",
            "fecha": "1973-09-11",
            "documento_id": document_id,
        }, "inv1")
        assert new_pk == 1

        after_insert = pd.read_sql_query(text(f"SELECT * FROM {table_name}"), conn)
        assert after_insert.iloc[0]["created_by"] == "inv1"
        assert int(after_insert.iloc[0]["documento_id"]) == document_id

        snapshot = after_insert.copy()
        blank_row = {column: None for column in snapshot.columns}
        blank_row["record_id"] = ""
        blank_row["nombre"] = "Fila con PK vacía"
        edited_with_blank_pk = pd.concat([snapshot, pd.DataFrame([blank_row])], ignore_index=True)
        assert sync_table_edits(conn, table_name, snapshot, edited_with_blank_pk, "inv1")["inserted"] == 1

        current = pd.read_sql_query(text(f"SELECT * FROM {table_name} ORDER BY record_id"), conn)
        assert len(current) == 2

        conn.execute(text('''
            INSERT INTO extraction_records (
                document_id, element_category, key_information, literal_quote,
                cross_reference_notes, review_status, created_by, created_at, updated_by, updated_at
            ) VALUES (
                :document_id, 'Hardware', 'Computador central de prueba', 'cita de prueba',
                'control cruzado', 'Pendiente', 'admin', :now, 'admin', :now
            )
        '''), {"document_id": document_id, "now": utc_now()})
        conn.commit()

        search_title = global_search(conn, "Tarjeta histórica")
        assert len(search_title["documents"]) == 1
        search_source = global_search(conn, "Archivo Nacional")
        assert len(search_source["documents"]) == 1
        search_prueba = global_search(conn, "prueba")
        assert len(search_prueba["documents"]) == 1
        assert len(search_prueba["extractions"]) == 1
        search_tarjeta = global_search(conn, "Tarjeta")
        assert any(row["table_name"] == table_name for row in search_tarjeta["tables"])
        assert isinstance(global_search(conn, "%"), dict)
        assert isinstance(global_search(conn, "_"), dict)
        assert isinstance(global_search(conn, "\\"), dict)

        snapshot_a = current.copy()
        snapshot_b = current.copy()
        edited_a = snapshot_a.copy()
        edited_a.loc[0, "nombre"] = "Edición A"
        assert sync_table_edits(conn, table_name, snapshot_a, edited_a, "inv1")["updated"] == 1

        edited_b = snapshot_b.copy()
        edited_b.loc[0, "nombre"] = "Edición B"
        try:
            sync_table_edits(conn, table_name, snapshot_b, edited_b, "inv2")
            raise AssertionError("Debió detectar conflicto concurrente")
        except ConcurrentEditError:
            pass

        personas = create_dynamic_table(
            conn,
            "Personas",
            [
                {"name": "Nombre", "type": "Texto", "foreign_key_table": None},
                {"name": "Mencion", "type": "Documento", "foreign_key_table": "document_records"},
            ],
            "Personas mencionadas",
            "admin",
        )
        assert personas == "personas"
        personas_metadata = get_dynamic_column_metadata(conn, personas)
        assert [row["column_name"] for row in personas_metadata] == ["nombre", "mencion"]
        persona_id = insert_record(conn, personas, {
            "Nombre": "Juan",
            "nombre": None,
            "Mencion": None,
            "mencion": document_id,
        }, "admin")
        assert persona_id == 1

        conn.execute(text('''
            INSERT INTO dynamic_table_columns (
                table_name, column_name, column_type, ordinal_position, foreign_key_table
            ) VALUES ('personas', 'Nombre', 'Texto', 0, NULL)
        '''))
        conn.execute(text('''
            INSERT INTO dynamic_table_columns (
                table_name, column_name, column_type, ordinal_position, foreign_key_table
            ) VALUES ('personas', 'Mencion', 'Documento', 1, 'document_records')
        '''))
        conn.commit()
        init_db()
        repaired = get_dynamic_column_metadata(conn, personas)
        assert [row["column_name"] for row in repaired] == ["nombre", "mencion"]
        assert [row["column_type"] for row in repaired] == ["Texto", "Documento"]
        preserved_person = conn.execute(text("SELECT nombre, mencion FROM personas WHERE record_id = 1")).mappings().one()
        assert preserved_person["nombre"] == "Juan"
        assert int(preserved_person["mencion"]) == document_id
        drop_dynamic_table(conn, personas, "admin")

        child_table = create_dynamic_table(
            conn,
            "hardware_notes",
            [{"name": "hardware_id", "type": "Llave foránea", "foreign_key_table": table_name}],
            "Notas",
            "admin",
        )
        try:
            drop_dynamic_table(conn, table_name, "admin")
            raise AssertionError("Debió bloquear una tabla referenciada")
        except ValueError as exc:
            assert child_table in str(exc)

        drop_dynamic_table(conn, child_table, "admin")

        entries = fetch_audit_log(conn, table_filter=table_name)
        actions = [entry["action"] for entry in entries]
        assert "CREATE_TABLE" in actions and "INSERT" in actions and "UPDATE" in actions

        drop_dynamic_table(conn, table_name, "admin")
        assert table_name not in list_user_tables(conn)

    assert safe_pdf_filename(12, "Caja Ñ / Documento 1") == "12_Caja_N_Documento_1_OCR.pdf"
    assert document_text_snippets("uno dos tres prueba cinco", "prueba")
