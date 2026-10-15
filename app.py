"""Entrada principal de db_visualizer."""
import streamlit as st
from src.session_state import clear_user_state
from streamlit_cookies_controller import CookieController

import config
from src.auth import (
    authenticate,
    authenticate_session,
    change_password,
    create_login_session,
    revoke_session,
    bootstrap_admin_if_empty,
)
from src.database import init_db
from src.ui import apply_ui
from src.navigation import register_pages

st.set_page_config(
    page_title=f"{config.APP_NAME} · {config.APP_VERSION}",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_ui()
@st.cache_resource
def initialize_application():
    init_db()
    bootstrap_admin_if_empty()


initialize_application()

HIDE_SIDEBAR_CSS = "<style>[data-testid='stSidebar']{display:none;}</style>"

SESSION_DEFAULTS = {
    "logged_in": False,
    "username": "",
    "user_id": None,
    "role": None,
    "full_name": "",
    "must_change_password": False,
    "auth_token": None,
    "reissue_auth_session": False,
}
for key, value in SESSION_DEFAULTS.items():
    st.session_state.setdefault(key, value)

cookie_controller = CookieController(key="db_visualizer_auth_cookie_controller")


def _apply_user(user: dict, token: str | None = None) -> None:
    st.session_state.logged_in = True
    st.session_state.username = user["username"]
    st.session_state.user_id = user["user_id"]
    st.session_state.role = user["role"]
    st.session_state.full_name = user.get("full_name") or user["username"]
    st.session_state.must_change_password = bool(user.get("must_change_password"))
    if token is not None:
        st.session_state.auth_token = token


def _browser_token() -> str | None:
    # st.context lee la cookie enviada al abrir una pestaña nueva; el controlador
    # sirve además como respaldo para la pestaña donde acaba de escribirse.
    try:
        token = st.context.cookies.get(config.AUTH_COOKIE_NAME)
    except Exception:
        token = None
    return token or cookie_controller.get(config.AUTH_COOKIE_NAME)


def _set_browser_token(token: str, expires_at) -> None:
    cookie_controller.set(
        config.AUTH_COOKIE_NAME,
        token,
        path="/",
        expires=expires_at,
        secure=config.AUTH_COOKIE_SECURE,
        same_site="lax",
    )


def _remove_browser_token() -> None:
    try:
        if cookie_controller.get(config.AUTH_COOKIE_NAME) is not None or _browser_token() is not None:
            cookie_controller.remove(
                config.AUTH_COOKIE_NAME,
                path="/",
                secure=config.AUTH_COOKIE_SECURE,
                same_site="lax",
            )
    except (KeyError, ValueError):
        pass


# Una pestaña nueva tiene otro st.session_state. Restauramos la identidad desde
# un token aleatorio guardado en cookie y validado contra PostgreSQL.
if not st.session_state.logged_in:
    saved_token = _browser_token()
    if saved_token:
        restored_user = authenticate_session(saved_token)
        if restored_user is not None:
            _apply_user(restored_user, saved_token)
        else:
            _remove_browser_token()

# Un cambio de contraseña revoca las sesiones anteriores. La pestaña que hizo
# el cambio recibe inmediatamente un token nuevo antes de validar la sesión.
if st.session_state.logged_in and st.session_state.reissue_auth_session:
    token, expires_at = create_login_session(st.session_state.user_id)
    _set_browser_token(token, expires_at)
    st.session_state.auth_token = token
    st.session_state.reissue_auth_session = False

# Validamos también las pestañas que ya estaban abiertas. Así un cierre de sesión
# o restablecimiento de contraseña invalida realmente los tokens revocados.
if st.session_state.logged_in:
    active_token = st.session_state.auth_token or _browser_token()
    active_user = authenticate_session(active_token) if active_token else None
    if active_user is None:
        _remove_browser_token()
        clear_user_state(st.session_state, SESSION_DEFAULTS)
        st.rerun()
    else:
        _apply_user(active_user, active_token)

# Registrar SIEMPRE las rutas, incluso cuando el usuario debe iniciar sesión.
# Ninguna página se ejecuta hasta superar las verificaciones de autenticación.
incoming_params = st.query_params.to_dict()
current_page = register_pages()

if not st.session_state.logged_in:
    if incoming_params or "login_return_to" not in st.session_state:
        st.session_state["login_return_to"] = (current_page, incoming_params)
    st.markdown(HIDE_SIDEBAR_CSS, unsafe_allow_html=True)
    _, center, _ = st.columns([1, 1.15, 1])
    with center:
        st.title("db_visualizer")
        st.caption("Plataforma colaborativa de gestión documental")
        with st.form("login_form"):
            username = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button("Iniciar sesión", use_container_width=True)
            if submitted:
                user = authenticate(username, password) if username and password else None
                if user is None:
                    st.error("Usuario o contraseña incorrectos.")
                else:
                    token, expires_at = create_login_session(user["user_id"])
                    _set_browser_token(token, expires_at)
                    _apply_user(user, token)
                    st.rerun()
    st.stop()

if st.session_state.must_change_password:
    st.markdown(HIDE_SIDEBAR_CSS, unsafe_allow_html=True)
    _, center, _ = st.columns([1, 1.15, 1])
    with center:
        st.title("Actualizar contraseña")
        with st.form("forced_password_change"):
            new_password = st.text_input("Nueva contraseña", type="password")
            confirm_password = st.text_input("Confirmar contraseña", type="password")
            submitted = st.form_submit_button("Guardar", use_container_width=True)
            if submitted:
                if len(new_password) < config.MIN_PASSWORD_LENGTH:
                    st.error(f"La contraseña debe tener al menos {config.MIN_PASSWORD_LENGTH} caracteres.")
                elif new_password != confirm_password:
                    st.error("Las contraseñas no coinciden.")
                else:
                    change_password(st.session_state.user_id, new_password)
                    token, expires_at = create_login_session(st.session_state.user_id)
                    _set_browser_token(token, expires_at)
                    st.session_state.auth_token = token
                    st.session_state.must_change_password = False
                    st.rerun()
    st.stop()

return_to = st.session_state.pop("login_return_to", None)
if return_to:
    target_page, target_params = return_to
    if current_page.url_path != target_page.url_path:
        st.switch_page(target_page, query_params=target_params)
    st.query_params.from_dict(target_params)

with st.sidebar:
    st.markdown(f"**{st.session_state.full_name}**")
    st.caption(f"{st.session_state.username} · {st.session_state.role}")
    st.caption(f"db_visualizer {config.APP_VERSION}")
    if st.button("Cerrar sesión", use_container_width=True):
        revoke_session(st.session_state.auth_token or _browser_token())
        _remove_browser_token()
        clear_user_state(st.session_state, SESSION_DEFAULTS)
        st.rerun()
    st.divider()

# Alias de compatibilidad para enlaces antiguos en la raíz.
if current_page.url_path == "" and st.query_params.get("document_id") is not None:
    st.switch_page("pages/document_viewer.py", query_params=st.query_params.to_dict())
if current_page.url_path == "" and st.query_params.get("table") is not None:
    st.switch_page("pages/data_explorer.py", query_params=st.query_params.to_dict())

current_page.run()
