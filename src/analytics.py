"""Consultas de solo lectura para gráficos de tablas registradas y documentos.

Los identificadores proceden del esquema; los filtros son parámetros SQL.
Se agrupa en la base de datos y nunca se carga el texto OCR para graficar.
"""
import unicodedata
from collections import Counter

import pandas as pd
from sqlalchemy import inspect, text

from src.database import get_dynamic_column_metadata, get_primary_key_column
from src.dynamic_tables import list_registered_tables
from src.field_values import normalize_partial_date

DOCUMENT_COLUMNS = {
    'title': ('Título', 'Texto'),
    'author': ('Autor', 'Texto'),
    'source_reference': ('Procedencia', 'Texto'),
    'document_date': ('Fecha del documento', 'Fecha'),
    'original_format': ('Formato', 'Texto'),
    'ocr_language': ('Idioma configurado para OCR', 'Texto'),
    'ocr_status': ('Estado OCR', 'Texto'),
    'physical_location': ('Localización física', 'Texto'),
}


def normalized_name(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value)).casefold()
                   if not unicodedata.combining(c))


def table_catalog(conn):
    existing = set(inspect(conn).get_table_names())
    result = {'document_records': 'Documentos'}
    result.update({r['table_name']: r['table_name'] for r in list_registered_tables(conn)
                   if r['table_name'] in existing})
    return result


def column_catalog(conn, table):
    if table not in table_catalog(conn):
        raise ValueError('Selecciona una tabla registrada o Documentos.')
    physical = {c['name'] for c in inspect(conn).get_columns(table)}
    if table == 'document_records':
        return {name: {'label': label, 'kind': kind} for name, (label, kind)
                in DOCUMENT_COLUMNS.items() if name in physical}
    return {r['column_name']: {'label': r['column_name'], 'kind': r['column_type']}
            for r in get_dynamic_column_metadata(conn, table) if r['column_name'] in physical}


def _category_expression(conn, column):
    quote = conn.dialect.identifier_preparer.quote
    return f"NULLIF(TRIM(CAST({quote(column)} AS TEXT)), '')"


def grouped_values(conn, table, column, *, filter_column=None, filter_value=None, limit=1000):
    columns = column_catalog(conn, table)
    if column not in columns:
        raise ValueError('La columna no está disponible para gráficos.')
    quote = conn.dialect.identifier_preparer.quote
    expression = _category_expression(conn, column)
    where, params = '', {'limit': limit + 1}
    if filter_column is not None:
        if filter_column not in columns:
            raise ValueError('Columna de filtro no válida.')
        filter_expression = _category_expression(conn, filter_column)
        if filter_value is None:
            where = f'WHERE {filter_expression} IS NULL'
        else:
            where = f'WHERE {filter_expression} = :filter_value'
            params['filter_value'] = str(filter_value)
    rows = conn.execute(text(
        f'SELECT {expression} AS value, COUNT(*) AS quantity FROM {quote(table)} '
        f'{where} GROUP BY {expression} ORDER BY quantity DESC, value ASC LIMIT :limit'
    ), params).mappings().all()
    if len(rows) > limit:
        raise ValueError(f'La columna supera {limit} valores diferentes. Elige otra columna '
                         'o aplica un filtro; no se muestran porcentajes de una muestra incompleta.')
    return [dict(row) for row in rows]


def category_label(value):
    # Diferencia una celda NULL del texto literal escrito por un investigador.
    if value is None:
        return 'Sin información'
    if value in ('Sin información', 'Otras categorías (agrupadas)'):
        return f'{value} (valor registrado)'
    return str(value)


def frequency_frame(rows, *, include_missing=True):
    selected = [r for r in rows if include_missing or r['value'] is not None]
    total = sum(r['quantity'] for r in selected)
    return pd.DataFrame([
        {'Categoría': category_label(r['value']), 'Cantidad': int(r['quantity']),
         'Porcentaje': 100 * r['quantity'] / total if total else 0}
        for r in selected
    ], columns=['Categoría', 'Cantidad', 'Porcentaje'])


