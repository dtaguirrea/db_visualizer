"""Carga, OCR y reprocesamiento de documentos."""
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st
from sqlalchemy import text

import config
from src.audit import log_action
from src.database import get_connection, utc_now
from src.ocr_client import OcrServiceError, create_searchable_pdf, extract_text_from_pdf
from src.documents import safe_pdf_filename
from src.table_relations import document_viewer_url

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Documentos")
st.caption("Carga de PDF y generación automática de una copia buscable con OCR.")


def _run_ocr(original_path: str, ocr_path: str) -> tuple[str | None, str, str, str | None]:
    """Procesa el PDF preservando el OCR aunque falle la indexación de texto."""
    searchable_pdf = create_searchable_pdf(original_path, ocr_path)
    try:
        indexed_text = extract_text_from_pdf(searchable_pdf)
        return searchable_pdf, indexed_text, "completado", None
    except OcrServiceError as exc:
        return searchable_pdf, "", "parcial", str(exc)


with st.form("document_upload_form", clear_on_submit=True):
    col1, col2 = st.columns([2, 1])
    with col1:
        source_reference = st.text_input("Origen o referencia")
        uploaded_pdf = st.file_uploader("Archivo PDF", type=["pdf"])
    with col2:
        original_format = st.selectbox(
            "Formato de origen",
            ["PDF digitalizado", "PDF nativo", "Microfilm digitalizado", "Escaneo de baja calidad"],
        )
    submitted = st.form_submit_button("Cargar y procesar", type="primary")

