"""Autenticación, sesiones persistentes y gestión de usuarios."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe

import bcrypt
from sqlalchemy import text

import config
from src.database import get_engine, utc_now


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _hash_session_token(raw_token: str) -> str:
    return sha256(raw_token.encode("utf-8")).hexdigest()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def bootstrap_admin_if_empty() -> str | None:
    """Creates the initial administrator only when the user table is empty.

    The password is supplied through the environment and is never logged or
    returned by this function. The administrator must change it on first login.
    """
    with get_engine().begin() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
        if total > 0:
            return None

        username = config.BOOTSTRAP_ADMIN_USERNAME
        full_name = config.BOOTSTRAP_ADMIN_FULL_NAME or username
        password = config.BOOTSTRAP_ADMIN_PASSWORD
        if not username:
            raise RuntimeError("BOOTSTRAP_ADMIN_USERNAME no puede estar vacío.")
        if len(password) < config.MIN_PASSWORD_LENGTH:
            raise RuntimeError(
                f"BOOTSTRAP_ADMIN_PASSWORD debe tener al menos {config.MIN_PASSWORD_LENGTH} caracteres antes del primer inicio."
            )

        conn.execute(text("""
            INSERT INTO users (username, full_name, password_hash, role, must_change_password, created_at)
            VALUES (:username, :full_name, :password_hash, :role, 1, :created_at)
        """), {
            "username": username,
            "full_name": full_name,
            "password_hash": hash_password(password),
            "role": config.ROLE_ADMIN,
            "created_at": utc_now(),
        })
        return username


def authenticate(username: str, plain_password: str):
    with get_engine().begin() as conn:
        row = conn.execute(text("SELECT * FROM users WHERE username = :username"),
                           {"username": username.strip()}).mappings().first()
        if row is None or not verify_password(plain_password, row["password_hash"]):
            return None
        conn.execute(text("UPDATE users SET last_login_at = :now WHERE user_id = :user_id"),
                     {"now": utc_now(), "user_id": row["user_id"]})
        return dict(row)


def create_login_session(user_id: int, days: int | None = None) -> tuple[str, datetime]:
    """Crea un token de sesión. Solo su hash se persiste en la base de datos."""
    raw_token = token_urlsafe(48)
    token_hash = _hash_session_token(raw_token)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=days or config.AUTH_SESSION_DAYS)
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM auth_sessions WHERE expires_at <= :now"), {"now": now.isoformat()})
        conn.execute(text('''
            INSERT INTO auth_sessions (user_id, token_hash, created_at, expires_at, last_seen_at)
            VALUES (:user_id, :token_hash, :created_at, :expires_at, :last_seen_at)
        '''), {
            "user_id": user_id,
            "token_hash": token_hash,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "last_seen_at": now.isoformat(),
        })
    return raw_token, expires_at


def authenticate_session(raw_token: str | None):
    """Restaura un usuario desde un token válido y no expirado."""
    if not raw_token or not isinstance(raw_token, str):
        return None
    token_hash = _hash_session_token(raw_token)
    with get_engine().begin() as conn:
        row = conn.execute(text('''
            SELECT s.session_id, s.expires_at,
                   u.user_id, u.username, u.full_name, u.role, u.must_change_password,
                   u.created_at, u.last_login_at
            FROM auth_sessions s
            JOIN users u ON u.user_id = s.user_id
            WHERE s.token_hash = :token_hash
        '''), {"token_hash": token_hash}).mappings().first()
        if row is None:
            return None
        expires_at = _parse_datetime(row["expires_at"])
        now = datetime.now(timezone.utc)
        if expires_at is None or expires_at <= now:
            conn.execute(text("DELETE FROM auth_sessions WHERE session_id = :session_id"),
                         {"session_id": row["session_id"]})
            return None
        conn.execute(text("UPDATE auth_sessions SET last_seen_at = :now WHERE session_id = :session_id"),
                     {"now": now.isoformat(), "session_id": row["session_id"]})
        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "full_name": row["full_name"],
            "role": row["role"],
            "must_change_password": row["must_change_password"],
            "created_at": row["created_at"],
            "last_login_at": row["last_login_at"],
        }


def revoke_session(raw_token: str | None) -> None:
    if not raw_token:
        return
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM auth_sessions WHERE token_hash = :token_hash"),
                     {"token_hash": _hash_session_token(raw_token)})


def revoke_user_sessions(user_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM auth_sessions WHERE user_id = :user_id"), {"user_id": user_id})


def change_password(user_id: int, new_password: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text('''UPDATE users SET password_hash = :password_hash, must_change_password = 0
                             WHERE user_id = :user_id'''),
                     {"password_hash": hash_password(new_password), "user_id": user_id})
        # Un cambio de contraseña invalida las demás pestañas/dispositivos.
        conn.execute(text("DELETE FROM auth_sessions WHERE user_id = :user_id"), {"user_id": user_id})


def list_users() -> list:
    with get_engine().connect() as conn:
        rows = conn.execute(text('''
            SELECT user_id, username, full_name, role, must_change_password, created_at, last_login_at
            FROM users ORDER BY username
        ''')).mappings().all()
        return [dict(row) for row in rows]


def create_user(username: str, full_name: str, role: str, temp_password: str) -> None:
    if len(temp_password) < config.MIN_PASSWORD_LENGTH:
        raise ValueError(f"La contraseña temporal debe tener al menos {config.MIN_PASSWORD_LENGTH} caracteres.")
    with get_engine().begin() as conn:
        conn.execute(text('''
            INSERT INTO users (username, full_name, password_hash, role, must_change_password, created_at)
            VALUES (:username, :full_name, :password_hash, :role, 1, :created_at)
        '''), {
            "username": username.strip(),
            "full_name": full_name.strip(),
            "password_hash": hash_password(temp_password),
            "role": role,
            "created_at": utc_now(),
        })


def reset_password(user_id: int, temp_password: str) -> None:
    if len(temp_password) < config.MIN_PASSWORD_LENGTH:
        raise ValueError(f"La contraseña temporal debe tener al menos {config.MIN_PASSWORD_LENGTH} caracteres.")
    with get_engine().begin() as conn:
        conn.execute(text('''UPDATE users SET password_hash = :password_hash, must_change_password = 1
                             WHERE user_id = :user_id'''),
                     {"password_hash": hash_password(temp_password), "user_id": user_id})
        conn.execute(text("DELETE FROM auth_sessions WHERE user_id = :user_id"), {"user_id": user_id})
