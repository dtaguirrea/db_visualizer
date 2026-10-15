"""Localización de evidencia por página sin volver a ejecutar OCR ni /text."""
import io
import re
from pathlib import Path

import streamlit as st
from pypdf import PdfReader, PdfWriter

from src.documents import document_text_snippets


@st.cache_data(max_entries=24, show_spinner=False)
def _page_texts(path, modified_ns, size):
    reader = PdfReader(path)
    return [" ".join((page.extract_text() or "").split()) for page in reader.pages]


def page_texts(path):
    stat = Path(path).stat()
    return _page_texts(str(path), stat.st_mtime_ns, stat.st_size)


def find_passages(path, query, *, limit=30):
    needle = " ".join((query or "").split())
    if not needle:
        return []
    texts = page_texts(path)
    # Se prioriza la frase completa; términos sueltos sólo si no hay frase.
    exact = any(needle.casefold() in value.casefold() for value in texts)
    terms = [needle] if exact else [word for word in re.findall(r"\w+", needle) if len(word) >= 2]
    matches = []
    for index, value in enumerate(texts, start=1):
        if not any(term.casefold() in value.casefold() for term in terms):
            continue
        for snippet in document_text_snippets(value, needle, limit=limit - len(matches), radius=160):
            matches.append({"page": index, "snippet": snippet, "exact": exact})
            if len(matches) >= limit:
                return matches
    return matches


@st.cache_data(max_entries=24, show_spinner=False)
def _single_page(path, modified_ns, size, number):
    reader = PdfReader(path)
    if not 1 <= number <= len(reader.pages):
        raise ValueError("La página solicitada no existe.")
    writer = PdfWriter()
    writer.add_page(reader.pages[number - 1])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def single_page(path, number):
    stat = Path(path).stat()
    return _single_page(str(path), stat.st_mtime_ns, stat.st_size, number)