if submitted:
    if not source_reference.strip() or uploaded_pdf is None:
        st.error("Ingresa una referencia y selecciona un PDF.")
    else:
        token = uuid4().hex
        original_path = config.UPLOAD_DIR / f"{token}_original.pdf"
        ocr_path = config.UPLOAD_DIR / f"{token}_ocr.pdf"
        original_path.write_bytes(uploaded_pdf.getbuffer())

        ocr_status = "error"
        ocr_text = ""
        ocr_file_path = None
        ocr_error = None
        with st.status("Procesando documento", expanded=True) as status:
            try:
                st.write("Generando PDF con capa de texto...")
                ocr_file_path, ocr_text, ocr_status, ocr_error = _run_ocr(
                    str(original_path), str(ocr_path)
                )
                if ocr_status == "completado":
                    status.update(label="Documento procesado", state="complete", expanded=False)
                else:
                    status.update(
                        label="PDF OCR generado; indexación pendiente",
                        state="complete",
                        expanded=True,
                    )
                    st.warning(
                        "El PDF con OCR fue generado, pero el texto para búsqueda no pudo indexarse. "
                        "Puedes reprocesarlo más tarde."
                    )
            except OcrServiceError as exc:
                ocr_error = str(exc)
                status.update(label="El OCR no pudo completarse", state="error", expanded=True)
                st.error(ocr_error)

        visible_path = ocr_file_path or str(original_path)
        with get_connection() as conn:
            try:
                now = utc_now()
                result = conn.execute(text('''
                    INSERT INTO document_records (
                        source_reference, original_format, file_path, original_file_path,
                        ocr_file_path, ocr_text, ocr_status, ocr_error,
                        created_by, created_at, updated_by, updated_at
                    ) VALUES (
                        :source_reference, :original_format, :file_path, :original_file_path,
                        :ocr_file_path, :ocr_text, :ocr_status, :ocr_error,
                        :created_by, :created_at, :updated_by, :updated_at
                    ) RETURNING document_id
                '''), {
                    "source_reference": source_reference.strip(),
                    "original_format": original_format,
                    "file_path": visible_path,
                    "original_file_path": str(original_path),
                    "ocr_file_path": ocr_file_path,
                    "ocr_text": ocr_text,
                    "ocr_status": ocr_status,
                    "ocr_error": ocr_error,
                    "created_by": st.session_state.username,
                    "created_at": now,
                    "updated_by": st.session_state.username,
                    "updated_at": now,
                })
                document_id = result.scalar_one()
                log_action(conn, "document_records", document_id, "INSERT", st.session_state.username,
                           details={"ocr_status": ocr_status})
                conn.commit()
                st.success(f"Documento {document_id} guardado.")
                if ocr_file_path:
                    with open(ocr_file_path, "rb") as pdf_file:
                        st.download_button(
                            "Descargar PDF con OCR",
                            data=pdf_file,
                            file_name=safe_pdf_filename(document_id, source_reference, ocr=True),
                            mime="application/pdf",
                            type="primary",
                            key=f"upload_download_ocr_{document_id}",
                            on_click="ignore",
                        )
                    st.pdf(ocr_file_path, height=650, key=f"upload_pdf_{document_id}")
            except Exception as exc:
                conn.rollback()
                for file_path in (original_path, ocr_path):
                    try:
                        Path(file_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                st.error(f"No fue posible guardar el documento en la base de datos: {exc}")

st.divider()
st.subheader("Documentos cargados")
with get_connection() as conn:
    documents = pd.read_sql_query(text('''
        SELECT document_id, source_reference, original_format, ocr_status, ocr_error,
               original_file_path, ocr_file_path, created_by, created_at
        FROM document_records
        ORDER BY document_id DESC
    '''), conn)

if documents.empty:
    st.info("No hay documentos cargados.")
else:
    display = documents[["document_id", "source_reference", "original_format", "ocr_status", "created_by", "created_at"]].copy()
    display["Abrir"] = display["document_id"].map(document_viewer_url)
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Abrir": st.column_config.LinkColumn("Abrir", display_text="Abrir documento"),
        },
    )

    retry_candidates = documents[documents["ocr_status"] != "completado"]
    if not retry_candidates.empty:
        labels = {
            f"{int(row.document_id)} · {row.source_reference}": int(row.document_id)
            for row in retry_candidates.itertuples()
        }
        with st.expander("Reprocesar OCR"):
            selected = st.selectbox("Documento", list(labels.keys()))
            if st.button("Reprocesar", type="primary"):
                document_id = labels[selected]
                row = retry_candidates[retry_candidates["document_id"] == document_id].iloc[0]
                original_path = row["original_file_path"]
                if not original_path or not Path(original_path).exists():
                    st.error("No se encuentra el archivo original en el servidor.")
                else:
                    ocr_path = str(
                        Path(original_path).with_name(
                            Path(original_path).stem.replace("_original", "") + "_ocr.pdf"
                        )
                    )
                    try:
                        with st.spinner("Procesando OCR..."):
                            ocr_file_path, ocr_text, ocr_status, ocr_error = _run_ocr(
                                original_path, ocr_path
                            )
                        with get_connection() as conn:
                            now = utc_now()
                            conn.execute(text('''
                                UPDATE document_records
                                SET file_path = :file_path, ocr_file_path = :ocr_file_path,
                                    ocr_text = :ocr_text, ocr_status = :ocr_status,
                                    ocr_error = :ocr_error,
                                    updated_by = :updated_by, updated_at = :updated_at
                                WHERE document_id = :document_id
                            '''), {
                                "file_path": ocr_file_path,
                                "ocr_file_path": ocr_file_path,
                                "ocr_text": ocr_text,
                                "ocr_status": ocr_status,
                                "ocr_error": ocr_error,
                                "updated_by": st.session_state.username,
                                "updated_at": now,
                                "document_id": document_id,
                            })
                            log_action(
                                conn,
                                "document_records",
                                document_id,
                                "REPROCESS_OCR",
                                st.session_state.username,
                                details={"ocr_status": ocr_status},
                            )
                            conn.commit()
                        if ocr_status == "completado":
                            st.success("OCR e indexación completados.")
                        else:
                            st.warning("El PDF OCR fue generado, pero la indexación de texto sigue pendiente.")
                        st.rerun()
                    except OcrServiceError as exc:
                        with get_connection() as conn:
                            conn.execute(text('''
                                UPDATE document_records
                                SET ocr_status = 'error', ocr_error = :ocr_error,
                                    updated_by = :updated_by, updated_at = :updated_at
                                WHERE document_id = :document_id
                            '''), {
                                "ocr_error": str(exc),
                                "updated_by": st.session_state.username,
                                "updated_at": utc_now(),
                                "document_id": document_id,
                            })
                            conn.commit()
                        st.error(str(exc))
