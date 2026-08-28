"""Resumen principal de db_visualizer."""
import pandas as pd
import streamlit as st

import config
from sqlalchemy import text

from src.database import get_connection
from src.ocr_client import check_service_health

st.title("db_visualizer")
st.caption(f"Gestión documental, OCR y datos relacionados · versión {config.APP_VERSION}")

with get_connection() as conn:
    total_documents = int(conn.execute(text("SELECT COUNT(*) FROM document_records")).scalar_one())
    total_extractions = int(conn.execute(text("SELECT COUNT(*) FROM extraction_records")).scalar_one())
    pending_review = int(conn.execute(text(
        "SELECT COUNT(*) FROM extraction_records WHERE review_status != 'Validado'"
    )).scalar_one())
    ocr_ready = int(conn.execute(text(
        "SELECT COUNT(*) FROM document_records WHERE ocr_file_path IS NOT NULL"
    )).scalar_one())

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Documentos", total_documents)
    col2.metric("PDF con OCR", ocr_ready)
    col3.metric("Extracciones", total_extractions)
    col4.metric("Pendientes de revisión", pending_review)

    st.caption(f"Servicio OCR: {'disponible' if check_service_health() else 'no disponible'}")

    contributions = pd.read_sql_query(text('''
        SELECT created_by AS usuario, COUNT(*) AS fichas
        FROM extraction_records
        GROUP BY created_by
        ORDER BY fichas DESC
    '''), conn)

st.subheader("Actividad de extracción")
if contributions.empty:
    st.info("Aún no hay fichas de extracción.")
else:
    st.bar_chart(contributions.set_index("usuario"))
