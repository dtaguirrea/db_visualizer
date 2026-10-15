"""Utilidades de presentación para llaves foráneas en tablas dinámicas."""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from src.database import get_foreign_keys, get_primary_key_column
from src.documents import document_display_label

_AUDIT = {"created_by", "created_at", "updated_by", "updated_at"}


def _preferred_label_column(conn: Connection, table_name: str, pk_column: str) -> str | None:
    preferred = {
        "users": "username",
        "extraction_records": "key_information",
    }.get(table_name)
    columns = inspect(conn).get_columns(table_name)
    column_names = {column["name"] for column in columns}
    if preferred in column_names:
        return preferred

    foreign_columns = set(get_foreign_keys(conn, table_name))
    candidates = [
        column["name"] for column in columns
        if column["name"] != pk_column
        and column["name"] not in _AUDIT
        and column["name"] not in foreign_columns
    ]
    return candidates[0] if candidates else None


def get_foreign_key_choices(conn: Connection, table_name: str) -> dict[str, dict]:
    """Devuelve opciones legibles para cada FK de una tabla."""
    choices = {}
    quote = conn.dialect.identifier_preparer.quote
    for column_name, referred_table in get_foreign_keys(conn, table_name).items():
        ref_pk = get_primary_key_column(conn, referred_table)

        if referred_table == "document_records":
            rows = conn.execute(text('''
                SELECT document_id, title, source_reference, document_date
                FROM document_records
                ORDER BY document_id
            ''')).mappings().all()
            labels = {row["document_id"]: document_display_label(dict(row)) for row in rows}
            label_column = "title"
        else:
            label_column = _preferred_label_column(conn, referred_table, ref_pk)
            select_columns = [ref_pk]
            if label_column and label_column != ref_pk:
                select_columns.append(label_column)
            rows = conn.execute(text(
                f"SELECT {', '.join(quote(c) for c in select_columns)} "
                f"FROM {quote(referred_table)} ORDER BY {quote(ref_pk)}"
            )).mappings().all()
            labels = {}
            for row in rows:
                pk_value = row[ref_pk]
                detail = row.get(label_column) if label_column else None
                detail_text = str(detail).strip() if detail is not None else ""
                labels[pk_value] = f"{pk_value} · {detail_text}" if detail_text else str(pk_value)

        reverse = {label: pk for pk, label in labels.items()}
        choices[column_name] = {
            "referred_table": referred_table,
            "pk_column": ref_pk,
            "label_column": label_column,
            "labels": labels,
            "reverse": reverse,
            "options": list(labels),
        }
    return choices


def document_viewer_url(document_id) -> str | None:
    if document_id is None:
        return None
    try:
        if document_id != document_id:
            return None
    except Exception:
        pass
    text_id = str(document_id).strip()
    if not text_id:
        return None
    if text_id.endswith(".0") and text_id[:-2].isdigit():
        text_id = text_id[:-2]
    return f"/documento?document_id={text_id}"


