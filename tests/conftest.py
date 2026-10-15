import pytest
import config
from src.database import get_engine, init_db


@pytest.fixture
def database(tmp_path, monkeypatch):
    get_engine.cache_clear()
    monkeypatch.setattr(config, 'DATABASE_URL', f'sqlite:///{tmp_path / "test.db"}')
    monkeypatch.setattr(config, 'UPLOAD_DIR', tmp_path / 'pdfs')
    config.UPLOAD_DIR.mkdir()
    init_db()
    engine = get_engine()
    yield engine
    engine.dispose()
    get_engine.cache_clear()


@pytest.fixture
def conn(database):
    with database.connect() as connection:
        yield connection
