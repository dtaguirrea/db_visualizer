"""Edición de tablas personalizadas con relaciones y trazabilidad."""
import pandas as pd
import streamlit as st
from sqlalchemy import text

import config
from src.database import (
    get_connection,
    get_dynamic_column_metadata,
    get_primary_key_column,
)
from src.dynamic_tables import list_registered_tables
from src.repository import (
    ConcurrentEditError,
    InvalidRecordError,
    delete_records,
    insert_record,
    sync_table_edits,
)
from src.table_relations import document_viewer_url, get_foreign_key_choices

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Tablas")


def _parse_form_value(raw_value: str, column_type: str):
    value = (raw_value or "").strip()
    if value == "":
        return None
    if column_type == "Número entero":
        return int(value)
    if column_type == "Número decimal":
        return float(value.replace(",", "."))
    return value


with get_connection() as conn:
    registered = list_registered_tables(conn)
    available_tables = [row["table_name"] for row in registered]
    descriptions = {row["table_name"]: row.get("description") for row in registered}

    if not available_tables:
        st.info("No hay tablas personalizadas. Crea una tabla para comenzar.")
        st.stop()

    requested_table = st.query_params.get("table") or st.session_state.pop("requested_table", None)
    pending_record_id = st.session_state.pop("requested_record_id", None)
    selected_index = available_tables.index(requested_table) if requested_table in available_tables else 0
    selected_table = st.selectbox("Tabla", available_tables, index=selected_index)
    if descriptions.get(selected_table):
        st.caption(descriptions[selected_table])

    pk_column = get_primary_key_column(conn, selected_table)
    quote = conn.dialect.identifier_preparer.quote
    original_df = pd.read_sql_query(
        text(f"SELECT * FROM {quote(selected_table)} ORDER BY {quote(pk_column)}"), conn
    )
    requested_record_id = (st.query_params.get("record_id") or pending_record_id) if requested_table == selected_table else None
    if requested_record_id and not original_df.empty:
        pk_text = original_df[pk_column].map(lambda value: str(value).removesuffix(".0"))
        focus_mask = pk_text == str(requested_record_id)
        if focus_mask.any():
            original_df = pd.concat([original_df[focus_mask], original_df[~focus_mask]], ignore_index=True)
            st.caption(f"Registro solicitado: {requested_record_id}")
    fk_choices = get_foreign_key_choices(conn, selected_table)
    metadata = get_dynamic_column_metadata(conn, selected_table)

    tab_records, tab_new, tab_delete = st.tabs([
        "Registros", "Nuevo registro", "Eliminar registros"
    ])

    with tab_records:
        display_df = original_df.copy()
        column_config = {
            pk_column: st.column_config.Column(pk_column, disabled=True),
        }
        for audit_column in ("created_by", "created_at", "updated_by", "updated_at"):
            if audit_column in display_df.columns:
                column_config[audit_column] = st.column_config.Column(audit_column, disabled=True)

        for column_name, relation in fk_choices.items():
            labels = relation["labels"]
            display_df[column_name] = display_df[column_name].map(
                lambda value, labels=labels: "" if pd.isna(value) else labels.get(value, str(value))
            )
            column_config[column_name] = st.column_config.SelectboxColumn(
                column_name,
                options=[""] + list(relation["reverse"].keys()),
                help=f"Relacionado con {relation['referred_table']}",
            )

        edited_display_df = st.data_editor(
            display_df,
            num_rows="fixed",
            use_container_width=True,
            column_config=column_config,
            key=f"editor_v6_{selected_table}",
            height=520,
            hide_index=True,
        )

        edited_raw_df = edited_display_df.copy()
        for column_name, relation in fk_choices.items():
            reverse = relation["reverse"]
            edited_raw_df[column_name] = edited_raw_df[column_name].map(
                lambda value, reverse=reverse: None
                if value is None or str(value).strip() == ""
                else reverse.get(value, value)
            )

        document_fk_columns = [
            column_name for column_name, relation in fk_choices.items()
            if relation["referred_table"] == "document_records"
        ]
        if document_fk_columns and not edited_raw_df.empty:
            link_data = pd.DataFrame({pk_column: edited_raw_df[pk_column]})
            link_config = {pk_column: st.column_config.Column(pk_column)}
            for column_name in document_fk_columns:
                link_column = f"Abrir · {column_name}"
                link_data[column_name] = edited_display_df[column_name]
                link_data[link_column] = edited_raw_df[column_name].map(
                    lambda value: document_viewer_url(value) or ""
                )
                link_config[link_column] = st.column_config.LinkColumn(
                    link_column,
                    display_text="Abrir documento",
                )
            st.caption("Documentos vinculados")
            st.dataframe(
                link_data,
                use_container_width=True,
                hide_index=True,
                column_config=link_config,
            )

        col_save, col_reload, _ = st.columns([1, 1, 4])
        with col_save:
            save = st.button("Guardar cambios", type="primary", use_container_width=True)
        with col_reload:
            reload_page = st.button("Recargar", use_container_width=True)

        if reload_page:
            st.rerun()

        if save:
            try:
                summary = sync_table_edits(
                    conn, selected_table, original_df, edited_raw_df, st.session_state.username
                )
                st.success(
                    f"Guardado: {summary['updated']} editados, {summary['deleted']} eliminados."
                )
                st.rerun()
            except ConcurrentEditError as exc:
                st.error(str(exc))
            except Exception as exc:
                conn.rollback()
                st.error(f"No fue posible guardar los cambios: {exc}")

    with tab_new:
        if not metadata:
            st.warning("No se encontraron metadatos de columnas para esta tabla.")
        else:
            with st.form(f"new_record_{selected_table}", clear_on_submit=True):
                values = {}
                parse_specs = {}
                for column in metadata:
                    column_name = column["column_name"]
                    column_type = column["column_type"]
                    relation = fk_choices.get(column_name)

                    if relation:
                        options = [None] + relation["options"]
                        values[column_name] = st.selectbox(
                            column_name,
                            options=options,
                            format_func=lambda value, labels=relation["labels"]: (
                                "Sin selección" if value is None else labels.get(value, str(value))
                            ),
                            key=f"new_{selected_table}_{column_name}",
                        )
                    elif column_type == "Fecha":
                        raw = st.text_input(
                            column_name,
                            placeholder="AAAA-MM-DD",
                            key=f"new_{selected_table}_{column_name}",
                        )
                        values[column_name] = raw
                        parse_specs[column_name] = column_type
                    elif column_type in ("Número entero", "Número decimal"):
                        raw = st.text_input(
                            column_name,
                            key=f"new_{selected_table}_{column_name}",
                        )
                        values[column_name] = raw
                        parse_specs[column_name] = column_type
                    else:
                        values[column_name] = st.text_input(
                            column_name,
                            key=f"new_{selected_table}_{column_name}",
                        )

                create = st.form_submit_button("Agregar registro", type="primary")

            if create:
                try:
                    parsed_values = dict(values)
                    for column_name, column_type in parse_specs.items():
                        parsed_values[column_name] = _parse_form_value(values[column_name], column_type)
                    for column in metadata:
                        name = column["column_name"]
                        if column["column_type"] == "Texto":
                            parsed_values[name] = (parsed_values.get(name) or "").strip() or None
                    new_pk = insert_record(
                        conn, selected_table, parsed_values, st.session_state.username
                    )
                    st.success(f"Registro {new_pk} agregado.")
                    st.rerun()
                except ValueError as exc:
                    st.error(f"Revisa los valores ingresados: {exc}")
                except Exception as exc:
                    conn.rollback()
                    st.error(f"No fue posible agregar el registro: {exc}")

    with tab_delete:
        if original_df.empty:
            st.info("La tabla no contiene registros para eliminar.")
        else:
            st.warning(
                "La eliminación es permanente y queda registrada en Auditoría. "
                "Si otra tabla usa uno de estos registros, la operación será bloqueada."
            )
            user_columns = [
                column["column_name"] for column in metadata
                if column["column_name"] in original_df.columns
            ]
            row_by_pk = {
                row[pk_column]: row for row in original_df.to_dict("records")
            }

            def record_label(pk_value):
                row = row_by_pk[pk_value]
                details = [
                    str(row.get(column)).strip()
                    for column in user_columns
                    if row.get(column) is not None and str(row.get(column)).strip()
                ][:2]
                return f"{pk_value} · {' · '.join(details)}" if details else str(pk_value)

            selected_pks = st.multiselect(
                "Registros que deseas eliminar",
                options=list(row_by_pk),
                format_func=record_label,
            )
            if selected_pks:
                preview_columns = [pk_column] + user_columns
                preview = original_df[original_df[pk_column].isin(selected_pks)][preview_columns]
                st.dataframe(preview, use_container_width=True, hide_index=True)
            confirmed = st.checkbox(
                f"Confirmo la eliminación de {len(selected_pks)} registro(s)",
                disabled=not selected_pks,
            )
            if st.button(
                "Eliminar definitivamente",
                type="primary",
                disabled=not selected_pks or not confirmed,
            ):
                records_to_delete = [
                    {
                        "pk": pk_value,
                        "updated_at": row_by_pk[pk_value].get("updated_at"),
                    }
                    for pk_value in selected_pks
                ]
                try:
                    deleted = delete_records(
                        conn, selected_table, records_to_delete, st.session_state.username
                    )
                    st.success(f"{deleted} registro(s) eliminado(s).")
                    st.rerun()
                except (ConcurrentEditError, InvalidRecordError) as exc:
                    st.error(str(exc))
                except Exception as exc:
                    conn.rollback()
                    st.error(f"No fue posible eliminar los registros: {exc}")
