"""Limpieza del estado privado al terminar una sesión autenticada."""


def clear_user_state(state, defaults):
    # Conserva únicamente el componente que acaba de retirar la cookie.
    # Elimina snapshots, editores, CSV subidos, búsquedas y formularios pendientes.
    for key in list(state):
        if key != 'db_visualizer_auth_cookie_controller':
            del state[key]
    for key, value in defaults.items():
        state[key] = value
