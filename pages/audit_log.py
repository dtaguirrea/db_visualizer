"""Historial de actividad."""
import pandas as pd
import streamlit as st

from src.audit import fetch_audit_log
from src.auth import list_users
from src.database import get_connection, list_user_tables

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Auditoría")

with get_connection() as conn:
    tables = ["Todas"] + list_user_tables(conn)
    users = ["Todos"] + [user["username"] for user in list_users()]
    col1, col2 = st.columns(2)
    with col1:
        table_filter = st.selectbox("Tabla", tables)
    with col2:
        user_filter = st.selectbox("Usuario", users)

    entries = fetch_audit_log(
        conn,
        table_filter=None if table_filter == "Todas" else table_filter,
        user_filter=None if user_filter == "Todos" else user_filter,
    )

if not entries:
    st.info("No hay actividad para los filtros seleccionados.")
else:
    df = pd.DataFrame(entries)[["occurred_at", "username", "action", "table_name", "record_id", "details"]]
    df.columns = ["Fecha", "Usuario", "Acción", "Tabla", "Registro", "Detalle"]
    st.dataframe(df, use_container_width=True, hide_index=True, height=600)
