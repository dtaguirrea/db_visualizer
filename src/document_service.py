"""Cola OCR persistente y operaciones seguras sobre documentos."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

import config
from src.audit import log_action
from src.database import get_connection, get_engine, is_postgres, utc_now
from src.ocr_client import (
    create_searchable_pdf,
    extract_text_from_pdf,
    extract_text_locally,
)

LOGGER = logging.getLogger(__name__)
MIN_DOCUMENT_DATE = date(1800, 1, 1)


class DocumentServiceError(RuntimeError):
    """Error esperado al administrar documentos."""


class QueueFullError(DocumentServiceError):
    """La cola no tiene espacio para el lote solicitado."""


class DocumentNotFoundError(DocumentServiceError):
    """El documento ya no existe."""


class DocumentBusyError(DocumentServiceError):
    """El documento está siendo procesado y no se puede eliminar."""


class DocumentInUseError(DocumentServiceError):
    """Una tabla creada por usuarios todavía referencia el documento."""


class ConcurrentDocumentEditError(DocumentServiceError):
    """El documento cambió después de abrir el formulario."""


class InvalidDocumentDate(ValueError):
    """La fecha está fuera del rango permitido."""


def normalize_document_date(value: date | datetime | str | None) -> str | None:
    """Normaliza una fecha y restringe el rango entre 1800-01-01 y hoy."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        try:
            parsed = date.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise InvalidDocumentDate("La fecha debe usar el formato AAAA-MM-DD.") from exc
    if parsed < MIN_DOCUMENT_DATE or parsed > date.today():
        raise InvalidDocumentDate(
            f"La fecha debe estar entre {MIN_DOCUMENT_DATE.isoformat()} y {date.today().isoformat()}."
        )
    return parsed.isoformat()


def _queue_lock(conn: Connection) -> None:
    if is_postgres(conn):
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('db_visualizer_ocr_enqueue'))"))


def get_queue_counts(conn: Connection | None = None) -> dict[str, int]:
    owns_connection = conn is None
    conn = conn or get_connection()
    try:
        rows = conn.execute(text('''
            SELECT ocr_status, COUNT(*) AS total
            FROM document_records
            WHERE ocr_status IN ('en_cola', 'procesando')
            GROUP BY ocr_status
        ''')).mappings().all()
        counts = {"en_cola": 0, "procesando": 0}
        counts.update({str(row["ocr_status"]): int(row["total"]) for row in rows})
        counts["ocupados"] = counts["en_cola"] + counts["procesando"]
        counts["disponibles"] = max(0, config.OCR_QUEUE_MAX_SIZE - counts["ocupados"])
        return counts
    finally:
        if owns_connection:
            conn.close()


def enqueue_documents(records: Iterable[dict], username: str) -> list[int]:
    """Inserta un lote completo en la cola, sin superar su capacidad total."""
    items = list(records)
    if not items:
        return []
    if len(items) > config.OCR_QUEUE_MAX_SIZE:
        raise QueueFullError(
            f"Puedes cargar como máximo {config.OCR_QUEUE_MAX_SIZE} documentos por lote."
        )

    with get_connection() as conn:
        try:
            _queue_lock(conn)
            counts = get_queue_counts(conn)
            if len(items) > counts["disponibles"]:
                raise QueueFullError(
                    f"La cola tiene {counts['disponibles']} espacio(s) disponible(s). "
                    f"El lote contiene {len(items)} documento(s)."
                )

            now = utc_now()
            document_ids: list[int] = []
            for item in items:
                document_date = normalize_document_date(item.get("document_date"))
                result = conn.execute(text('''
                    INSERT INTO document_records (
                        title, source_reference, document_date, original_format,
                        file_path, original_file_path, ocr_file_path, ocr_text,
                        ocr_status, ocr_error, queued_at, processing_started_at,
                        processing_finished_at, ocr_attempts,
                        ocr_language, created_by, created_at, updated_by, updated_at
                    ) VALUES (
                        :title, :source_reference, :document_date, :original_format,
                        :original_file_path, :original_file_path, NULL, '',
                        'en_cola', NULL, :queued_at, NULL, NULL, 0, :ocr_language,
                        :created_by, :created_at, :updated_by, :updated_at
                    ) RETURNING document_id
                '''), {
                    "title": (item.get("title") or "").strip() or None,
                    "source_reference": (item.get("source_reference") or "").strip() or None,
                    "document_date": document_date,
                    "original_format": item.get("original_format"),
                    "original_file_path": str(item["original_file_path"]),
                    "ocr_language": item.get("ocr_language") or config.OCR_LANGUAGE,
                    "queued_at": now,
                    "created_by": username,
                    "created_at": now,
                    "updated_by": username,
                    "updated_at": now,
                })
                document_id = int(result.scalar_one())
                document_ids.append(document_id)
                log_action(
                    conn,
                    "document_records",
                    document_id,
                    "ENQUEUE_OCR",
                    username,
                    details={
                        "title": item.get("title"),
                        "ocr_status": "en_cola",
                        "ocr_language": item.get("ocr_language") or config.OCR_LANGUAGE,
                    },
                )
            conn.commit()
            return document_ids
        except Exception:
            conn.rollback()
            raise


