"""Lectura estricta de CSV e importación atómica de tablas con auditoría."""
import csv
import io
import math
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

from src.database import get_dynamic_column_metadata, get_primary_key_column
from src.dynamic_tables import create_dynamic_table, list_registered_tables, validate_identifier
from src.repository import AUDIT_COLUMNS, insert_record
from src.table_relations import get_foreign_key_choices
from src.field_values import normalize_field, column_options

MAX_CSV_BYTES = 10 * 1024 * 1024
MAX_CSV_ROWS = 10000


def read_csv(data: bytes, encoding: str = "utf-8-sig", delimiter: str = "auto") -> pd.DataFrame:
    if len(data) > MAX_CSV_BYTES:
        raise ValueError("El CSV supera el límite de 10 MB.")
    try:
        content = data.decode(encoding).lstrip("\ufeff")
    except UnicodeDecodeError as exc:
        raise ValueError("No se pudo leer el archivo. Prueba la codificación Windows-1252.") from exc
    if not content.strip():
        raise ValueError("El archivo está vacío.")
    if "\x00" in content:
        raise ValueError("El archivo contiene caracteres nulos; usa un CSV UTF-8.")
    if delimiter == "auto":
        try:
            delimiter = csv.Sniffer().sniff(content[:65536], delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    if delimiter not in (",", ";", "\t"):
        raise ValueError("Separador no válido.")
    try:
        reader = csv.reader(io.StringIO(content, newline=""), delimiter=delimiter, strict=True)
        headers = [value.strip() for value in next(reader)]
        if not headers or any(not value for value in headers):
            raise ValueError("Todas las columnas deben tener un encabezado.")
        names = [name.casefold() for name in headers]
        if len(names) != len(set(names)):
            raise ValueError("Hay encabezados repetidos (se ignoran mayúsculas y minúsculas).")
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(headers):
                raise ValueError(f"Línea {reader.line_num}: se esperaban {len(headers)} campos y llegaron {len(row)}.")
            rows.append(row)
            if len(rows) > MAX_CSV_ROWS:
                raise ValueError("El límite por importación es de 10.000 registros.")
    except csv.Error as exc:
        raise ValueError(f"CSV inválido: {exc}") from exc
    # Conserva ceros iniciales y textos como NA; sólo las celdas vacías serán NULL.
    return pd.DataFrame(rows, columns=headers, dtype=str)


def export_csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, lineterminator="\r\n").encode("utf-8-sig")


def _value(raw, kind):
    from src.field_values import normalize_field
    # Las opciones se validan también en insert_record, dentro de la transacción.
    return normalize_field(raw, kind) if kind != 'Opciones' else (str(raw).strip() or None)


def prepare_rows(conn, table_name, frame):
    if table_name not in {row["table_name"] for row in list_registered_tables(conn)}:
        raise ValueError("Selecciona una tabla personalizada registrada.")
    metadata = {c["column_name"]: c for c in get_dynamic_column_metadata(conn, table_name)}
    pk = get_primary_key_column(conn, table_name)
    protected = {pk.casefold(), *(c.casefold() for c in AUDIT_COLUMNS)}
    lookup = {name.casefold(): name for name in metadata}
    mapping = {}
    for header in frame.columns:
        if header.casefold() in protected:
            continue
        name = lookup.get(header.casefold())
        if name is None:
            raise ValueError(f"La columna '{header}' no existe en '{table_name}'.")
        mapping[header] = name
    if not mapping:
        raise ValueError("El CSV no contiene columnas de datos para importar.")
    relations = get_foreign_key_choices(conn, table_name)
    keys = {name: {str(key): key for key in rel["options"]} for name, rel in relations.items()}
    prepared = []
    for index, row in enumerate(frame.to_dict("records"), start=2):
        values = {}
        for header, name in mapping.items():
            try:
                raw = row[header]
                if name in keys:
                    lookup_key = str(raw).strip()
                    # Una columna nullable exportada por pandas puede representar 1 como 1.0.
                    if lookup_key and keys[name] and all(isinstance(k, int) for k in keys[name].values()):
                        try:
                            number = Decimal(lookup_key)
                            if number.is_finite() and number == number.to_integral_value() and -(2**63) <= number < 2**63:
                                lookup_key = str(int(number))
                        except InvalidOperation:
                            pass
                    value = None if not lookup_key else keys[name].get(lookup_key)
                    if value is None and str(raw).strip():
                        raise ValueError(f"no existe ese ID en {relations[name]['referred_table']}")
                else:
                    column = metadata[name]
                    value = normalize_field(raw, column['column_type'], column_options(column))
                values[name] = value
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"Registro CSV {index - 1}, columna '{header}': {exc}") from exc
        prepared.append(values)
    return prepared


def import_csv(conn, frame, username, *, table_name, columns=None, description=""):
    """Crea opcionalmente la tabla e inserta todas las filas o ninguna."""
    if frame.empty:
        raise ValueError("El CSV no contiene registros.")
    if len(frame) > MAX_CSV_ROWS:
        raise ValueError("El límite por importación es de 10.000 registros.")
    try:
        if columns is not None:
            # SQLite necesita BEGIN explícito para que el DDL también sea reversible.
            if conn.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN")
            table_name = create_dynamic_table(conn, table_name, columns, description, username, commit=False)
            frame = frame.rename(columns={name: validate_identifier(name, "encabezado") for name in frame.columns})
        rows = prepare_rows(conn, table_name, frame)
        for values in rows:
            insert_record(conn, table_name, values, username, commit=False)
        conn.commit()
        return table_name, len(rows)
    except Exception:
        conn.rollback()
        raise
