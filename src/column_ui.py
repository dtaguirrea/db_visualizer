"""Controles para definir columnas sin formularios que oculten campos dependientes."""
import streamlit as st
from src.dynamic_tables import add_dynamic_column, list_registered_tables

TYPES = ['Texto', 'Número entero', 'Número decimal', 'Fecha', 'Opciones', 'Documento', 'Llave foránea']


def column_controls(conn, scope):
    name = st.text_input('Nombre del campo', key=f'{scope}_name')
    kind = st.selectbox('Tipo de campo', TYPES, key=f'{scope}_type')
    target = None
    options = None
    if kind == 'Llave foránea':
        targets = [r['table_name'] for r in list_registered_tables(conn)]
        target = st.selectbox('Tabla relacionada', targets, key=f'{scope}_target') if targets else None
        if not targets:
            st.info('Crea primero la tabla que deseas relacionar.')
    elif kind == 'Documento':
        target = 'document_records'
    elif kind == 'Opciones':
        options = st.text_area('Opciones permitidas (una por línea)', key=f'{scope}_options',
                               placeholder='Hombre\nMujer\nOtro')
        st.caption('El campo permite una opción por registro o dejarlo sin información.')
    return dict(name=name, type=kind, foreign_key_table=target, options=options)


def render_add_column(conn, table, username):
    scope = f'add_column_{table}'
    st.caption('La columna nueva quedará vacía en los registros existentes. El cambio queda registrado en Auditoría.')
    column = column_controls(conn, scope)
    if st.button('Agregar columna', key=f'{scope}_submit', type='primary'):
        try:
            name = add_dynamic_column(conn, table, column, username)
            st.session_state.pop(f'table_snapshot_{table}', None)
            st.session_state[f'table_generation_{table}'] = st.session_state.get(f'table_generation_{table}', 0) + 1
            st.session_state['table_notice'] = f'Columna {name} agregada a {table}.'
            st.rerun()
        except Exception as exc:
            conn.rollback()
            st.error(str(exc))
