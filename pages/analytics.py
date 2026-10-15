"""Exploración gráfica de datos reales, sin modificar las tablas de origen."""
import altair as alt
import streamlit as st
from sqlalchemy import text

from src.analytics import (annual_frame, category_label, column_catalog, frequency_frame,
    grouped_values, normalized_name, relation_catalog, relation_ranking, table_catalog)
from src.analytics_ui import show_annual, show_frequency
from src.csv_tables import export_csv
from src.database import get_connection

if not st.session_state.get('logged_in'):
    st.stop()

st.title('Gráficos y estadísticas')
st.caption('Los gráficos cuentan registros guardados. Si una entidad está duplicada en la tabla, '
           'se contará más de una vez. No se deducen relaciones desde el texto OCR.')
st.button('Actualizar datos', key='analytics_refresh')

with get_connection() as conn:
    catalog = table_catalog(conn)
    mode = st.radio('Vista', ['Explorar tablas', 'Documentos', 'Relaciones'], horizontal=True)
    try:
        if mode == 'Explorar tablas':
            names = list(catalog)
            default = next((i for i, name in enumerate(names) if 'persona' in normalized_name(name)), 0)
            table = st.selectbox('Tabla', names, index=default, format_func=catalog.get)
            columns = column_catalog(conn, table)
            if not columns:
                st.info('Esta tabla no tiene columnas disponibles para gráficos.')
                st.stop()
            names = list(columns)
            default = next((i for i, name in enumerate(names)
                            if normalized_name(name) in ('genero', 'sexo', 'gender')), 0)
            column = st.selectbox('Columna que deseas analizar', names, index=default,
                                 format_func=lambda c: columns[c]['label'], key=f'analytics_column_{table}')
            filter_column = st.selectbox('Filtrar por otra columna (opcional)', [None] + names,
                format_func=lambda c: 'Sin filtro' if c is None else columns[c]['label'],
                key=f'analytics_filter_{table}')
            filters = {}
            if filter_column is not None:
                filter_rows = grouped_values(conn, table, filter_column)
                if not filter_rows:
                    st.info('La tabla está vacía.')
                    st.stop()
                values = [r['value'] for r in filter_rows]
                selected = st.selectbox('Valor del filtro', values, format_func=category_label,
                    key=f'analytics_filter_value_{table}_{filter_column}')
                filters = {'filter_column': filter_column, 'filter_value': selected}
            scope = f'analytics_{table}_{column}'
            is_date = columns[column]['kind'] == 'Fecha'
            rows = grouped_values(conn, table, column, limit=10000 if is_date else 1000, **filters)
            total = sum(r['quantity'] for r in rows)
            missing = sum(r['quantity'] for r in rows if r['value'] is None)
            a, b, c = st.columns(3)
            a.metric('Registros seleccionados', total)
            b.metric('Con información', total - missing)
            c.metric('Sin información', missing)
            if is_date:
                frame, excluded = annual_frame(rows)
                show_annual(frame, excluded, key=scope)
            else:
                if columns[column]['kind'] in ('Documento', 'Llave foránea'):
                    st.caption('Esta distribución muestra IDs. La vista Relaciones ofrece nombres y rankings.')
                include_missing = st.checkbox('Incluir registros sin información en los porcentajes',
                                              value=True, key=f'{scope}_missing')
                denominator = total if include_missing else total - missing
                st.caption(f'Denominador de los porcentajes: {denominator} registros '
                           f'({"incluye" if include_missing else "excluye"} datos faltantes), después del filtro.')
                values = [r['value'] for r in rows if r['value'] is not None]
                defaults = [v for v in values if normalized_name(v) in ('mujer', 'mujeres', 'femenino', 'female')]
                chosen = st.multiselect('Categorías para calcular un porcentaje destacado', values,
                    default=defaults, format_func=category_label, key=f'{scope}_highlight')
                if chosen:
                    numerator = sum(r['quantity'] for r in rows if r['value'] in chosen)
                    st.metric('Porcentaje de las categorías seleccionadas',
                              f'{100 * numerator / denominator:.2f} %' if denominator else 'Sin datos')
                    st.caption(f'{numerator} de {denominator} registros. Selección: {", ".join(chosen)}.')
                show_frequency(frequency_frame(rows, include_missing=include_missing),
                               key=scope, title=f'Distribución por {columns[column]["label"]}')

        elif mode == 'Documentos':
            counts = conn.execute(text("SELECT COUNT(*) AS total, "
                "COALESCE(SUM(CASE WHEN ocr_file_path IS NOT NULL THEN 1 ELSE 0 END), 0) AS ready "
                "FROM document_records")).mappings().one()
            a, b = st.columns(2)
            a.metric('Documentos registrados', counts['total'])
            b.metric('Documentos con PDF OCR', counts['ready'])
            columns = column_catalog(conn, 'document_records')
            column = st.selectbox('Análisis documental',
                ['document_date', 'ocr_status', 'ocr_language', 'original_format', 'author', 'source_reference'],
                format_func=lambda c: columns[c]['label'])
            rows = grouped_values(conn, 'document_records', column,
                                  limit=10000 if column == 'document_date' else 1000)
            if column == 'document_date':
                frame, excluded = annual_frame(rows)
                show_annual(frame, excluded, key='analytics_documents_year')
            else:
                if column == 'ocr_language':
                    st.caption('Idioma seleccionado al solicitar el OCR; no es una detección del idioma del documento.')
                st.caption(f'Denominador: {sum(r["quantity"] for r in rows)} documentos, incluidos los datos faltantes.')
                show_frequency(frequency_frame(rows), key=f'analytics_documents_{column}',
                               title=columns[column]['label'])

        else:
            relations = relation_catalog(conn)
            if not relations:
                st.info('No hay llaves foráneas entre las tablas disponibles. Para contar personas por documento, '
                        'crea una relación Documento en Personas o una tabla intermedia con Persona y Documento.')
            else:
                index = st.selectbox('Relación', range(len(relations)), format_func=lambda i:
                    f'{catalog[relations[i]["source"]]} · {relations[i]["column"]} → {catalog[relations[i]["target"]]}')
                relation = relations[index]
                other = {r['column']: r for r in relations if r['source'] == relation['source']
                         and r['column'] != relation['column']}
                distinct = st.selectbox('Qué contar', [None] + list(other), key=f'analytics_distinct_{index}',
                    format_func=lambda c: f'Registros de {relation["source"]}' if c is None
                    else f'IDs distintos de {catalog[other[c]["target"]]} ({c})')
                limit = st.selectbox('Registros del ranking', [10, 20, 50])
                frame = relation_ranking(conn, relation, distinct_column=distinct, limit=limit)
                st.caption('El ranking incluye destinos sin vínculos (cantidad 0). Las filas sin destino '
                           'no se asignan a ningún registro. No representa un porcentaje del total.')
                if distinct:
                    st.caption('Cada ID se cuenta una vez por destino; los IDs vacíos no se cuentan. '
                               'Una misma entidad puede aparecer en varios destinos.')
                if frame.empty:
                    st.info('La tabla de destino no tiene registros.')
                else:
                    chart = alt.Chart(frame).mark_bar(color='#315b7c').encode(
                        x=alt.X('Cantidad:Q'), y=alt.Y('Registro:N', sort='-x', axis=alt.Axis(labelLimit=300)),
                        tooltip=['Registro:N', 'Cantidad:Q'])
                    st.altair_chart(chart.properties(height=max(240, len(frame) * 28)), use_container_width=True)
                    st.dataframe(frame, hide_index=True, use_container_width=True)
                    st.download_button('Descargar ranking CSV', export_csv(frame), 'ranking_relaciones.csv', 'text/csv')
    except ValueError as exc:
        st.warning(str(exc))
