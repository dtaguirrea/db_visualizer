"""Carga en cola y administración de documentos."""
from datetime import date
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st
from sqlalchemy import text

import config
from src.database import get_connection
from src.document_service import (
    ConcurrentDocumentEditError,
    DocumentServiceError,
    InvalidDocumentDate,
    QueueFullError,
    delete_document,
    enqueue_documents,
    get_queue_counts,
    queue_snapshot,
    requeue_document,
    update_document_metadata,
)
from src.documents import document_display_label
from src.table_relations import document_viewer_url

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Documentos")
st.caption(
    "Carga hasta 20 PDF. Los documentos se guardan inmediatamente y un trabajador OCR "
    "los procesa de a uno, en el orden de llegada."
)

ORIGINAL_FORMATS = [
    "PDF digitalizado",
    "PDF nativo",
    "Microfilm digitalizado",
    "Escaneo de baja calidad",
]
STATUS_LABELS = {
    "en_cola": "En cola",
    "procesando": "Procesando",
    "completado": "Completado",
    "parcial": "Parcial",
    "error": "Error",
    "pendiente": "Pendiente",
}


def _text_or_empty(value) -> str:
    return "" if value is None or pd.isna(value) else str(value)


@st.fragment(run_every="5s")
def queue_panel() -> None:
    counts = get_queue_counts()
    col1, col2, col3 = st.columns(3)
    col1.metric("Procesando", counts["procesando"])
    col2.metric("En espera", counts["en_cola"])
    col3.metric("Espacios disponibles", counts["disponibles"])

    active_jobs = queue_snapshot()
    if not active_jobs:
        st.caption("La cola OCR está vacía.")
        return
    queue_df = pd.DataFrame(active_jobs)[[
        "document_id", "title", "source_reference", "ocr_language", "ocr_status",
        "queue_position", "ocr_attempts", "created_by",
    ]]
    queue_df.columns = [
        "ID", "Título", "Origen", "Idioma", "Estado", "Posición", "Intentos", "Cargado por"
    ]
    queue_df["Idioma"] = queue_df["Idioma"].map(config.OCR_LANGUAGE_LABELS).fillna(queue_df["Idioma"])
    queue_df["Estado"] = queue_df["Estado"].map(STATUS_LABELS).fillna(queue_df["Estado"])
    st.dataframe(queue_df, use_container_width=True, hide_index=True)


st.subheader("Cola OCR")
queue_panel()

st.subheader("Agregar documentos")
with st.form("document_upload_form", clear_on_submit=True):
    st.caption(
        "El título se usa para una carga individual. En una carga múltiple, cada documento "
        "recibe como título el nombre de su archivo y luego puedes editarlo."
    )
    title = st.text_input("Título o nombre", placeholder="Opcional")
    author = st.text_input("Autor del documento", placeholder="Opcional; persona o institución")
    col1, col2 = st.columns(2)
    with col1:
        source_reference = st.text_input("Origen o procedencia", placeholder="Opcional")
    with col2:
        document_date = st.date_input(
            "Fecha del documento",
            value=None,
            min_value=date(1800, 1, 1),
            max_value=date.today(),
            format="YYYY-MM-DD",
        )

    col3, col4, col5 = st.columns([1, 1, 2])
    with col3:
        original_format = st.selectbox("Formato de origen", ORIGINAL_FORMATS)
    with col4:
        ocr_language = st.selectbox(
            "Idioma del OCR",
            options=list(config.OCR_LANGUAGE_LABELS),
            index=list(config.OCR_LANGUAGE_LABELS).index(config.OCR_LANGUAGE),
            format_func=lambda code: config.OCR_LANGUAGE_LABELS.get(code, code),
        )
    with col5:
        uploaded_pdfs = st.file_uploader(
            "Archivos PDF",
            type=["pdf"],
            accept_multiple_files=True,
            help="Máximo 20 documentos entre los que esperan y el que se está procesando.",
        )

    submitted = st.form_submit_button("Agregar a la cola", type="primary")

