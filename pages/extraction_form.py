"""Extracción estructurada con visor del PDF OCRizado."""
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

import config
from src.audit import log_action
from src.database import get_connection, utc_now
from src.documents import safe_pdf_filename

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Extracción")
st.caption("El visor utiliza la copia OCR del documento cuando está disponible.")

with get_connection() as conn:
    documents_df = pd.read_sql_query(text('''
        SELECT document_id, source_reference, original_file_path, ocr_file_path,
               file_path, ocr_text, ocr_status
        FROM document_records
        ORDER BY document_id DESC
    '''), conn)

if documents_df.empty:
    st.info("Carga un documento antes de iniciar una extracción.")
    st.stop()

options = {
    f"{int(row.document_id)} · {row.source_reference}": int(row.document_id)
    for row in documents_df.itertuples()
}
selected_label = st.selectbox("Documento", list(options.keys()))
selected_document_id = options[selected_label]
selected = documents_df[documents_df["document_id"] == selected_document_id].iloc[0]

col_pdf, col_form = st.columns([1.2, 1], gap="large")

with col_pdf:
    st.subheader("Documento")
    pdf_path = selected["ocr_file_path"] or selected["file_path"] or selected["original_file_path"]
    if pdf_path and Path(pdf_path).exists():
        if selected["ocr_file_path"] and Path(selected["ocr_file_path"]).is_file():
            with open(selected["ocr_file_path"], "rb") as pdf_file:
                st.download_button(
                    "Descargar PDF con OCR",
                    data=pdf_file,
                    file_name=safe_pdf_filename(
                        selected_document_id, selected["source_reference"], ocr=True
                    ),
                    mime="application/pdf",
                    key=f"extraction_download_ocr_{selected_document_id}",
                    on_click="ignore",
                    use_container_width=True,
                )
        st.pdf(pdf_path, height=820, key=f"extraction_pdf_{selected_document_id}")
        if selected["ocr_file_path"] and selected["ocr_status"] == "parcial":
            st.warning("El PDF OCR está disponible, pero su texto aún no está completamente indexado.")
        elif not selected["ocr_file_path"]:
            st.warning("Este documento aún no tiene una copia OCR disponible.")
    else:
        st.error("No se encuentra el archivo PDF en el servidor.")

with col_form:
    st.subheader("Ficha")
    with st.form("extraction_form", clear_on_submit=True):
        element_category = st.selectbox("Categoría", options=config.ELEMENT_CATEGORIES)
        key_information = st.text_area("Información clave", height=180)
        literal_quote = st.text_area("Cita literal", height=150)
        cross_reference_notes = st.text_input("Cruce o control")
        review_status = st.selectbox("Estado", ["Pendiente", "Revisado", "Validado"])
        submitted = st.form_submit_button("Guardar extracción", type="primary", use_container_width=True)

    if submitted:
        if not key_information.strip():
            st.error("La información clave no puede quedar vacía.")
        else:
            with get_connection() as conn:
                try:
                    now = utc_now()
                    result = conn.execute(text('''
                        INSERT INTO extraction_records (
                            document_id, element_category, key_information, literal_quote,
                            cross_reference_notes, review_status, created_by, created_at,
                            updated_by, updated_at
                        ) VALUES (
                            :document_id, :element_category, :key_information, :literal_quote,
                            :cross_reference_notes, :review_status, :created_by, :created_at,
                            :updated_by, :updated_at
                        ) RETURNING extraction_id
                    '''), {
                        "document_id": selected_document_id,
                        "element_category": element_category,
                        "key_information": key_information.strip(),
                        "literal_quote": literal_quote.strip(),
                        "cross_reference_notes": cross_reference_notes.strip(),
                        "review_status": review_status,
                        "created_by": st.session_state.username,
                        "created_at": now,
                        "updated_by": st.session_state.username,
                        "updated_at": now,
                    })
                    extraction_id = result.scalar_one()
                    log_action(conn, "extraction_records", extraction_id, "INSERT", st.session_state.username)
                    conn.commit()
                    st.success("Extracción guardada.")
                except Exception:
                    conn.rollback()
                    raise

    with st.expander("Texto OCR"):
        st.text_area("Texto indexado", selected["ocr_text"] or "", height=300, label_visibility="collapsed")
