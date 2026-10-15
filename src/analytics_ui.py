"""Presentación reutilizable de gráficos y sus tablas descargables."""
import altair as alt
import streamlit as st

from src.analytics import compact_frequencies
from src.csv_tables import export_csv


def show_frequency(frame, *, key, title):
    st.subheader(title)
    if frame.empty:
        st.info('No hay registros para esta selección.')
        return
    left, right = st.columns(2)
    style = left.selectbox('Presentación', ['Barras', 'Circular', 'Solo tabla'], key=f'{key}_style')
    top_n = right.selectbox('Categorías principales', [10, 20, 30], key=f'{key}_top')
    plot = compact_frequencies(frame, top_n)
    tooltip = [alt.Tooltip('Categoría:N'), alt.Tooltip('Cantidad:Q', format=',d'),
               alt.Tooltip('Porcentaje:Q', format='.2f', title='Porcentaje (%)')]
    base = alt.Chart(plot)
    if style == 'Barras':
        chart = base.mark_bar(color='#315b7c').encode(
            x=alt.X('Cantidad:Q', title='Registros'),
            y=alt.Y('Categoría:N', sort='-x', axis=alt.Axis(labelLimit=280)), tooltip=tooltip)
        st.altair_chart(chart.properties(height=max(220, len(plot) * 28)), use_container_width=True)
    elif style == 'Circular':
        chart = base.mark_arc(innerRadius=65).encode(theta=alt.Theta('Cantidad:Q'),
            color=alt.Color('Categoría:N', legend=alt.Legend(title='Categoría')), tooltip=tooltip)
        st.altair_chart(chart.properties(height=340), use_container_width=True)
    if len(plot) < len(frame):
        st.caption('Las categorías restantes se agrupan para conservar el total y los porcentajes.')
    st.dataframe(frame, hide_index=True, use_container_width=True,
                 column_config={'Porcentaje': st.column_config.NumberColumn('Porcentaje (%)', format='%.2f')})
    st.download_button('Descargar resumen CSV', export_csv(frame), file_name=f'{key}.csv',
                       mime='text/csv', key=f'{key}_download')


def show_annual(frame, excluded, *, key):
    st.caption('Cada registro se cuenta una vez por su año conocido, sin inventar mes ni día.')
    st.caption(' · '.join(f'{name}: {count}' for name, count in excluded.items()))
    if frame.empty:
        st.info('No hay fechas válidas con año conocido.')
        return
    chart = alt.Chart(frame).mark_bar(color='#315b7c').encode(
        x=alt.X('Año:O', sort='ascending'), y=alt.Y('Cantidad:Q', title='Registros'),
        tooltip=['Año:O', 'Cantidad:Q'])
    st.altair_chart(chart.properties(height=320), use_container_width=True)
    st.dataframe(frame, hide_index=True, use_container_width=True)
    st.download_button('Descargar serie anual CSV', export_csv(frame), file_name=f'{key}.csv',
                       mime='text/csv', key=f'{key}_download')
