"""Visor de documentos con selección directa, descarga y búsqueda OCR."""
import html
import re
from pathlib import Path

import streamlit as st
from sqlalchemy import text

import config
from src.database import get_connection
from src.documents import (
    document_display_label,
    document_text_snippets,
    get_ocr_pdf_path,
    get_visible_pdf_path,
    safe_pdf_filename,
)
from src.table_relations import extraction_workspace_url

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Visor de documentos")

with get_connection() as conn:
    rows = conn.execute(text('''
        SELECT document_id, title, author, source_reference, document_date, original_format,
               ocr_language, ocr_status, ocr_error, original_file_path, ocr_file_path,
               file_path, ocr_text, created_by, created_at, updated_by, updated_at
        FROM document_records
        ORDER BY document_id DESC
    ''')).mappings().all()

documents = [dict(row) for row in rows]
if not documents:
    st.info("Todavía no hay documentos cargados.")
    st.stop()

document_by_id = {int(document["document_id"]): document for document in documents}
document_ids = list(document_by_id)
raw_document_id = st.query_params.get("document_id")
try:
    requested_id = int(raw_document_id) if raw_document_id is not None else None
except (TypeError, ValueError):
    requested_id = None

selection_key = "viewer_document_selection"
last_query_key = "viewer_last_query_document_id"
if requested_id in document_by_id and st.session_state.get(last_query_key) != requested_id:
    st.session_state[selection_key] = requested_id
    st.session_state[last_query_key] = requested_id
elif st.session_state.get(selection_key) not in document_by_id:
    st.session_state[selection_key] = document_ids[0]

document_id = st.selectbox(
    "Documento que deseas visualizar",
    options=document_ids,
    key=selection_key,
    format_func=lambda value: document_display_label(document_by_id[value]),
)
if requested_id != document_id:
    st.query_params["document_id"] = str(document_id)
    st.session_state[last_query_key] = document_id
st.session_state.viewer_document_id = document_id
document = document_by_id[document_id]

st.subheader(document_display_label(document))
metadata_parts = []
if document.get("author"):
    metadata_parts.append(f"Autor: {document['author']}")
if document.get("source_reference"):
    metadata_parts.append(f"Origen: {document['source_reference']}")
if document.get("document_date"):
    metadata_parts.append(f"Fecha: {document['document_date']}")
metadata_parts.extend([
    document.get("original_format") or "Formato no indicado",
    f"Idioma OCR: {config.OCR_LANGUAGE_LABELS.get(document.get('ocr_language'), document.get('ocr_language'))}",
    f"OCR: {document['ocr_status']}",
    f"Cargado por {document['created_by']}",
])
st.caption(" · ".join(metadata_parts))

pdf_path = get_visible_pdf_path(document)
if not pdf_path or not Path(pdf_path).is_file():
    st.error("El archivo PDF no está disponible en el servidor.")
    st.stop()

ocr_pdf_path = get_ocr_pdf_path(document)
col_download, col_original, col_extract, _ = st.columns([1.35, 1.15, 1.3, 3])

with col_download:
    if ocr_pdf_path:
        with open(ocr_pdf_path, "rb") as pdf_file:
            st.download_button(
                "Descargar PDF con OCR",
                data=pdf_file,
                file_name=safe_pdf_filename(
                    document["document_id"],
                    document.get("title") or document.get("source_reference"),
                    ocr=True,
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
                    document["document_id"],
                    document.get("title") or document.get("source_reference"),
                    ocr=False,
                ),
                mime="application/pdf",
                use_container_width=True,
                key=f"download_original_{document_id}",
                on_click="ignore",
            )

with col_extract:
    st.link_button(
        "Crear ficha de extracción",
        extraction_workspace_url(document_id),
        use_container_width=True,
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
            st.info("Este PDF no contiene texto indexado para la búsqueda interna.")

st.pdf(pdf_path, height=900, key=f"pdf_viewer_{document_id}")
