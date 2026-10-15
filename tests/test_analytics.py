import pandas as pd
import pytest
from sqlalchemy import text

from src.analytics import (annual_frame, compact_frequencies, frequency_frame,
    grouped_values, relation_catalog, relation_ranking, table_catalog)
from src.dynamic_tables import create_dynamic_table
from src.repository import insert_record


def people(conn):
    create_dynamic_table(conn, 'personas', [
        {'name': 'genero', 'type': 'Opciones', 'options': ['Mujer', 'Hombre', 'Otro']},
        {'name': 'pais', 'type': 'Texto'},
        {'name': 'fecha', 'type': 'Fecha'},
    ], '', 'test')
    for gender, country in [('Mujer', 'Chile'), ('Mujer', 'Perú'), ('Hombre', 'Chile'), (None, 'Chile')]:
        insert_record(conn, 'personas', {'genero': gender, 'pais': country}, 'test')


def test_percentages_include_missing_and_change_denominator(conn):
    people(conn)
    rows = grouped_values(conn, 'personas', 'genero')
    full = frequency_frame(rows).set_index('Categoría')
    assert full.loc['Mujer', 'Cantidad'] == 2
    assert full.loc['Mujer', 'Porcentaje'] == 50
    assert full.loc['Sin información', 'Porcentaje'] == 25
    known = frequency_frame(rows, include_missing=False).set_index('Categoría')
    assert known.loc['Mujer', 'Porcentaje'] == pytest.approx(200 / 3)
    assert known['Porcentaje'].sum() == pytest.approx(100)


def test_filter_is_applied_before_percentages(conn):
    people(conn)
    rows = grouped_values(conn, 'personas', 'genero', filter_column='pais', filter_value='Chile')
    assert sum(r['quantity'] for r in rows) == 3
    assert frequency_frame(rows).set_index('Categoría').loc['Mujer', 'Porcentaje'] == pytest.approx(100 / 3)
    assert grouped_values(conn, 'personas', 'genero', filter_column='pais', filter_value="Chile' OR 1=1 --") == []


def test_null_filter_and_empty_table(conn):
    people(conn)
    rows = grouped_values(conn, 'personas', 'pais', filter_column='genero', filter_value=None)
    assert rows == [{'value': 'Chile', 'quantity': 1}]
    assert grouped_values(conn, 'document_records', 'author') == []
    assert frequency_frame([]).empty


def test_no_access_to_users_or_raw_ocr(conn):
    assert 'users' not in table_catalog(conn)
    with pytest.raises(ValueError):
        grouped_values(conn, 'users', 'password_hash')
    with pytest.raises(ValueError):
        grouped_values(conn, 'document_records', 'ocr_text')
    with pytest.raises(ValueError):
        grouped_values(conn, 'document_records; DROP TABLE users', 'author')


def test_limit_does_not_return_misleading_partial_totals(conn):
    people(conn)
    with pytest.raises(ValueError, match='muestra incompleta'):
        grouped_values(conn, 'personas', 'genero', limit=1)


def test_top_categories_keep_total_and_percentages():
    frame = frequency_frame([{'value': str(i), 'quantity': i + 1} for i in range(15)])
    compact = compact_frequencies(frame, 10)
    assert len(compact) == 11
    assert compact['Cantidad'].sum() == 120
    assert compact['Porcentaje'].sum() == pytest.approx(100)
    assert compact.iloc[-1]['Categoría'] == 'Otras categorías (agrupadas)'


def test_partial_dates_and_exclusions():
    rows = [{'value': v, 'quantity': n} for v, n in [
        ('1932', 3), ('1932-05', 2), ('1932-05-17', 1), ('1933', 4),
        ('????-06', 2), (None, 5), ('1932-02-30', 1)]]
    frame, excluded = annual_frame(rows)
    assert frame.to_dict('records') == [{'Año': 1932, 'Cantidad': 6}, {'Año': 1933, 'Cantidad': 4}]
    assert excluded == {'Sin fecha': 5, 'Sin año conocido': 2, 'Fecha inválida': 1}
    assert frame['Cantidad'].sum() + sum(excluded.values()) == 18


def test_relation_distinct_entities_and_zero_links(conn):
    for title in ['Documento A', 'Documento B']:
        conn.execute(text("INSERT INTO document_records (title, created_by, created_at) VALUES (:title, 'test', '2020')"), {'title': title})
    conn.commit()
    create_dynamic_table(conn, 'personas', [{'name': 'nombre', 'type': 'Texto'}], '', 'test')
    p1 = insert_record(conn, 'personas', {'nombre': 'Ana'}, 'test')
    p2 = insert_record(conn, 'personas', {'nombre': 'Berta'}, 'test')
    create_dynamic_table(conn, 'menciones', [
        {'name': 'persona', 'type': 'Llave foránea', 'foreign_key_table': 'personas'},
        {'name': 'documento', 'type': 'Documento'},
    ], '', 'test')
    for person in [p1, p1, p2, None]:
        insert_record(conn, 'menciones', {'persona': person, 'documento': 1}, 'test')
    relation = next(r for r in relation_catalog(conn) if r['target'] == 'document_records')
    assert relation_ranking(conn, relation)['Cantidad'].tolist() == [4, 0]
    distinct = relation_ranking(conn, relation, distinct_column='persona')
    assert distinct['Cantidad'].tolist() == [2, 0]
    assert 'Documento A' in distinct.iloc[0]['Registro']
    with pytest.raises(ValueError):
        relation_ranking(conn, relation, distinct_column='created_by')


def test_blank_values_and_literal_missing_label(conn):
    create_dynamic_table(conn, 'organizaciones', [{'name': 'tipo', 'type': 'Texto'}], '', 'test')
    for value in [None, '  ', 'Sin información', 'Sindicato']:
        conn.execute(text("INSERT INTO organizaciones (tipo, created_by, created_at) VALUES (:v, 'test', '2020')"), {'v': value})
    conn.commit()
    frame = frequency_frame(grouped_values(conn, 'organizaciones', 'tipo')).set_index('Categoría')
    assert frame.loc['Sin información', 'Cantidad'] == 2
    assert frame.loc['Sin información (valor registrado)', 'Cantidad'] == 1