if submitted:
    uploaded_pdfs = uploaded_pdfs or []
    if not uploaded_pdfs:
        st.error("Selecciona al menos un archivo PDF.")
    elif len(uploaded_pdfs) > config.OCR_QUEUE_MAX_SIZE:
        st.error(f"Selecciona como máximo {config.OCR_QUEUE_MAX_SIZE} archivos por carga.")
    else:
        saved_paths: list[Path] = []
        records: list[dict] = []
        invalid_files: list[str] = []
        try:
            for uploaded_pdf in uploaded_pdfs:
                file_bytes = bytes(uploaded_pdf.getbuffer())
                if not file_bytes.startswith(b"%PDF-"):
                    invalid_files.append(uploaded_pdf.name)
                    continue
                original_path = config.UPLOAD_DIR / f"{uuid4().hex}_original.pdf"
                original_path.write_bytes(file_bytes)
                saved_paths.append(original_path)
                inferred_title = Path(uploaded_pdf.name).stem.strip() or None
                records.append({
                    "title": (title.strip() or inferred_title) if len(uploaded_pdfs) == 1 else inferred_title,
                    "source_reference": source_reference,
                    "author": author,
                    "document_date": document_date,
                    "original_format": original_format,
                    "ocr_language": ocr_language,
                    "original_file_path": str(original_path),
                })

            if invalid_files:
                raise ValueError(
                    "Los siguientes archivos no tienen una cabecera PDF válida: "
                    + ", ".join(invalid_files)
                )
            document_ids = enqueue_documents(records, st.session_state.username)
            st.success(
                f"{len(document_ids)} documento(s) agregado(s) a la cola: "
                + ", ".join(str(value) for value in document_ids)
            )
        except (QueueFullError, InvalidDocumentDate, ValueError) as exc:
            for path in saved_paths:
                path.unlink(missing_ok=True)
            st.error(str(exc))
        except Exception as exc:
            for path in saved_paths:
                path.unlink(missing_ok=True)
            st.error(f"No fue posible guardar el lote: {exc}")

st.divider()
st.subheader("Documentos cargados")
with get_connection() as conn:
    documents = pd.read_sql_query(text('''
        SELECT document_id, title, author, source_reference, document_date, original_format,
               ocr_language, ocr_status, ocr_error, original_file_path, ocr_file_path,
               created_by, created_at, updated_by, updated_at
        FROM document_records
        ORDER BY document_id DESC
    '''), conn)

if documents.empty:
    st.info("No hay documentos cargados.")
    st.stop()

display = documents[[
    "document_id", "title", "author", "source_reference", "document_date",
    "original_format", "ocr_language", "ocr_status", "created_by", "created_at",
]].copy()
display.columns = [
    "ID", "Título", "Autor", "Origen", "Fecha", "Formato", "Idioma", "OCR", "Creado por", "Creado el",
]
display["Idioma"] = display["Idioma"].map(config.OCR_LANGUAGE_LABELS).fillna(display["Idioma"])
display["OCR"] = display["OCR"].map(STATUS_LABELS).fillna(display["OCR"])
display["Abrir"] = documents["document_id"].map(document_viewer_url)
st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Abrir": st.column_config.LinkColumn("Abrir", display_text="Abrir documento"),
    },
)

labels = {
    document_display_label(row): int(row["document_id"])
    for row in documents.to_dict("records")
}

