"""Resumen documental."""
import streamlit as st
from sqlalchemy import text
import config
from src.database import get_connection
from src.ocr_client import check_service_health
from src.dynamic_tables import list_registered_tables

if not st.session_state.get('logged_in'):
    st.stop()
st.title('db_visualizer')
st.caption(f'Gestión documental y datos relacionados · versión {config.APP_VERSION}')
with get_connection() as conn:
    total = conn.execute(text('SELECT COUNT(*) FROM document_records')).scalar_one()
    ready = conn.execute(text('SELECT COUNT(*) FROM document_records WHERE ocr_file_path IS NOT NULL')).scalar_one()
    queued = conn.execute(text("SELECT COUNT(*) FROM document_records WHERE ocr_status IN ('en_cola', 'procesando')")).scalar_one()
    tables = len(list_registered_tables(conn))
for col, label, value in zip(st.columns(4), ['Documentos', 'PDF con OCR', 'En cola o procesando', 'Tablas personalizadas'], [total, ready, queued, tables]):
    col.metric(label, value)
st.caption(f"Servicio OCR: {'disponible' if check_service_health() else 'no disponible'}")
