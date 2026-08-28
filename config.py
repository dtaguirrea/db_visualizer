"""Configuración central de db_visualizer."""
import os
from pathlib import Path
from urllib.parse import quote_plus

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = "db_visualizer"
APP_VERSION = (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
APP_ENV = os.environ.get("APP_ENV", "production")
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BASE_DIR / "uploaded_files"))
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", DATA_DIR / "db_visualizer.db"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# PostgreSQL es el backend recomendado para despliegues colaborativos.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL and os.environ.get("DB_HOST"):
    db_user = quote_plus(os.environ.get("DB_USER", "db_visualizer"))
    db_password = quote_plus(os.environ.get("DB_PASSWORD", ""))
    db_host = os.environ["DB_HOST"]
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = quote_plus(os.environ.get("DB_NAME", "db_visualizer"))
    DATABASE_URL = f"postgresql+psycopg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
if not DATABASE_URL:
    DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

OCR_SERVICE_URL = os.environ.get("OCR_SERVICE_URL", "http://localhost:5060")
OCR_TIMEOUT_SECONDS = int(os.environ.get("OCR_TIMEOUT_SECONDS", "600"))
OCR_USE_FAST_MODE = os.environ.get("OCR_USE_FAST_MODE", "false").lower() == "true"
OCR_LANGUAGE = os.environ.get("OCR_LANGUAGE", "es")
OCR_ROTATE_PAGES = os.environ.get("OCR_ROTATE_PAGES", "true").lower() == "true"
OCR_DESKEW = os.environ.get("OCR_DESKEW", "true").lower() == "true"

BOOTSTRAP_ADMIN_USERNAME = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin").strip()
BOOTSTRAP_ADMIN_FULL_NAME = os.environ.get("BOOTSTRAP_ADMIN_FULL_NAME", "Administrador").strip()
BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")

# Sesiones persistentes entre pestañas. El navegador guarda únicamente un token aleatorio;
# la base de datos conserva su hash para que el token no quede almacenado en claro.
AUTH_COOKIE_NAME = os.environ.get("AUTH_COOKIE_NAME", "db_visualizer_session")
AUTH_SESSION_DAYS = max(1, int(os.environ.get("AUTH_SESSION_DAYS", "7")))
AUTH_COOKIE_SECURE = os.environ.get("AUTH_COOKIE_SECURE", "false").lower() == "true"

SEARCH_RESULT_LIMIT = max(10, int(os.environ.get("SEARCH_RESULT_LIMIT", "100")))
MIN_PASSWORD_LENGTH = max(12, int(os.environ.get("MIN_PASSWORD_LENGTH", "12")))

ELEMENT_CATEGORIES = [
    "Hardware",
    "Software",
    "Procesos tecnológicos",
    "Personas-redes",
    "Organizaciones",
    "Hitos",
    "Sistemas estadísticos",
    "Discursos",
]

ROLE_ADMIN = "admin"
ROLE_RESEARCHER = "investigador"
VALID_ROLES = (ROLE_ADMIN, ROLE_RESEARCHER)

PROTECTED_TABLES = {
    "users",
    "auth_sessions",
    "document_records",
    "extraction_records",
    "dynamic_tables_registry",
    "dynamic_table_columns",
    "audit_log",
}
