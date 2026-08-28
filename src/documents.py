"""Utilidades compartidas para documentos y PDFs OCRizados."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def get_visible_pdf_path(document: dict) -> str | None:
    """Prioriza siempre la copia OCR cuando existe."""
    return (
        document.get("ocr_file_path")
        or document.get("file_path")
        or document.get("original_file_path")
    )


def get_ocr_pdf_path(document: dict) -> str | None:
    path = document.get("ocr_file_path")
    return path if path and Path(path).is_file() else None


def safe_pdf_filename(document_id: int, source_reference: str | None, *, ocr: bool = True) -> str:
    reference = unicodedata.normalize("NFKD", source_reference or "documento")
    reference = "".join(char for char in reference if not unicodedata.combining(char))
    reference = re.sub(r"[^A-Za-z0-9._-]+", "_", reference).strip("._-")
    reference = reference[:80] or "documento"
    suffix = "_OCR" if ocr else ""
    return f"{int(document_id)}_{reference}{suffix}.pdf"


def document_text_snippets(text: str | None, query: str, *, limit: int = 20, radius: int = 120) -> list[str]:
    """Devuelve contextos de coincidencias literales sin alterar el texto almacenado."""
    source = text or ""
    needle = (query or "").strip()
    if not source or not needle:
        return []

    matches = list(re.finditer(re.escape(needle), source, flags=re.IGNORECASE))
    if not matches:
        # Si no aparece la frase completa, busca los términos individuales.
        terms = [term for term in re.findall(r"\w+", needle, flags=re.UNICODE) if len(term) >= 2]
        if terms:
            pattern = re.compile("|".join(re.escape(term) for term in terms), flags=re.IGNORECASE)
            matches = list(pattern.finditer(source))

    snippets: list[str] = []
    last_end = -1
    for match in matches:
        start = max(0, match.start() - radius)
        end = min(len(source), match.end() + radius)
        if start <= last_end:
            continue
        snippet = " ".join(source[start:end].split())
        if start > 0:
            snippet = "…" + snippet
        if end < len(source):
            snippet += "…"
        snippets.append(snippet)
        last_end = end
        if len(snippets) >= limit:
            break
    return snippets
