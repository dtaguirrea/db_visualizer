import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

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
from pypdf import PdfWriter
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
from src.document_service import (
    DocumentBusyError,
    InvalidDocumentDate,
    QueueFullError,
    claim_next_document,
    delete_document,
    enqueue_documents,
    get_queue_counts,
    normalize_document_date,
    process_next_document,
    queue_snapshot,
    recover_interrupted_jobs,
    requeue_document,
    update_document_metadata,
)
from src.dynamic_tables import InvalidIdentifierError, create_dynamic_table, drop_dynamic_table, validate_identifier
from src.ocr_client import extract_text_locally
from src.repository import ConcurrentEditError, delete_records, insert_record, sync_table_edits
from src.search import global_search
from src.search import search_documents
from src.table_relations import (
    document_viewer_url,
    extraction_workspace_url,
    get_foreign_key_choices,
)


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
        assert extraction_workspace_url(document_id) == f"/extraccion?document_id={document_id}"

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


def test_v2_document_queue_and_crud(monkeypatch):
    init_db()
    with get_connection() as conn:
        conn.execute(text(
            "UPDATE document_records SET ocr_status = 'error' "
            "WHERE ocr_status IN ('en_cola', 'procesando', 'pendiente')"
        ))
        conn.commit()

    monkeypatch.setattr(config, "OCR_QUEUE_MAX_SIZE", 2)
    first_original = Path(config.UPLOAD_DIR) / "queue_first_original.pdf"
    second_original = Path(config.UPLOAD_DIR) / "queue_second_original.pdf"
    third_original = Path(config.UPLOAD_DIR) / "queue_third_original.pdf"
    for path in (first_original, second_original, third_original):
        path.write_bytes(b"%PDF-1.4\n%%EOF")

    document_ids = enqueue_documents([
        {
            "title": "Documento de 1800",
            "source_reference": "Archivo A",
            "document_date": "1800-01-01",
            "original_format": "PDF digitalizado",
            "original_file_path": str(first_original),
        },
        {
            "title": "Documento reciente",
            "source_reference": "Archivo B",
            "document_date": date.today(),
            "original_format": "PDF nativo",
            "ocr_language": "en",
            "original_file_path": str(second_original),
        },
    ], "admin")
    assert len(document_ids) == 2
    assert get_queue_counts()["disponibles"] == 0
    assert [row["document_id"] for row in queue_snapshot()] == document_ids

    try:
        enqueue_documents([{
            "title": "Sin espacio",
            "source_reference": None,
            "document_date": None,
            "original_format": "PDF nativo",
            "original_file_path": str(third_original),
        }], "admin")
        raise AssertionError("Debió rechazar una cola completa")
    except QueueFullError:
        pass

    received_languages = []

    def fake_create_searchable_pdf(input_path, output_path, language=None):
        received_languages.append(language)
        Path(output_path).write_bytes(Path(input_path).read_bytes())
        return output_path

    monkeypatch.setattr(
        "src.document_service.create_searchable_pdf", fake_create_searchable_pdf
    )
    monkeypatch.setattr(config, "OCR_TEXT_EXTRACTION_MODE", "local")
    monkeypatch.setattr("src.document_service.extract_text_locally", lambda _path: "texto OCR indexado")
    assert process_next_document() is True
    with get_connection() as conn:
        first = conn.execute(text('''
            SELECT ocr_status, ocr_text, ocr_attempts, updated_at, ocr_file_path
            FROM document_records WHERE document_id = :document_id
        '''), {"document_id": document_ids[0]}).mappings().one()
    assert first["ocr_status"] == "completado"
    assert first["ocr_text"] == "texto OCR indexado"
    assert first["ocr_attempts"] == 1
    assert Path(first["ocr_file_path"]).is_file()

    def fail_indexing(_path):
        raise RuntimeError("indexación no disponible")

    monkeypatch.setattr("src.document_service.extract_text_locally", fail_indexing)
    assert process_next_document() is True
    with get_connection() as conn:
        second = conn.execute(text('''
            SELECT ocr_status, ocr_error, ocr_file_path
            FROM document_records WHERE document_id = :document_id
        '''), {"document_id": document_ids[1]}).mappings().one()
    assert second["ocr_status"] == "parcial"
    assert "indexación no disponible" in second["ocr_error"]
    assert Path(second["ocr_file_path"]).is_file()
    assert process_next_document() is False
    assert received_languages == [config.OCR_LANGUAGE, "en"]

    update_document_metadata(
        document_ids[0],
        title="Documento histórico editado",
        source_reference="Archivo corregido",
        document_date="1800-12-31",
        original_format="Microfilm digitalizado",
        ocr_language="fr",
        username="admin",
        expected_updated_at=first["updated_at"],
    )
    with get_connection() as conn:
        updated = conn.execute(text('''
            SELECT title, document_date, ocr_language
            FROM document_records WHERE document_id = :document_id
        '''), {"document_id": document_ids[0]}).mappings().one()
        extraction_id = conn.execute(text('''
            INSERT INTO extraction_records (
                document_id, review_status, created_by, created_at, updated_by, updated_at
            ) VALUES (
                :document_id, 'Pendiente', 'admin', :now, 'admin', :now
            ) RETURNING extraction_id
        '''), {"document_id": document_ids[0], "now": utc_now()}).scalar_one()
        conn.commit()
    assert updated["title"] == "Documento histórico editado"
    assert updated["document_date"] == "1800-12-31"
    assert updated["ocr_language"] == "fr"

    try:
        normalize_document_date("1799-12-31")
        raise AssertionError("Debió rechazar una fecha anterior a 1800")
    except InvalidDocumentDate:
        pass
    try:
        normalize_document_date(date.today() + timedelta(days=1))
        raise AssertionError("Debió rechazar una fecha futura")
    except InvalidDocumentDate:
        pass

    first_paths = [first_original, Path(first["ocr_file_path"])]
    delete_document(document_ids[0], "admin")
    assert all(not path.exists() for path in first_paths)
    with get_connection() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM document_records WHERE document_id = :document_id"
        ), {"document_id": document_ids[0]}).scalar_one() == 0
        assert conn.execute(text(
            "SELECT COUNT(*) FROM extraction_records WHERE extraction_id = :extraction_id"
        ), {"extraction_id": extraction_id}).scalar_one() == 0

    requeue_document(document_ids[1], "admin")
    claimed = claim_next_document()
    assert claimed["document_id"] == document_ids[1]
    try:
        delete_document(document_ids[1], "admin")
        raise AssertionError("Debió impedir eliminar un documento en procesamiento")
    except DocumentBusyError:
        pass
    assert recover_interrupted_jobs() == 1
    second_paths = [second_original, Path(second["ocr_file_path"])]
    delete_document(document_ids[1], "admin")
    assert all(not path.exists() for path in second_paths)
    third_original.unlink(missing_ok=True)


