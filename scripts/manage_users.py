"""Administración de usuarios desde línea de comandos."""
import sys
from pathlib import Path
from sqlalchemy import text

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.auth import create_user, list_users, reset_password  # noqa: E402
from src.database import get_connection, init_db  # noqa: E402


def _find_user_id(username: str):
    with get_connection() as conn:
        return conn.execute(
            text("SELECT user_id FROM users WHERE username = :username"),
            {"username": username},
        ).scalar_one_or_none()


def main() -> None:
    init_db()
    if len(sys.argv) < 2:
        print(__doc__)
        return
    command = sys.argv[1]
    if command == "list":
        for user in list_users():
            print(f"{user['username']} | {user['full_name']} | {user['role']}")
    elif command == "add" and len(sys.argv) == 6:
        _, _, username, full_name, role, temp_password = sys.argv
        create_user(username, full_name, role, temp_password)
        print(f"Usuario '{username}' creado.")
    elif command == "reset-password" and len(sys.argv) == 4:
        _, _, username, temp_password = sys.argv
        user_id = _find_user_id(username)
        if user_id is None:
            print(f"No existe el usuario '{username}'.")
            return
        reset_password(user_id, temp_password)
        print(f"Contraseña de '{username}' restablecida.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
