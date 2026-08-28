"""Estilos compartidos para una interfaz sobria y consistente."""
import streamlit as st


def apply_ui() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1500px;}
        h1 {font-size: 2rem !important; letter-spacing: -0.02em;}
        h2 {font-size: 1.35rem !important;}
        h3 {font-size: 1.05rem !important;}
        [data-testid="stSidebar"] {border-right: 1px solid rgba(128,128,128,.18);}
        [data-testid="stMetric"] {border: 1px solid rgba(128,128,128,.20); padding: 1rem; border-radius: .45rem;}
        div[data-testid="stForm"] {border: 1px solid rgba(128,128,128,.20); border-radius: .45rem; padding: 1rem;}
        .stAlert {border-radius: .35rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )
