"""Espacio de fichaje, revisión y exportación de evidencias documentales."""
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

import config
from src.audit import log_action
from src.database import get_connection, utc_now
from src.documents import document_display_label, safe_pdf_filename
from src.repository import (
    ConcurrentEditError,
    InvalidRecordError,
    delete_records,
    sync_table_edits,
)
from src.table_relations import document_viewer_url

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Extracción")
st.caption(
    "Transforma hallazgos del documento en fichas verificables: registra la página, "
    "la cita literal, su interpretación y el estado de revisión."
)

with get_connection() as conn:
    documents_df = pd.read_sql_query(text('''
        SELECT document_id, title, source_reference, document_date, original_file_path,
               ocr_file_path, file_path, ocr_text, ocr_status, ocr_language
        FROM document_records
        ORDER BY document_id DESC
    '''), conn)

if documents_df.empty:
    st.info("Carga un documento antes de iniciar una extracción.")
    st.stop()

document_records = documents_df.to_dict("records")
document_by_id = {int(row["document_id"]): row for row in document_records}
document_ids = list(document_by_id)
raw_document_id = st.query_params.get("document_id")
try:
    requested_id = int(raw_document_id) if raw_document_id is not None else None
except (TypeError, ValueError):
    requested_id = None

selection_key = "extraction_document_selection"
last_query_key = "extraction_last_query_document_id"
if requested_id in document_by_id and st.session_state.get(last_query_key) != requested_id:
    st.session_state[selection_key] = requested_id
    st.session_state[last_query_key] = requested_id
elif st.session_state.get(selection_key) not in document_by_id:
    st.session_state[selection_key] = document_ids[0]

selected_document_id = st.selectbox(
    "Documento de trabajo",
    options=document_ids,
    key=selection_key,
    format_func=lambda value: document_display_label(document_by_id[value]),
)
if requested_id != selected_document_id:
    st.query_params["document_id"] = str(selected_document_id)
    st.session_state[last_query_key] = selected_document_id
selected = document_by_id[selected_document_id]

with get_connection() as conn:
    extractions_df = pd.read_sql_query(text('''
        SELECT extraction_id, document_id, page_number, element_category,
               key_information, literal_quote, cross_reference_notes, review_status,
               created_by, created_at, updated_by, updated_at
        FROM extraction_records
        WHERE document_id = :document_id
        ORDER BY extraction_id DESC
    '''), conn, params={"document_id": selected_document_id})

status_counts = (
    extractions_df["review_status"].value_counts().to_dict()
    if not extractions_df.empty else {}
)
metric1, metric2, metric3, metric4 = st.columns(4)
metric1.metric("Fichas", len(extractions_df))
metric2.metric("Pendientes", int(status_counts.get("Pendiente", 0)))
metric3.metric("Revisadas", int(status_counts.get("Revisado", 0)))
metric4.metric("Validadas", int(status_counts.get("Validado", 0)))

col_pdf, col_form = st.columns([1.25, 1], gap="large")

with col_pdf:
    st.subheader("Fuente")
    st.link_button(
        "Abrir en el visor de documentos",
        document_viewer_url(selected_document_id),
        use_container_width=True,
    )
    pdf_path = selected.get("ocr_file_path") or selected.get("file_path") or selected.get("original_file_path")
    if pdf_path and Path(pdf_path).is_file():
        if selected.get("ocr_file_path") and Path(selected["ocr_file_path"]).is_file():
            with open(selected["ocr_file_path"], "rb") as pdf_file:
                st.download_button(
                    "Descargar PDF con OCR",
                    data=pdf_file,
                    file_name=safe_pdf_filename(
                        selected_document_id,
                        selected.get("title") or selected.get("source_reference"),
                        ocr=True,
                    ),
                    mime="application/pdf",
                    key=f"extraction_download_ocr_{selected_document_id}",
                    on_click="ignore",
                    use_container_width=True,
                )
        st.pdf(pdf_path, height=820, key=f"extraction_pdf_{selected_document_id}")
        if selected.get("ocr_status") == "parcial":
            st.warning("El PDF está disponible, pero su texto no pudo indexarse completamente.")
        elif not selected.get("ocr_file_path"):
            st.warning("Este documento aún no tiene una copia OCR disponible.")
    else:
        st.error("No se encuentra el archivo PDF en el servidor.")

