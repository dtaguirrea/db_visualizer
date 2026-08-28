"""Búsqueda global de documentos, extracciones y tablas personalizadas."""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

import config
from src.database import get_dynamic_column_metadata, get_primary_key_column, is_postgres
from src.dynamic_tables import list_registered_tables

AUDIT_COLUMNS = {"created_by", "created_at", "updated_by", "updated_at"}


def _quote_identifier(conn: Connection, identifier: str) -> str:
    return conn.dialect.identifier_preparer.quote(identifier)


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_documents(conn: Connection, query: str, limit: int | None = None) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    limit = limit or config.SEARCH_RESULT_LIMIT
    params = {"query": query, "pattern": _like_pattern(query), "limit": limit}

    common_select = '''
        SELECT document_id, title, source_reference, document_date, original_format,
               ocr_status, ocr_text, created_by, created_at
        FROM document_records
    '''
    if is_postgres(conn):
        rows = conn.execute(text(common_select + '''
            WHERE
                to_tsvector(
                    'spanish',
                    COALESCE(title, '') || ' ' ||
                    COALESCE(source_reference, '') || ' ' ||
                    COALESCE(document_date, '') || ' ' ||
                    COALESCE(original_format, '') || ' ' ||
                    COALESCE(ocr_text, '')
                ) @@ websearch_to_tsquery('spanish', :query)
                OR LOWER(COALESCE(title, '')) LIKE LOWER(:pattern) ESCAPE '\\'
                OR LOWER(COALESCE(source_reference, '')) LIKE LOWER(:pattern) ESCAPE '\\'
                OR LOWER(COALESCE(document_date, '')) LIKE LOWER(:pattern) ESCAPE '\\'
                OR LOWER(COALESCE(original_format, '')) LIKE LOWER(:pattern) ESCAPE '\\'
                OR LOWER(COALESCE(ocr_text, '')) LIKE LOWER(:pattern) ESCAPE '\\'
            ORDER BY
                CASE
                    WHEN LOWER(COALESCE(title, '')) LIKE LOWER(:pattern) ESCAPE '\\' THEN 0
                    WHEN LOWER(COALESCE(source_reference, '')) LIKE LOWER(:pattern) ESCAPE '\\' THEN 1
                    ELSE 2
                END,
                document_id DESC
            LIMIT :limit
        '''), params).mappings().all()
    else:
        rows = conn.execute(text(common_select + '''
            WHERE
                LOWER(COALESCE(title, '')) LIKE LOWER(:pattern) ESCAPE '\\'
                OR LOWER(COALESCE(source_reference, '')) LIKE LOWER(:pattern) ESCAPE '\\'
                OR LOWER(COALESCE(document_date, '')) LIKE LOWER(:pattern) ESCAPE '\\'
                OR LOWER(COALESCE(original_format, '')) LIKE LOWER(:pattern) ESCAPE '\\'
                OR LOWER(COALESCE(ocr_text, '')) LIKE LOWER(:pattern) ESCAPE '\\'
            ORDER BY document_id DESC
            LIMIT :limit
        '''), params).mappings().all()
    return [dict(row) for row in rows]


def search_extractions(conn: Connection, query: str, limit: int | None = None) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    limit = limit or config.SEARCH_RESULT_LIMIT
    pattern = _like_pattern(query)
    operator = "ILIKE" if is_postgres(conn) else "LIKE"
    sql = f'''
        SELECT e.extraction_id, e.document_id, e.element_category, e.key_information,
               e.literal_quote, e.cross_reference_notes, e.review_status,
               e.created_by, e.created_at, d.title, d.source_reference, d.document_date
        FROM extraction_records e
        JOIN document_records d ON d.document_id = e.document_id
        WHERE
            COALESCE(e.element_category, '') {operator} :pattern ESCAPE '\\'
            OR COALESCE(e.key_information, '') {operator} :pattern ESCAPE '\\'
            OR COALESCE(e.literal_quote, '') {operator} :pattern ESCAPE '\\'
            OR COALESCE(e.cross_reference_notes, '') {operator} :pattern ESCAPE '\\'
            OR COALESCE(d.title, '') {operator} :pattern ESCAPE '\\'
            OR COALESCE(d.source_reference, '') {operator} :pattern ESCAPE '\\'
            OR COALESCE(d.document_date, '') {operator} :pattern ESCAPE '\\'
        ORDER BY e.extraction_id DESC
        LIMIT :limit
    '''
    rows = conn.execute(text(sql), {"pattern": pattern, "limit": limit}).mappings().all()
    return [dict(row) for row in rows]


def search_dynamic_tables(conn: Connection, query: str, limit: int | None = None) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    limit = limit or config.SEARCH_RESULT_LIMIT
    pattern = _like_pattern(query)
    results: list[dict] = []
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())

    for registry_row in list_registered_tables(conn):
        table_name = registry_row["table_name"]
        if table_name not in existing_tables:
            continue
        pk_column = get_primary_key_column(conn, table_name)
        metadata = get_dynamic_column_metadata(conn, table_name)
        physical_names = {column["name"] for column in inspector.get_columns(table_name)}
        searchable_columns = list(dict.fromkeys(
            item["column_name"] for item in metadata
            if item["column_name"] not in AUDIT_COLUMNS and item["column_name"] in physical_names
        ))
        if not searchable_columns:
            continue

        q_table = _quote_identifier(conn, table_name)
        q_pk = _quote_identifier(conn, pk_column)
        conditions = []
        for column_name in searchable_columns:
            q_col = _quote_identifier(conn, column_name)
            if is_postgres(conn):
                conditions.append(f"CAST({q_col} AS TEXT) ILIKE :pattern ESCAPE '\\'")
            else:
                conditions.append(f"LOWER(CAST({q_col} AS TEXT)) LIKE LOWER(:pattern) ESCAPE '\\'")

        remaining = max(0, limit - len(results))
        if remaining == 0:
            break
        q_columns = ", ".join(_quote_identifier(conn, col) for col in searchable_columns)
        sql = f'''
            SELECT {q_pk}, {q_columns}
            FROM {q_table}
            WHERE {' OR '.join(conditions)}
            ORDER BY {q_pk} DESC
            LIMIT :limit
        '''
        rows = conn.execute(text(sql), {"pattern": pattern, "limit": remaining}).mappings().all()
        for row in rows:
            values = []
            for column_name in searchable_columns:
                value = row.get(column_name)
                if value is not None and query.casefold() in str(value).casefold():
                    values.append(f"{column_name}: {value}")
            if not values:
                values = [
                    f"{column_name}: {row.get(column_name)}"
                    for column_name in searchable_columns
                    if row.get(column_name) is not None
                ][:3]
            results.append({
                "table_name": table_name,
                "description": registry_row.get("description"),
                "record_id": row[pk_column],
                "matched_values": values[:5],
            })
            if len(results) >= limit:
                break
    return results


def global_search(conn: Connection, query: str) -> dict[str, list[dict]]:
    return {
        "documents": search_documents(conn, query),
        "extractions": search_extractions(conn, query),
        "tables": search_dynamic_tables(conn, query),
    }
