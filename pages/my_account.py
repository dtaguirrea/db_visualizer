"""Cuenta y administración de usuarios."""
import streamlit as st

import config
from src.auth import change_password, create_user, list_users, reset_password

if not st.session_state.get("logged_in"):
    st.stop()

st.title("Cuenta")
st.caption(f"{st.session_state.full_name} · {st.session_state.username} · {st.session_state.role}")

with st.form("change_own_password_form", clear_on_submit=True):
    st.subheader("Cambiar contraseña")
    new_password = st.text_input("Nueva contraseña", type="password")
    confirm_password = st.text_input("Confirmar contraseña", type="password")
    submitted = st.form_submit_button("Actualizar")
    if submitted:
        if len(new_password) < config.MIN_PASSWORD_LENGTH:
            st.error(f"La contraseña debe tener al menos {config.MIN_PASSWORD_LENGTH} caracteres.")
        elif new_password != confirm_password:
            st.error("Las contraseñas no coinciden.")
        else:
            change_password(st.session_state.user_id, new_password)
            st.session_state.reissue_auth_session = True
            st.success("Contraseña actualizada.")
            st.rerun()

if st.session_state.role == config.ROLE_ADMIN:
    st.divider()
    st.subheader("Equipo")
    st.dataframe(list_users(), use_container_width=True, hide_index=True)

    col_create, col_reset = st.columns(2)
    with col_create:
        with st.form("create_user_form", clear_on_submit=True):
            st.markdown("**Agregar usuario**")
            username = st.text_input("Usuario")
            full_name = st.text_input("Nombre completo")
            role = st.selectbox("Rol", config.VALID_ROLES)
            temp_password = st.text_input("Contraseña temporal", type="password")
            create = st.form_submit_button("Crear")
        if create:
            if not username.strip() or " " in username.strip():
                st.error("El usuario no puede contener espacios.")
            else:
                try:
                    create_user(username, full_name, role, temp_password)
                    st.success("Usuario creado.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"No fue posible crear el usuario: {exc}")

    with col_reset:
        user_rows = list_users()
        options = {f"{u['username']} · {u['full_name']}": u["user_id"] for u in user_rows}
        with st.form("reset_password_form", clear_on_submit=True):
            st.markdown("**Restablecer contraseña**")
            selected = st.selectbox("Usuario", list(options.keys()))
            temp_password = st.text_input("Nueva contraseña temporal", type="password")
            reset = st.form_submit_button("Restablecer")
        if reset:
            try:
                target_user_id = options[selected]
                reset_password(target_user_id, temp_password)
                if target_user_id == st.session_state.user_id:
                    st.session_state.reissue_auth_session = True
                st.success("Contraseña restablecida.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
