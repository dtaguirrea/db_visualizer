from datetime import date

import pandas as pd
import pytest
from sqlalchemy import text

from src.csv_tables import import_csv, read_csv
from src.database import init_db
from src.dynamic_tables import add_dynamic_column, create_dynamic_table
from src.field_values import date_bounds, normalize_partial_date
from src.repository import (ConcurrentEditError, RecordInUseError, delete_records,
                            insert_record, sync_table_edits)
from src.search import search_documents
from src.session_state import clear_user_state


def test_logout_removes_snapshots_uploads_and_pending_forms():
    state = {'logged_in': True, 'username': 'ana', 'table_snapshot_personas': 'private',
             'csv_personas_file': b'private', 'new_table_columns': ['private'],
             'analytics_personas_genero_highlight': ['private'],
             'db_visualizer_auth_cookie_controller': {}}
    clear_user_state(state, {'logged_in': False, 'username': ''})
    assert state == {'logged_in': False, 'username': '', 'db_visualizer_auth_cookie_controller': {}}


def test_partial_date_bounds_do_not_expire_on_upload_day():
    year = date.today().year
    assert date_bounds(str(year)) == (f'{year}-01-01', f'{year}-12-31')
    assert normalize_partial_date('1932-05') == '1932-05'
    with pytest.raises(ValueError):
        normalize_partial_date('1932-02-30')


def test_filter_without_year_is_not_silently_ignored(conn):
    with pytest.raises(ValueError, match='año conocido'):
        search_documents(conn, 'test', date_from='????-05')


def test_csv_option_error_identifies_row_and_rolls_back(conn):
    create_dynamic_table(conn, 'personas', [
        {'name': 'genero', 'type': 'Opciones', 'options': ['Mujer', 'Hombre']}
    ], '', 'test')
    frame = read_csv(b'genero\nMujer\nInvalido\n')
    with pytest.raises(ValueError, match="Registro CSV 2, columna 'genero'"):
        import_csv(conn, frame, 'test', table_name='personas')
    assert conn.execute(text('SELECT COUNT(*) FROM personas')).scalar_one() == 0


def test_csv_partial_dates_and_add_column_keep_records(conn):
    create_dynamic_table(conn, 'personas', [{'name': 'fecha', 'type': 'Fecha'}], '', 'test')
    import_csv(conn, read_csv(b'fecha\n1932\n1932-05\n1932-05-17\n'), 'test', table_name='personas')
    add_dynamic_column(conn, 'personas', {'name': 'genero', 'type': 'Opciones', 'options': ['Mujer']}, 'test')
    rows = conn.execute(text('SELECT fecha, genero FROM personas ORDER BY record_id')).all()
    assert rows == [('1932', None), ('1932-05', None), ('1932-05-17', None)]
    assert conn.execute(text("SELECT COUNT(*) FROM audit_log WHERE action='ADD_COLUMN'")).scalar_one() == 1


def test_stale_edit_cannot_overwrite_another_user(conn):
    create_dynamic_table(conn, 'personas', [{'name': 'nombre', 'type': 'Texto'}], '', 'test')
    insert_record(conn, 'personas', {'nombre': 'Ana'}, 'test')
    original = pd.read_sql_query(text('SELECT * FROM personas'), conn)
    first = original.copy()
    first.loc[0, 'nombre'] = 'Ana María'
    sync_table_edits(conn, 'personas', original, first, 'editor1')
    second = original.copy()
    second.loc[0, 'nombre'] = 'Ana Isabel'
    with pytest.raises(ConcurrentEditError):
        sync_table_edits(conn, 'personas', original, second, 'editor2')
    assert conn.execute(text('SELECT nombre FROM personas')).scalar_one() == 'Ana María'


def test_cannot_delete_referenced_record(conn):
    create_dynamic_table(conn, 'personas', [{'name': 'nombre', 'type': 'Texto'}], '', 'test')
    pk = insert_record(conn, 'personas', {'nombre': 'Ana'}, 'test')
    create_dynamic_table(conn, 'vinculos', [
        {'name': 'persona', 'type': 'Llave foránea', 'foreign_key_table': 'personas'}
    ], '', 'test')
    insert_record(conn, 'vinculos', {'persona': pk}, 'test')
    with pytest.raises(RecordInUseError):
        delete_records(conn, 'personas', [{'pk': pk, 'updated_at': None}], 'test')


def test_migration_repairs_derived_dates_and_is_idempotent(database):
    year = date.today().year
    with database.begin() as conn:
        conn.execute(text("DELETE FROM schema_migrations WHERE version='3.1.0'"))
        conn.execute(text("INSERT INTO document_records (document_date, document_date_end, created_by, created_at) "
                          "VALUES (:year, :old, 'test', '2020')"), {'year': str(year), 'old': f'{year}-01-01'})
    init_db()
    init_db()
    with database.connect() as conn:
        row = conn.execute(text('SELECT document_date, document_date_end FROM document_records')).one()
        assert row == (str(year), f'{year}-12-31')
        assert conn.execute(text("SELECT COUNT(*) FROM schema_migrations WHERE version='3.1.0'")).scalar_one() == 1