def requeue_document(document_id: int, username: str) -> None:
    """Devuelve un documento fallido o parcial al final de la cola."""
    with get_connection() as conn:
        try:
            _queue_lock(conn)
            counts = get_queue_counts(conn)
            if counts["disponibles"] < 1:
                raise QueueFullError("La cola OCR está completa.")
            now = utc_now()
            result = conn.execute(text('''
                UPDATE document_records
                SET ocr_status = 'en_cola', ocr_error = NULL, queued_at = :queued_at,
                    processing_started_at = NULL, processing_finished_at = NULL,
                    updated_by = :updated_by, updated_at = :updated_at
                WHERE document_id = :document_id
                  AND ocr_status NOT IN ('en_cola', 'procesando')
            '''), {
                "queued_at": now,
                "updated_by": username,
                "updated_at": now,
                "document_id": document_id,
            })
            if result.rowcount != 1:
                exists = conn.execute(text(
                    "SELECT ocr_status FROM document_records WHERE document_id = :document_id"
                ), {"document_id": document_id}).scalar_one_or_none()
                if exists is None:
                    raise DocumentNotFoundError("El documento ya no existe.")
                raise DocumentServiceError("El documento ya está en cola o se está procesando.")
            log_action(conn, "document_records", document_id, "REQUEUE_OCR", username)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def claim_next_document() -> dict | None:
    """Reserva atómicamente el documento más antiguo pendiente."""
    with get_connection() as conn:
        try:
            suffix = " FOR UPDATE SKIP LOCKED" if is_postgres(conn) else ""
            row = conn.execute(text(f'''
                SELECT document_id, title, original_file_path, ocr_attempts, ocr_language
                FROM document_records
                WHERE ocr_status = 'en_cola'
                ORDER BY queued_at, document_id
                LIMIT 1{suffix}
            ''')).mappings().first()
            if row is None:
                conn.commit()
                return None
            now = utc_now()
            result = conn.execute(text('''
                UPDATE document_records
                SET ocr_status = 'procesando', processing_started_at = :started_at,
                    processing_finished_at = NULL, ocr_error = NULL,
                    ocr_attempts = COALESCE(ocr_attempts, 0) + 1,
                    updated_by = 'ocr-worker', updated_at = :updated_at
                WHERE document_id = :document_id AND ocr_status = 'en_cola'
            '''), {
                "started_at": now,
                "updated_at": now,
                "document_id": row["document_id"],
            })
            if result.rowcount != 1:
                conn.rollback()
                return None
            claimed = dict(row)
            claimed["ocr_attempts"] = int(claimed.get("ocr_attempts") or 0) + 1
            log_action(
                conn, "document_records", claimed["document_id"], "START_OCR", "ocr-worker",
                details={
                    "attempt": claimed["ocr_attempts"],
                    "ocr_language": claimed.get("ocr_language") or config.OCR_LANGUAGE,
                },
            )
            conn.commit()
            return claimed
        except Exception:
            conn.rollback()
            raise