def test_v2_1_explicit_record_deletion():
    init_db()
    with get_connection() as conn:
        table_name = create_dynamic_table(
            conn,
            "delete_rows_test",
            [{"name": "nombre", "type": "Texto", "foreign_key_table": None}],
            "Prueba de eliminación explícita",
            "admin",
        )
        first_id = insert_record(conn, table_name, {"nombre": "Conservar"}, "admin")
        second_id = insert_record(conn, table_name, {"nombre": "Eliminar"}, "admin")
        row = conn.execute(text(f'''
            SELECT record_id, updated_at FROM {table_name} WHERE record_id = :record_id
        '''), {"record_id": second_id}).mappings().one()
        assert delete_records(conn, table_name, [{
            "pk": row["record_id"], "updated_at": row["updated_at"]
        }], "admin") == 1
        remaining = conn.execute(text(
            f"SELECT record_id FROM {table_name} ORDER BY record_id"
        )).scalars().all()
        assert remaining == [first_id]
        drop_dynamic_table(conn, table_name, "admin")


def test_v2_1_local_pdf_text_extraction():
    pdf_path = Path(config.UPLOAD_DIR) / "blank_text_layer_test.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with pdf_path.open("wb") as output:
        writer.write(output)
    assert extract_text_locally(str(pdf_path)) == ""
    pdf_path.unlink(missing_ok=True)


