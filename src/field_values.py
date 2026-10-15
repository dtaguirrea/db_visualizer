"""Validación compartida para formularios, edición y CSV."""
import calendar
import json
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE_HELP = (
    "Año: 1950; año y mes: 1950-06; completa: 1950-06-12. "
    "Sin año: ????-06-12; solo mes: ????-06; solo día: ????-??-12. "
    "Usa ? para componentes desconocidos. Años desde 1800 hasta hoy."
)


def _date_parts(value):
    if isinstance(value, datetime):
        value = value.date()
    raw = value.isoformat() if isinstance(value, date) else str(value).strip()
    if not re.fullmatch(r"(?:\d{4}|\?{4})(?:-(?:\d{2}|\?{2})){0,2}", raw):
        raise ValueError(DATE_HELP)
    parts = raw.split("-")
    numbers = [None if "?" in part else int(part) for part in parts]
    numbers += [None] * (3 - len(numbers))
    year, month, day = numbers
    if not any(number is not None for number in numbers):
        raise ValueError("Deja la fecha vacía si no conoces ningún componente.")
    if year is not None and not 1800 <= year <= date.today().year:
        raise ValueError("El año debe estar entre 1800 y el año actual.")
    if month is not None and not 1 <= month <= 12:
        raise ValueError("El mes debe estar entre 01 y 12.")
    if day is not None and not 1 <= day <= 31:
        raise ValueError("El día debe estar entre 01 y 31.")
    if month is not None and day is not None:
        # 2000 permite 29 de febrero cuando no conocemos el año.
        date(year or 2000, month, day)
    return raw, year, month, day


def date_bounds(value):
    """Intervalo posible de búsqueda; no completa ni modifica la fecha histórica."""
    if value is None or not str(value).strip():
        return None, None
    _, year, month, day = _date_parts(value)
    if year is None:
        return None, None
    candidates = []
    for candidate_month in ([month] if month else range(1, 13)):
        last = calendar.monthrange(year, candidate_month)[1]
        if day is not None:
            if day <= last:
                candidates.append(date(year, candidate_month, day))
        else:
            candidates.extend((date(year, candidate_month, 1), date(year, candidate_month, last)))
    start, end = min(candidates), max(candidates)
    if start > date.today():
        raise ValueError("La fecha no puede ser posterior a hoy.")
    # El intervalo de una fecha parcial es estable: no se recorta al día de
    # carga, pues quedaría obsoleto al avanzar el calendario.
    return start.isoformat(), end.isoformat()


def normalize_partial_date(value):
    if value is None or not str(value).strip():
        return None
    raw, _, _, _ = _date_parts(value)
    date_bounds(raw)
    return raw


def normalize_options(values):
    if isinstance(values, str):
        values = values.splitlines()
    if not isinstance(values, (list, tuple)):
        raise ValueError("Define las opciones permitidas, una por línea.")
    options = [str(value).strip() for value in values if str(value).strip()]
    if not options:
        raise ValueError("Debes definir al menos una opción.")
    if len(options) > 200 or any(len(value) > 200 for value in options):
        raise ValueError("Máximo 200 opciones de hasta 200 caracteres.")
    if len({value.casefold() for value in options}) != len(options):
        raise ValueError("Las opciones no pueden repetirse, incluso con distintas mayúsculas.")
    if any("\x00" in value for value in options):
        raise ValueError("Las opciones contienen caracteres no válidos.")
    return options


def column_options(column):
    return json.loads(column.get("options_json") or "[]")


def normalize_field(value, kind, options=None):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if kind == "Fecha":
        return normalize_partial_date(value)
    if kind == "Opciones":
        value = str(value).strip()
        if value not in (options or []):
            raise ValueError("Selecciona una de las opciones permitidas.")
        return value
    if kind == "Número entero":
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("Se esperaba un número entero.") from exc
        if not number.is_finite() or number != number.to_integral_value() or not -(2**63) <= number < 2**63:
            raise ValueError("Se esperaba un entero de 64 bits.")
        return int(number)
    if kind == "Número decimal":
        number = float(str(value).replace(",", "."))
        if not math.isfinite(number):
            raise ValueError("El número debe ser finito.")
        return number
    return value