with st.expander("Editar datos del documento"):
    selected_edit_label = st.selectbox("Documento", list(labels), key="edit_document_select")
    selected_edit_id = labels[selected_edit_label]
    selected_edit = documents[documents["document_id"] == selected_edit_id].iloc[0]
    stored_date = None
    if pd.notna(selected_edit["document_date"]) and str(selected_edit["document_date"]).strip():
        try:
            stored_date = date.fromisoformat(str(selected_edit["document_date"]))
        except ValueError:
            st.warning("La fecha almacenada no es válida. Selecciona una nueva fecha para corregirla.")

    with st.form(f"edit_document_{selected_edit_id}"):
        edit_title = st.text_input("Título o nombre", value=_text_or_empty(selected_edit["title"]))
        edit_author = st.text_input("Autor del documento", value=_text_or_empty(selected_edit["author"]))
        edit_source = st.text_input(
            "Origen o procedencia", value=_text_or_empty(selected_edit["source_reference"])
        )
        edit_date = st.date_input(
            "Fecha del documento",
            value=stored_date,
            min_value=date(1800, 1, 1),
            max_value=date.today(),
            format="YYYY-MM-DD",
        )
        current_format = _text_or_empty(selected_edit["original_format"])
        format_options = list(ORIGINAL_FORMATS)
        if current_format and current_format not in format_options:
            format_options.insert(0, current_format)
        edit_format = st.selectbox(
            "Formato de origen",
            format_options,
            index=format_options.index(current_format) if current_format in format_options else 0,
        )
        current_language = _text_or_empty(selected_edit["ocr_language"]) or config.OCR_LANGUAGE
        language_options = list(config.OCR_LANGUAGE_LABELS)
        if current_language not in language_options:
            language_options.append(current_language)
        edit_ocr_language = st.selectbox(
            "Idioma del OCR",
            language_options,
            index=language_options.index(current_language),
            format_func=lambda code: config.OCR_LANGUAGE_LABELS.get(code, code),
        )
        save_edit = st.form_submit_button("Guardar cambios", type="primary")

    if save_edit:
        try:
            update_document_metadata(
                selected_edit_id,
                title=edit_title,
                author=edit_author,
                source_reference=edit_source,
                document_date=edit_date,
                original_format=edit_format,
                ocr_language=edit_ocr_language,
                username=st.session_state.username,
                expected_updated_at=(
                    None if pd.isna(selected_edit["updated_at"]) else str(selected_edit["updated_at"])
                ),
            )
            st.success("Datos del documento actualizados.")
            st.rerun()
        except (InvalidDocumentDate, ConcurrentDocumentEditError, DocumentServiceError) as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"No fue posible actualizar el documento: {exc}")

retry_candidates = documents[~documents["ocr_status"].isin(["en_cola", "procesando"])]
if not retry_candidates.empty:
    with st.expander("Procesar o reprocesar OCR"):
        st.caption(
            "Puedes cambiar primero el idioma del documento y luego volver a colocarlo al final de la cola."
        )
        retry_labels = {
            document_display_label(row): int(row["document_id"])
            for row in retry_candidates.to_dict("records")
        }
        selected_retry = st.selectbox("Documento", list(retry_labels), key="retry_document_select")
        if st.button("Agregar nuevamente a la cola", type="primary"):
            try:
                requeue_document(retry_labels[selected_retry], st.session_state.username)
                st.success("Documento agregado al final de la cola.")
                st.rerun()
            except DocumentServiceError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"No fue posible volver a encolar el documento: {exc}")

with st.expander("Eliminar documento"):
    st.warning(
        "Esta acción elimina el registro, sus extracciones asociadas y los archivos PDF del servidor. "
        "No se puede deshacer."
    )
    selected_delete_label = st.selectbox("Documento", list(labels), key="delete_document_select")
    selected_delete_id = labels[selected_delete_label]
    selected_delete = documents[documents["document_id"] == selected_delete_id].iloc[0]
    busy = selected_delete["ocr_status"] == "procesando"
    if busy:
        st.info("Este documento se está procesando. Podrás eliminarlo cuando termine el OCR.")
    confirmation = st.checkbox(
        f"Confirmo que deseo eliminar el documento {selected_delete_id}",
        key=f"delete_confirmation_{selected_delete_id}",
    )
    if st.button(
        "Eliminar definitivamente",
        type="primary",
        disabled=busy or not confirmation,
    ):
        try:
            delete_document(selected_delete_id, st.session_state.username)
            st.success(f"Documento {selected_delete_id} eliminado.")
            st.rerun()
        except DocumentServiceError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"No fue posible eliminar el documento: {exc}")
