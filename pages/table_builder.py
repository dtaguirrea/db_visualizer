"""Creación y administración de tablas personalizadas."""
import pandas as pd
import streamlit as st

import config
from src.csv_ui import render_csv_import
from src.column_ui import column_controls, render_add_column
from src.dynamic_tables import prepare_column
from src.database import get_connection
from src.dynamic_tables import (
    InvalidIdentifierError,
    create_dynamic_table,
    drop_dynamic_table,
    list_registered_tables,
)

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Crear tablas")
st.session_state.setdefault("new_table_columns", [])

with get_connection() as conn:
    registry = list_registered_tables(conn)
    dynamic_table_names = [row["table_name"] for row in registry]

    tab_create, tab_manage, tab_csv = st.tabs(["Crear", "Administrar", "Crear desde CSV"])

    with tab_csv:
        render_csv_import(conn, st.session_state.username)

    with tab_create:
        col_name, col_desc = st.columns(2)
        with col_name:
            new_table_name = st.text_input("Nombre de la tabla", key="new_table_name_input")
        with col_desc:
            new_table_description = st.text_input("Descripción", key="new_table_description_input")

        column = column_controls(conn, 'create_column')
        if st.button('Agregar campo'):
            try:
                seen = {'record_id', 'created_by', 'created_at', 'updated_by', 'updated_at'} | {c['name'] for c in st.session_state.new_table_columns}
                _, normalized = prepare_column(conn, column, seen, len(st.session_state.new_table_columns))
                column['name'] = normalized['name']
                st.session_state.new_table_columns.append(column)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

        if st.session_state.new_table_columns:
            preview_rows = []
            for column in st.session_state.new_table_columns:
                related = column.get("foreign_key_table") or ""
                preview_rows.append({
                    "Campo": column["name"],
                    "Tipo": column["type"],
                    "Relación": "Documentos" if related == "document_records" else related,
                    "Opciones": column.get("options") or "",
                })
            st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
            col_remove, col_clear, _ = st.columns([1, 1, 4])
            with col_remove:
                if st.button("Quitar último"):
                    st.session_state.new_table_columns.pop()
                    st.rerun()
            with col_clear:
                if st.button("Vaciar"):
                    st.session_state.new_table_columns = []
                    st.rerun()

        if st.button("Crear tabla", type="primary"):
            try:
                created = create_dynamic_table(
                    conn,
                    new_table_name,
                    st.session_state.new_table_columns,
                    new_table_description,
                    st.session_state.username,
                )
                st.session_state.new_table_columns = []
                st.success(f"Tabla {created} creada.")
                st.rerun()
            except (InvalidIdentifierError, ValueError) as exc:
                st.error(str(exc))
            except Exception as exc:
                conn.rollback()
                st.error(f"No fue posible crear la tabla: {exc}")

        current_registry = list_registered_tables(conn)
        if current_registry:
            st.subheader("Tablas personalizadas")
            st.dataframe(pd.DataFrame(current_registry), use_container_width=True, hide_index=True)

    with tab_manage:
        if dynamic_table_names:
            selected = st.selectbox("Tabla para agregar columnas", dynamic_table_names)
            render_add_column(conn, selected, st.session_state.username)
            st.divider()
        removable = dynamic_table_names
        if not removable:
            st.info("No hay tablas personalizadas para eliminar.")
        else:
            with st.form("drop_table_form"):
                table_to_drop = st.selectbox("Tabla", removable)
                confirmation = st.text_input("Escribe ELIMINAR para confirmar")
                drop = st.form_submit_button("Eliminar tabla")
            if drop:
                if confirmation != "ELIMINAR":
                    st.error("La confirmación no coincide.")
                else:
                    try:
                        drop_dynamic_table(conn, table_to_drop, st.session_state.username)
                        st.success(f"Tabla {table_to_drop} eliminada.")
                        st.rerun()
                    except Exception as exc:
                        conn.rollback()
                        st.error(str(exc))