def _finish_ocr(
    document_id: int,
    *,
    status: str,
    ocr_file_path: str | None,
    ocr_text: str,
    error: str | None,
) -> None:
    with get_connection() as conn:
        try:
            original_path = conn.execute(text(
                "SELECT original_file_path FROM document_records WHERE document_id = :document_id"
            ), {"document_id": document_id}).scalar_one_or_none()
            visible_path = ocr_file_path or original_path
            now = utc_now()
            result = conn.execute(text('''
                UPDATE document_records
                SET file_path = :file_path, ocr_file_path = :ocr_file_path,
                    ocr_text = :ocr_text, ocr_status = :ocr_status, ocr_error = :ocr_error,
                    processing_finished_at = :finished_at,
                    updated_by = 'ocr-worker', updated_at = :updated_at
                WHERE document_id = :document_id AND ocr_status = 'procesando'
            '''), {
                "file_path": visible_path,
                "ocr_file_path": ocr_file_path,
                "ocr_text": ocr_text,
                "ocr_status": status,
                "ocr_error": error,
                "finished_at": now,
                "updated_at": now,
                "document_id": document_id,
            })
            if result.rowcount == 1:
                log_action(
                    conn, "document_records", document_id, "FINISH_OCR", "ocr-worker",
                    details={"ocr_status": status, "error": error},
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def process_next_document() -> bool:
    """Procesa exactamente un elemento; devuelve False cuando la cola está vacía."""
    document = claim_next_document()
    if document is None:
        return False

    document_id = int(document["document_id"])
    original_path_value = str(document.get("original_file_path") or "").strip()
    if not original_path_value:
        _finish_ocr(
            document_id,
            status="error",
            ocr_file_path=None,
            ocr_text="",
            error="El documento no tiene una ruta de archivo original.",
        )
        return True
    original_path = Path(original_path_value)
    ocr_path = original_path.with_name(
        original_path.stem.replace("_original", "") + "_ocr.pdf"
    )
    if not original_path.is_file():
        _finish_ocr(
            document_id,
            status="error",
            ocr_file_path=None,
            ocr_text="",
            error="No se encuentra el archivo PDF original en el servidor.",
        )
        return True

    try:
        searchable_pdf = create_searchable_pdf(
            str(original_path),
            str(ocr_path),
            language=document.get("ocr_language") or config.OCR_LANGUAGE,
        )
    except Exception as exc:
        LOGGER.exception("OCR failed for document %s", document_id)
        _finish_ocr(
            document_id,
            status="error",
            ocr_file_path=None,
            ocr_text="",
            error=str(exc),
        )
        return True

    if config.OCR_TEXT_EXTRACTION_MODE == "disabled":
        _finish_ocr(
            document_id,
            status="completado",
            ocr_file_path=searchable_pdf,
            ocr_text="",
            error=None,
        )
        return True

    try:
        if config.OCR_TEXT_EXTRACTION_MODE == "huridocs":
            indexed_text = extract_text_from_pdf(searchable_pdf)
        else:
            indexed_text = extract_text_locally(searchable_pdf)
    except Exception as exc:
        LOGGER.warning("Text indexing failed for document %s", document_id, exc_info=True)
        _finish_ocr(
            document_id,
            status="parcial",
            ocr_file_path=searchable_pdf,
            ocr_text="",
            error=str(exc),
        )
        return True

    _finish_ocr(
        document_id,
        status="completado",
        ocr_file_path=searchable_pdf,
        ocr_text=indexed_text,
        error=None,
    )
    return True


def recover_interrupted_jobs() -> int:
    """Devuelve a la cola trabajos que quedaron activos tras detener el trabajador."""
    with get_engine().begin() as conn:
        now = utc_now()
        result = conn.execute(text('''
            UPDATE document_records
            SET ocr_status = 'en_cola', queued_at = :queued_at,
                processing_started_at = NULL,
                ocr_error = 'Procesamiento interrumpido; reintentando automáticamente.',
                updated_by = 'ocr-worker', updated_at = :updated_at
            WHERE ocr_status = 'procesando'
        '''), {"queued_at": now, "updated_at": now})
        return int(result.rowcount or 0)


def update_document_metadata(
    document_id: int,
    *,
    title: str | None,
    source_reference: str | None,
    document_date: date | datetime | str | None,
    original_format: str | None,
    ocr_language: str | None,
    username: str,
    expected_updated_at: str | None,
) -> None:
    normalized_date = normalize_document_date(document_date)
    with get_connection() as conn:
        try:
            now = utc_now()
            concurrency_clause = (
                "updated_at IS NULL" if expected_updated_at is None
                else "updated_at = :expected_updated_at"
            )
            result = conn.execute(text(f'''
                UPDATE document_records
                SET title = :title, source_reference = :source_reference,
                    document_date = :document_date, original_format = :original_format,
                    ocr_language = :ocr_language,
                    updated_by = :updated_by, updated_at = :updated_at
                WHERE document_id = :document_id AND {concurrency_clause}
            '''), {
                "title": (title or "").strip() or None,
                "source_reference": (source_reference or "").strip() or None,
                "document_date": normalized_date,
                "original_format": original_format,
                "ocr_language": ocr_language or config.OCR_LANGUAGE,
                "updated_by": username,
                "updated_at": now,
                "document_id": document_id,
                "expected_updated_at": expected_updated_at,
            })
            if result.rowcount != 1:
                exists = conn.execute(text(
                    "SELECT 1 FROM document_records WHERE document_id = :document_id"
                ), {"document_id": document_id}).scalar_one_or_none()
                if exists is None:
                    raise DocumentNotFoundError("El documento ya no existe.")
                raise ConcurrentDocumentEditError(
                    "El documento cambió mientras lo editabas. Recarga la página e inténtalo otra vez."
                )
            log_action(
                conn, "document_records", document_id, "UPDATE", username,
                details={
                    "columns": [
                        "title", "source_reference", "document_date", "original_format", "ocr_language"
                    ]
                },
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _blocking_references(conn: Connection, document_id: int) -> list[str]:
    references: list[str] = []
    preparer = conn.dialect.identifier_preparer
    for table_name in inspect(conn).get_table_names():
        if table_name == "extraction_records":
            continue
        for fk in inspect(conn).get_foreign_keys(table_name):
            if fk.get("referred_table") != "document_records":
                continue
            columns = fk.get("constrained_columns") or []
            if not columns:
                continue
            q_table = preparer.quote(table_name)
            q_column = preparer.quote(columns[0])
            count = int(conn.execute(text(
                f"SELECT COUNT(*) FROM {q_table} WHERE {q_column} = :document_id"
            ), {"document_id": document_id}).scalar_one())
            if count:
                references.append(f"{table_name} ({count})")
    return references


def delete_document(document_id: int, username: str) -> list[str]:
    """Elimina el registro, sus extracciones en cascada y sus archivos locales."""
    with get_connection() as conn:
        try:
            suffix = " FOR UPDATE" if is_postgres(conn) else ""
            row = conn.execute(text(f'''
                SELECT title, ocr_status, file_path, original_file_path, ocr_file_path
                FROM document_records
                WHERE document_id = :document_id{suffix}
            '''), {"document_id": document_id}).mappings().first()
            if row is None:
                raise DocumentNotFoundError("El documento ya no existe.")
            if row["ocr_status"] == "procesando":
                raise DocumentBusyError(
                    "Espera a que termine el OCR antes de eliminar este documento."
                )
            references = _blocking_references(conn, document_id)
            if references:
                raise DocumentInUseError(
                    "El documento está referenciado en: " + ", ".join(references) + "."
                )
            paths = {
                str(path) for path in (row["file_path"], row["original_file_path"], row["ocr_file_path"])
                if path
            }
            log_action(
                conn, "document_records", document_id, "DELETE", username,
                details={"title": row["title"]},
            )
            result = conn.execute(text(
                "DELETE FROM document_records WHERE document_id = :document_id"
            ), {"document_id": document_id})
            if result.rowcount != 1:
                raise DocumentNotFoundError("El documento ya no existe.")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    removed: list[str] = []
    for raw_path in paths:
        try:
            path = Path(raw_path)
            if path.is_file():
                path.unlink()
                removed.append(raw_path)
        except OSError:
            LOGGER.warning("Could not remove document file %s", raw_path, exc_info=True)
    return removed


def queue_snapshot() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(text('''
            SELECT document_id, title, source_reference, ocr_status, ocr_language, queued_at,
                   processing_started_at, ocr_attempts, created_by
            FROM document_records
            WHERE ocr_status IN ('en_cola', 'procesando')
            ORDER BY CASE WHEN ocr_status = 'procesando' THEN 0 ELSE 1 END,
                     queued_at, document_id
        ''')).mappings().all()
    queued_position = 0
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        if item["ocr_status"] == "en_cola":
            queued_position += 1
            item["queue_position"] = queued_position
        else:
            item["queue_position"] = None
        result.append(item)
    return result


def run_worker() -> None:
    """Ejecuta un único trabajador OCR por instalación."""
    lock_connection = get_engine().connect()
    acquired = True
    try:
        if is_postgres(lock_connection):
            acquired = bool(lock_connection.execute(text(
                "SELECT pg_try_advisory_lock(hashtext('db_visualizer_ocr_worker'))"
            )).scalar_one())
            lock_connection.commit()
        if not acquired:
            LOGGER.error("Another OCR worker already owns the queue lock; exiting.")
            return
        recovered = recover_interrupted_jobs()
        if recovered:
            LOGGER.warning("Recovered %s interrupted OCR job(s).", recovered)
        LOGGER.info("OCR worker started; queue capacity is %s.", config.OCR_QUEUE_MAX_SIZE)
        while True:
            if not process_next_document():
                time.sleep(config.OCR_WORKER_POLL_SECONDS)
    finally:
        if acquired and is_postgres(lock_connection):
            try:
                lock_connection.execute(text(
                    "SELECT pg_advisory_unlock(hashtext('db_visualizer_ocr_worker'))"
                ))
                lock_connection.commit()
            except Exception:
                LOGGER.warning("Could not release OCR worker lock cleanly.", exc_info=True)
        lock_connection.close()