def compact_frequencies(frame, top_n):
    """Conserva el denominador al agrupar la cola, incluso en un gráfico circular."""
    ordered = frame.sort_values(['Cantidad', 'Categoría'], ascending=[False, True])
    if len(ordered) <= top_n:
        return ordered.reset_index(drop=True)
    head, tail = ordered.iloc[:top_n].copy(), ordered.iloc[top_n:]
    other = pd.DataFrame([{'Categoría': 'Otras categorías (agrupadas)',
                           'Cantidad': int(tail['Cantidad'].sum()),
                           'Porcentaje': tail['Porcentaje'].sum()}])
    return pd.concat([head, other], ignore_index=True)


def annual_frame(rows):
    counts = Counter()
    excluded = {'Sin fecha': 0, 'Sin año conocido': 0, 'Fecha inválida': 0}
    for row in rows:
        value, count = row['value'], row['quantity']
        if value is None:
            excluded['Sin fecha'] += count
            continue
        try:
            raw = normalize_partial_date(value)
        except (ValueError, TypeError):
            excluded['Fecha inválida'] += count
            continue
        if raw.startswith('????'):
            excluded['Sin año conocido'] += count
        else:
            counts[int(raw[:4])] += count
    frame = pd.DataFrame([{'Año': year, 'Cantidad': counts[year]} for year in sorted(counts)],
                         columns=['Año', 'Cantidad'])
    return frame, excluded


def relation_catalog(conn):
    """Solo relaciones FK simples verificadas físicamente; no infiere coincidencias."""
    allowed = table_catalog(conn)
    result = []
    inspector = inspect(conn)
    for source in allowed:
        for fk in inspector.get_foreign_keys(source):
            target = fk.get('referred_table')
            local, remote = fk.get('constrained_columns', []), fk.get('referred_columns', [])
            if target in allowed and len(local) == len(remote) == 1:
                result.append({'source': source, 'column': local[0],
                               'target': target, 'target_column': remote[0]})
    return result


def relation_ranking(conn, relation, *, distinct_column=None, limit=20):
    if relation not in relation_catalog(conn):
        raise ValueError('La relación seleccionada ya no existe.')
    source, target = relation['source'], relation['target']
    quote = conn.dialect.identifier_preparer.quote
    columns = column_catalog(conn, target)
    preferred = ('title', 'nombre', 'nombres', 'nombre_completo', 'razon_social')
    label = next((c for c in preferred if c in columns), None)
    if label is None:
        label = next((c for c, meta in columns.items() if meta['kind'] == 'Texto'), None)
    label_expression = f't.{quote(label)}' if label else 'NULL'
    pk = get_primary_key_column(conn, target)
    source_pk = get_primary_key_column(conn, source)
    count_expression = f'COUNT(s.{quote(source_pk)})'
    if distinct_column:
        eligible = {r['column'] for r in relation_catalog(conn)
                    if r['source'] == source and r['column'] != relation['column']}
        if distinct_column not in eligible:
            raise ValueError('Para contar entidades distintas selecciona otra llave foránea de la tabla de origen.')
        count_expression = f'COUNT(DISTINCT s.{quote(distinct_column)})'
    group = f't.{quote(pk)}' + (f', {label_expression}' if label else '')
    rows = conn.execute(text(
        f'SELECT t.{quote(pk)} AS id, {label_expression} AS label, {count_expression} AS quantity '
        f'FROM {quote(target)} t LEFT JOIN {quote(source)} s '
        f'ON s.{quote(relation["column"])} = t.{quote(relation["target_column"])} '
        f'GROUP BY {group} ORDER BY quantity DESC, t.{quote(pk)} ASC LIMIT :limit'
    ), {'limit': int(limit)}).mappings().all()
    return pd.DataFrame([
        {'Registro': f'{r["id"]} · {r["label"] or "Sin nombre"}', 'Cantidad': int(r['quantity'])}
        for r in rows
    ], columns=['Registro', 'Cantidad'])
