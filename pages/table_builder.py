"""Creación y administración de tablas personalizadas."""
import pandas as pd
import streamlit as st

import config
from src.database import get_connection, list_user_tables
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
    existing_tables = list_user_tables(conn)
    registry = list_registered_tables(conn)
    dynamic_table_names = [row["table_name"] for row in registry]
    generic_fk_targets = ["document_records", "extraction_records"] + dynamic_table_names
    generic_fk_targets = [table for table in generic_fk_targets if table in existing_tables]

    tab_create, tab_manage = st.tabs(["Crear", "Administrar"])

    with tab_create:
        col_name, col_desc = st.columns(2)
        with col_name:
            new_table_name = st.text_input("Nombre de la tabla", key="new_table_name_input")
        with col_desc:
            new_table_description = st.text_input("Descripción", key="new_table_description_input")

        with st.form("add_column_form", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                column_name = st.text_input("Campo")
            with col2:
                column_type = st.selectbox(
                    "Tipo",
                    [
                        "Texto",
                        "Número entero",
                        "Número decimal",
                        "Fecha",
                        "Documento",
                        "Llave foránea",
                    ],
                )
            with col3:
                fk_target = None
                if column_type == "Documento":
                    fk_target = "document_records"
                    st.text_input("Tabla relacionada", value="Documentos", disabled=True)
                elif column_type == "Llave foránea":
                    non_document_targets = [
                        table for table in generic_fk_targets if table != "document_records"
                    ]
                    if non_document_targets:
                        fk_target = st.selectbox("Tabla relacionada", non_document_targets)
                    else:
                        st.text_input("Tabla relacionada", value="No hay tablas disponibles", disabled=True)
            add = st.form_submit_button("Agregar campo")

        if add:
            if not column_name.strip():
                st.error("Ingresa un nombre de campo.")
            elif column_type == "Llave foránea" and not fk_target:
                st.error("No hay una tabla disponible para relacionar.")
            else:
                st.session_state.new_table_columns.append({
                    "name": column_name.strip(),
                    "type": column_type,
                    "foreign_key_table": fk_target,
                })
                st.rerun()

        if st.session_state.new_table_columns:
            preview_rows = []
            for column in st.session_state.new_table_columns:
                related = column.get("foreign_key_table") or ""
                preview_rows.append({
                    "Campo": column["name"],
                    "Tipo": column["type"],
                    "Relación": "Documentos" if related == "document_records" else related,
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
