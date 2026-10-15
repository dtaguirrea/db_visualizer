"""Controles CSV compartidos por el creador y el explorador de tablas."""
import streamlit as st

from src.csv_tables import import_csv, read_csv
from src.dynamic_tables import validate_identifier
from src.repository import AUDIT_COLUMNS
from src.field_values import DATE_HELP


def render_csv_import(conn, username, *, table_name=None):
    scope = f"csv_{table_name or 'create'}"
    message = st.session_state.pop(f"{scope}_success", None)
    if message:
        st.success(message)
    generation = st.session_state.get(f"{scope}_generation", 0)
    key = f"{scope}_{generation}"
    st.caption(DATE_HELP)
    st.caption(
        "CSV de hasta 10 MB y 10.000 registros, con encabezados. "
        "Las celdas vacías se guardan como valores nulos."
    )
    upload = st.file_uploader("Archivo CSV", type=["csv"], key=f"{key}_file")
    left, right = st.columns(2)
    with left:
        separator = st.selectbox("Separador", ["Automático", "Coma", "Punto y coma", "Tabulación"], key=f"{key}_sep")
    with right:
        encoding = st.selectbox("Codificación", ["UTF-8", "Windows-1252"], key=f"{key}_encoding")
    if upload is None:
        return
    try:
        frame = read_csv(upload.getvalue(),
            encoding="utf-8-sig" if encoding == "UTF-8" else "cp1252",
            delimiter={"Automático": "auto", "Coma": ",", "Punto y coma": ";", "Tabulación": "\t"}[separator])
        st.caption(f"{len(frame)} registros · {len(frame.columns)} columnas. Vista previa de las primeras 50 filas.")
        st.dataframe(frame.head(50), hide_index=True, use_container_width=True)
        columns = None
        description = ""
        if table_name is None:
            table_name = st.text_input("Nombre de la nueva tabla", key=f"{key}_name")
            description = st.text_input("Descripción de la tabla", key=f"{key}_description")
            st.caption(
                "Elige el tipo de cada columna. Texto conserva códigos con ceros iniciales. "
                "Los encabezados se convierten a minúsculas y sus espacios a guiones bajos. "
                "Un CSV no define relaciones: para importar llaves foráneas, crea primero "
                "la tabla con sus relaciones y usa Importar CSV en Tablas."
            )
            columns = []
            for index, header in enumerate(frame.columns):
                name = validate_identifier(header, "encabezado")
                if name in {"record_id", *AUDIT_COLUMNS}:
                    continue
                kind = st.selectbox(f"Tipo de {header}", ["Texto", "Número entero", "Número decimal", "Fecha", "Opciones"],
                                    key=f"{key}_type_{index}_{header}")
                options = st.text_area(f"Opciones de {header} (una por línea)", key=f"{key}_options_{index}") if kind == "Opciones" else None
                columns.append({"name": name, "type": kind, "options": options})
        else:
            st.info(
                "Se agregarán registros nuevos a esta tabla; los existentes se conservan. "
                "Los encabezados deben coincidir con sus columnas. Las relaciones requieren "
                "el ID del registro relacionado. Las opciones deben coincidir con el catálogo definido."
            )
        st.caption(
            "Los IDs propios y los campos de auditoría del CSV se ignoran: se generan IDs nuevos "
            "y se registra tu usuario como autor de la importación. Repetir la carga duplica los registros."
        )
        confirm = st.checkbox("Confirmo la importación de estos registros", key=f"{key}_confirm")
        if st.button("Importar CSV", type="primary", disabled=not confirm or frame.empty, key=f"{key}_import"):
            target, count = import_csv(conn, frame, username, table_name=table_name,
                                       columns=columns, description=description)
            st.session_state.pop(f"table_snapshot_{target}", None)
            st.session_state[f"table_generation_{target}"] = st.session_state.get(f"table_generation_{target}", 0) + 1
            st.session_state[f"{scope}_success"] = f"Se importaron {count} registros en {target}."
            st.session_state[f"{scope}_generation"] = generation + 1
            st.rerun()
    except ValueError as exc:
        st.error(str(exc))
    except Exception as exc:
        conn.rollback()
        st.error(f"No fue posible importar el CSV. No se guardó ningún registro: {exc}")
