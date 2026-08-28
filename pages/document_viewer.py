"""Visor independiente de documentos, con descarga y búsqueda OCR."""
import html
import re
from pathlib import Path

import streamlit as st
from sqlalchemy import text

from src.database import get_connection
from src.documents import (
    document_display_label,
    document_text_snippets,
    get_ocr_pdf_path,
    get_visible_pdf_path,
    safe_pdf_filename,
)

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Visor de documento")

raw_document_id = st.query_params.get("document_id")
if raw_document_id is not None:
    st.session_state.viewer_document_id = raw_document_id
else:
    raw_document_id = st.session_state.get("viewer_document_id")
try:
    document_id = int(raw_document_id) if raw_document_id is not None else None
except (TypeError, ValueError):
    document_id = None

if document_id is None:
    st.info("Selecciona un documento desde una tabla, el buscador o la sección Documentos.")
    st.stop()

with get_connection() as conn:
    row = conn.execute(text('''
        SELECT document_id, title, source_reference, document_date, original_format, ocr_status, ocr_error,
               original_file_path, ocr_file_path, file_path, ocr_text,
               created_by, created_at, updated_by, updated_at
        FROM document_records
        WHERE document_id = :document_id
    '''), {"document_id": document_id}).mappings().first()

document = dict(row) if row is not None else None
if document is None:
    st.error("El documento solicitado no existe.")
    st.stop()

st.subheader(document_display_label(document))
metadata_parts = []
if document.get("source_reference"):
    metadata_parts.append(f"Origen: {document['source_reference']}")
if document.get("document_date"):
    metadata_parts.append(f"Fecha: {document['document_date']}")
metadata_parts.extend([
    document.get("original_format") or "Formato no indicado",
    f"OCR: {document['ocr_status']}",
    f"Cargado por {document['created_by']}",
])
st.caption(" · ".join(metadata_parts))

pdf_path = get_visible_pdf_path(document)
if not pdf_path or not Path(pdf_path).is_file():
    st.error("El archivo PDF no está disponible en el servidor.")
    st.stop()

ocr_pdf_path = get_ocr_pdf_path(document)
col_download, col_original, _ = st.columns([1.35, 1.15, 4])

with col_download:
    if ocr_pdf_path:
        with open(ocr_pdf_path, "rb") as pdf_file:
            st.download_button(
                "Descargar PDF con OCR",
                data=pdf_file,
                file_name=safe_pdf_filename(
                    document["document_id"], document.get("title") or document.get("source_reference"), ocr=True
                ),
                mime="application/pdf",
                type="primary",
                use_container_width=True,
                key=f"download_ocr_{document_id}",
                on_click="ignore",
            )
    else:
        st.button(
            "PDF con OCR no disponible",
            disabled=True,
            use_container_width=True,
            key=f"download_ocr_disabled_{document_id}",
        )

with col_original:
    original_path = document.get("original_file_path")
    if original_path and Path(original_path).is_file() and original_path != ocr_pdf_path:
        with open(original_path, "rb") as original_file:
            st.download_button(
                "Descargar original",
                data=original_file,
                file_name=safe_pdf_filename(
                    document["document_id"], document.get("title") or document.get("source_reference"), ocr=False
                ),
                mime="application/pdf",
                use_container_width=True,
                key=f"download_original_{document_id}",
                on_click="ignore",
            )

if ocr_pdf_path:
    st.caption("Se muestra la copia PDF con capa de texto OCR.")
elif document["ocr_status"] == "error":
    st.warning("El OCR no está disponible para este documento. Se muestra el archivo original.")
else:
    st.warning("Este documento todavía no tiene una copia OCR disponible.")

with st.expander("Buscar en este documento", expanded=False):
    local_query = st.text_input(
        "Texto",
        placeholder="Palabra o frase",
        key=f"document_search_{document_id}",
    )
    if local_query.strip():
        snippets = document_text_snippets(document.get("ocr_text"), local_query, limit=20)
        if snippets:
            st.caption(f"{len(snippets)} fragmento(s) mostrado(s)")
            terms = [local_query.strip()] + [
                term for term in re.findall(r"\w+", local_query, flags=re.UNICODE)
                if len(term) >= 2
            ]
            pattern = re.compile(
                "(" + "|".join(re.escape(term) for term in dict.fromkeys(terms)) + ")",
                flags=re.IGNORECASE,
            )
            for snippet in snippets:
                parts = pattern.split(snippet)
                rendered = "".join(
                    f"<mark>{html.escape(part)}</mark>" if index % 2 else html.escape(part)
                    for index, part in enumerate(parts)
                )
                st.markdown(rendered, unsafe_allow_html=True)
        elif document.get("ocr_text"):
            st.info("No se encontraron coincidencias en el texto indexado.")
        else:
            st.info(
                "Este PDF puede tener capa OCR, pero todavía no hay texto indexado para la búsqueda interna. "
                "Reprocesa la indexación desde Documentos."
            )

st.pdf(pdf_path, height=900, key=f"pdf_viewer_{document_id}")