def test_v2_2_author_and_date_filters():
    init_db()
    ids = enqueue_documents([
        {"title": "Historia", "author": "Maria Silva", "document_date": "1800-01-01",
         "original_file_path": "/tmp/author_fixture.pdf"},
        {"title": "Historia", "author": None, "document_date": "1900-01-01",
         "original_file_path": "/tmp/anonymous_fixture.pdf"},
    ], "admin")
    with get_connection() as conn:
        assert [r["document_id"] for r in search_documents(conn, "", author="silva")] == [ids[0]]
        assert [r["document_id"] for r in search_documents(conn, "Maria")] == [ids[0]]
        assert [r["document_id"] for r in search_documents(
            conn, "", date_from="1800-01-01", date_to="1800-01-01")] == [ids[0]]
        assert search_documents(conn, "", author="silva", date_from="1900-01-01") == []
        assert search_documents(conn, "", author="%") == []
        assert search_documents(conn, "no coincide", author="silva") == []
        assert search_documents(conn, "") == []
        import pytest
        with pytest.raises(ValueError):
            search_documents(conn, "", date_from="1900-01-01", date_to="1800-01-01")
        row = conn.execute(text("SELECT * FROM document_records WHERE document_id=:id"),
                           {"id": ids[0]}).mappings().one()
    update_document_metadata(ids[0], title=row["title"], source_reference=None,
        document_date=row["document_date"], original_format=None, ocr_language="pt",
        author="Joao", username="admin", expected_updated_at=row["updated_at"])
    init_db()
    with get_connection() as conn:
        assert search_documents(conn, "", author="Joao")[0]["document_id"] == ids[0]
        assert conn.execute(text("SELECT author FROM document_records WHERE document_id=:id"),
                            {"id": ids[1]}).scalar_one() is None


def test_v2_2_portuguese_request_and_error(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.ocr_client import create_searchable_pdf, OcrServiceError
    import pytest
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4")
    received = []
    def post(url, **kwargs):
        received.append(kwargs["data"])
        return SimpleNamespace(status_code=200, content=b"%PDF-1.4", text="")
    monkeypatch.setattr("src.ocr_client.requests.post", post)
    create_searchable_pdf(str(source), str(tmp_path / "output.pdf"), language="pt")
    assert received[0]["language"] == "pt"
    monkeypatch.setattr("src.ocr_client.requests.post", lambda *a, **kw:
        SimpleNamespace(status_code=500, text="missing language data"))
    with pytest.raises(OcrServiceError, match="tesseract --list-langs"):
        create_searchable_pdf(str(source), str(tmp_path / "error.pdf"), language="pt")
    assert not (tmp_path / "error.pdf").exists()


def test_v2_2_ui_dates_and_author(tmp_path):
    from streamlit.testing.v1 import AppTest
    init_db()
    pdf = tmp_path / "ui.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with pdf.open("wb") as output:
        writer.write(output)
    with get_connection() as conn:
        conn.execute(text("UPDATE document_records SET original_file_path=:path, file_path=:path"),
                     {"path": str(pdf)})
        conn.commit()
        table = create_dynamic_table(conn, "historical_dates",
            [{"name": "fecha_autoria", "type": "Fecha", "foreign_key_table": None}],
            "Fechas históricas", "admin")
        insert_record(conn, table, {"fecha_autoria": "1800-01-01"}, "admin")
    for page in ("document_upload", "document_viewer", "data_explorer", "search_engine"):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages" / f"{page}.py"))
        app.session_state["logged_in"] = True
        app.session_state["username"] = "admin"
        if page == "data_explorer":
            app.query_params["table"] = table
        app.run(timeout=20)
        assert not app.exception, str(app.exception)
        if page == "document_upload":
            assert all(item.min == date(1800, 1, 1) for item in app.date_input)
            app.date_input[0].set_value(date(1800, 1, 1)).run()
            assert not app.exception
        if page == "search_engine":
            app.text_input[1].set_value("Joao")
            app.button[0].click().run()
            assert not app.exception
            assert any("Autor: Joao" in item.value for item in app.caption)
