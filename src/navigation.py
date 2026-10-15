"""Navegación interna: conserva sesión y parámetros del destino."""
import streamlit as st


def register_pages():
    pages = {
        "Proyecto": [
            st.Page("pages/dashboard.py", title="Inicio", default=True),
            st.Page("pages/document_upload.py", title="Documentos"),
            st.Page("pages/document_viewer.py", title="Visor de documento", url_path="documento"),
        ],
        "Datos": [
            st.Page("pages/data_explorer.py", title="Tablas", url_path="tablas"),
            st.Page("pages/analytics.py", title="Gráficos", url_path="graficos"),
            st.Page("pages/search_engine.py", title="Buscar"),
            st.Page("pages/table_builder.py", title="Crear tablas"),
        ],
        "Administración": [
            st.Page("pages/audit_log.py", title="Auditoría"),
            st.Page("pages/my_account.py", title="Cuenta"),
        ],
    }
    return st.navigation(pages)


def open_document(document_id, *, query="", page=None):
    params = {"document_id": str(int(document_id))}
    if query:
        params["q"] = query
    if page is not None:
        params["page"] = str(int(page))
    st.switch_page("pages/document_viewer.py", query_params=params)


def open_table(table, record_id=None):
    params = {"table": table}
    if record_id is not None:
        params["record_id"] = str(record_id)
    st.switch_page("pages/data_explorer.py", query_params=params)