with col_form:
    st.subheader("Nueva ficha")
    st.caption(
        "Información clave resume el hallazgo con tus palabras. Cita literal conserva "
        "la evidencia exacta para que otra persona pueda verificarla."
    )
    with st.form(f"extraction_form_{selected_document_id}", clear_on_submit=True):
        form_col1, form_col2 = st.columns([2, 1])
        with form_col1:
            element_category = st.selectbox("Categoría", options=config.ELEMENT_CATEGORIES)
        with form_col2:
            page_text = st.text_input("Página", placeholder="Opcional")
        key_information = st.text_area(
            "Información clave",
            height=170,
            placeholder="¿Qué hecho, actor, proceso o relación relevante aparece?",
        )
        literal_quote = st.text_area(
            "Cita literal",
            height=140,
            placeholder="Transcribe el fragmento que respalda la ficha.",
        )
        cross_reference_notes = st.text_area(
            "Cruces y notas",
            height=100,
            placeholder="Relaciona esta ficha con personas, organizaciones, fechas u otros documentos.",
        )
        review_status = st.selectbox("Estado", ["Pendiente", "Revisado", "Validado"])
        submitted = st.form_submit_button(
            "Guardar ficha", type="primary", use_container_width=True
        )

    if submitted:
        try:
            page_number = int(page_text) if page_text.strip() else None
            if page_number is not None and page_number < 1:
                raise ValueError("La página debe ser un número entero mayor o igual a 1.")
            if not key_information.strip():
                raise ValueError("La información clave no puede quedar vacía.")
            with get_connection() as conn:
                now = utc_now()
                result = conn.execute(text('''
                    INSERT INTO extraction_records (
                        document_id, page_number, element_category, key_information,
                        literal_quote, cross_reference_notes, review_status,
                        created_by, created_at, updated_by, updated_at
                    ) VALUES (
                        :document_id, :page_number, :element_category, :key_information,
                        :literal_quote, :cross_reference_notes, :review_status,
                        :created_by, :created_at, :updated_by, :updated_at
                    ) RETURNING extraction_id
                '''), {
                    "document_id": selected_document_id,
                    "page_number": page_number,
                    "element_category": element_category,
                    "key_information": key_information.strip(),
                    "literal_quote": literal_quote.strip() or None,
                    "cross_reference_notes": cross_reference_notes.strip() or None,
                    "review_status": review_status,
                    "created_by": st.session_state.username,
                    "created_at": now,
                    "updated_by": st.session_state.username,
                    "updated_at": now,
                })
                extraction_id = int(result.scalar_one())
                log_action(
                    conn,
                    "extraction_records",
                    extraction_id,
                    "INSERT",
                    st.session_state.username,
                    details={"document_id": selected_document_id, "page_number": page_number},
                )
                conn.commit()
            st.success(f"Ficha {extraction_id} guardada.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"No fue posible guardar la ficha: {exc}")

    with st.expander("Texto OCR indexado"):
        if selected.get("ocr_text"):
            st.text_area(
                "Texto indexado",
                selected["ocr_text"],
                height=280,
                label_visibility="collapsed",
            )
        else:
            st.info("Este documento todavía no tiene texto indexado.")

st.divider()
st.subheader("Fichas del documento")

if extractions_df.empty:
    st.info("Todavía no hay fichas para este documento.")
    st.stop()

filter_col, download_col = st.columns([3, 1])
with filter_col:
    extraction_query = st.text_input(
        "Filtrar fichas",
        placeholder="Busca en categoría, información, cita, notas o estado",
    )
with download_col:
    st.download_button(
        "Exportar fichas CSV",
        data=extractions_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"documento_{selected_document_id}_extracciones.csv",
        mime="text/csv",
        use_container_width=True,
        on_click="ignore",
    )

filtered_df = extractions_df.copy()
if extraction_query.strip():
    query = extraction_query.strip().casefold()
    searchable = filtered_df[[
        "element_category", "key_information", "literal_quote",
        "cross_reference_notes", "review_status",
    ]].fillna("").astype(str).agg(" ".join, axis=1).str.casefold()
    filtered_df = filtered_df[searchable.str.contains(query, regex=False)].copy()

if filtered_df.empty:
    st.info("No hay fichas que coincidan con el filtro.")
    st.stop()

tab_edit, tab_delete = st.tabs(["Revisar y editar", "Eliminar fichas"])

with tab_edit:
    editable_df = filtered_df.drop(columns=["document_id"]).copy()
    column_config = {
        "extraction_id": st.column_config.NumberColumn("ID", disabled=True),
        "page_number": st.column_config.NumberColumn("Página", min_value=1, step=1),
        "element_category": st.column_config.SelectboxColumn(
            "Categoría", options=config.ELEMENT_CATEGORIES, required=True
        ),
        "key_information": st.column_config.TextColumn("Información clave", required=True),
        "literal_quote": st.column_config.TextColumn("Cita literal"),
        "cross_reference_notes": st.column_config.TextColumn("Cruces y notas"),
        "review_status": st.column_config.SelectboxColumn(
            "Estado", options=["Pendiente", "Revisado", "Validado"], required=True
        ),
    }
    for audit_column in ("created_by", "created_at", "updated_by", "updated_at"):
        column_config[audit_column] = st.column_config.Column(audit_column, disabled=True)

    edited_df = st.data_editor(
        editable_df,
        num_rows="fixed",
        use_container_width=True,
        hide_index=True,
        height=420,
        column_config=column_config,
        key=f"extraction_editor_{selected_document_id}",
    )
    save_col, reload_col, _ = st.columns([1, 1, 4])
    save_changes = save_col.button("Guardar cambios", type="primary", use_container_width=True)
    if reload_col.button("Recargar", use_container_width=True):
        st.rerun()
    if save_changes:
        try:
            with get_connection() as conn:
                summary = sync_table_edits(
                    conn,
                    "extraction_records",
                    filtered_df.drop(columns=["document_id"]),
                    edited_df,
                    st.session_state.username,
                )
            st.success(f"{summary['updated']} ficha(s) actualizada(s).")
            st.rerun()
        except (ConcurrentEditError, InvalidRecordError) as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"No fue posible actualizar las fichas: {exc}")

with tab_delete:
    extraction_by_id = {
        int(row["extraction_id"]): row for row in filtered_df.to_dict("records")
    }
    selected_extractions = st.multiselect(
        "Fichas que deseas eliminar",
        options=list(extraction_by_id),
        format_func=lambda value: (
            f"{value} · pág. {extraction_by_id[value].get('page_number') or 's/p'} · "
            f"{str(extraction_by_id[value].get('key_information') or '')[:90]}"
        ),
    )
    confirmed = st.checkbox(
        f"Confirmo la eliminación de {len(selected_extractions)} ficha(s)",
        disabled=not selected_extractions,
    )
    if st.button(
        "Eliminar definitivamente",
        type="primary",
        disabled=not selected_extractions or not confirmed,
    ):
        records = [
            {
                "pk": extraction_id,
                "updated_at": extraction_by_id[extraction_id].get("updated_at"),
            }
            for extraction_id in selected_extractions
        ]
        try:
            with get_connection() as conn:
                deleted = delete_records(
                    conn, "extraction_records", records, st.session_state.username
                )
            st.success(f"{deleted} ficha(s) eliminada(s).")
            st.rerun()
        except (ConcurrentEditError, InvalidRecordError) as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"No fue posible eliminar las fichas: {exc}")
