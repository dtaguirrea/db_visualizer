"""Búsqueda global en OCR y tablas personalizadas."""
import html
from datetime import date
import re
from urllib.parse import quote

import streamlit as st

from src.database import get_connection
from src.documents import document_display_label, document_text_snippets
from src.search import global_search, search_documents
from src.navigation import open_document, open_table

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Buscar")
st.caption("Busca en documentos OCR y tablas personalizadas.")


def _highlight(value: str | None, query: str) -> str:
    source = str(value or "")
    terms = [query.strip()] + [
        term for term in re.findall(r"\w+", query, flags=re.UNICODE)
        if len(term) >= 2
    ]
    terms = list(dict.fromkeys(term for term in terms if term))
    if not terms:
        return html.escape(source)
    pattern = re.compile("(" + "|".join(re.escape(term) for term in terms) + ")", re.IGNORECASE)
    parts = pattern.split(source)
    return "".join(
        f"<mark>{html.escape(part)}</mark>" if index % 2 else html.escape(part)
        for index, part in enumerate(parts)
    )


with st.form("search_form"):
    query_text = st.text_input("Texto", placeholder="Palabra o frase")
    author_filter = st.text_input("Autor del documento (opcional)")
    left, right = st.columns(2)
    date_from = left.date_input("Fecha de autoría desde (opcional)", value=None,
                                min_value=date(1800, 1, 1), max_value=date.today())
    date_to = right.date_input("Fecha de autoría hasta (opcional)", value=None,
                              min_value=date(1800, 1, 1), max_value=date.today())
    st.caption("Las fechas parciales se incluyen si su intervalo posible coincide con el filtro; las fechas sin año se excluyen al filtrar por fecha.")
    st.caption("Autor y fechas filtran documentos. Puedes dejar Texto vacío; usa ambas fechas iguales para un día exacto.")
    submitted = st.form_submit_button("Buscar", type="primary")

if submitted:
    st.session_state["global_search_query"] = query_text.strip()
    st.session_state["document_search_filters"] = dict(
        author=author_filter.strip(), date_from=date_from, date_to=date_to)

query = st.session_state.get("global_search_query", "").strip()
filters = st.session_state.get("document_search_filters", {})
if not query and not any(filters.values()):
    st.stop()

with get_connection() as conn:
    try:
        if any(filters.values()):
            results = {"documents": search_documents(conn, query, **filters),
                       "tables": []}
        else:
            results = global_search(conn, query)
    except Exception as exc:
        st.error(f"No fue posible ejecutar la búsqueda: {exc}")
        st.stop()

documents = results["documents"]
table_rows = results["tables"]
total = len(documents) + len(table_rows)

if total == 0:
    st.info("No se encontraron coincidencias.")
    st.stop()

st.caption(
    f"{total} resultado(s): {len(documents)} documento(s), "
    f"{len(table_rows)} registro(s) de tablas."
)

tab_documents, tab_tables = st.tabs([
    f"Documentos ({len(documents)})",
    f"Tablas ({len(table_rows)})",
])

with tab_documents:
    if not documents:
        st.info("Sin coincidencias en documentos.")
    for row in documents:
        st.markdown(f"**{html.escape(document_display_label(row))}**")
        st.caption(
            f"Autor: {row.get('author') or 'Sin indicar'} · "
            f"{row['original_format'] or 'Formato no indicado'} · OCR: {row['ocr_status']} · "
            f"{row['created_by']}"
        )
        contexts = document_text_snippets(row.get("ocr_text"), query, limit=2, radius=160)
        if contexts:
            for context in contexts:
                st.markdown(_highlight(context, query), unsafe_allow_html=True)
        else:
            metadata_match = " · ".join(
                str(value) for value in [row.get("title"), row.get("source_reference"), row.get("document_date")]
                if value
            )
            if query.casefold() in metadata_match.casefold():
                st.markdown(_highlight(metadata_match, query), unsafe_allow_html=True)
        if st.button(f"Ver coincidencias · documento {int(row['document_id'])}", key=f"search_doc_{row['document_id']}"):
            open_document(row["document_id"], query=query)
        st.divider()

with tab_tables:
    if not table_rows:
        st.info("Sin coincidencias en tablas personalizadas.")
    for index, row in enumerate(table_rows):
        st.markdown(f"**{html.escape(row['table_name'])} · registro {html.escape(str(row['record_id']))}**")
        if row.get("description"):
            st.caption(row["description"])
        for value in row["matched_values"]:
            st.markdown(_highlight(value, query), unsafe_allow_html=True)
        table_url = f"/tablas?table={quote(row['table_name'])}&record_id={quote(str(row['record_id']))}"
        if st.button(f"Abrir {row['table_name']} · {row['record_id']}", key=f"search_table_{index}"):
            open_table(row["table_name"], row["record_id"])
        st.divider()
