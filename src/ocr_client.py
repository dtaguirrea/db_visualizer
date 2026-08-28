"""Cliente para HURIDOCS pdf-document-layout-analysis."""
from pathlib import Path
import os
import requests

import config

_EXCLUDED_SEGMENT_TYPES = {"Page header", "Page footer"}


class OcrServiceError(Exception):
    pass


def _post_pdf(endpoint: str, file_path: str, data: dict):
    url = f"{config.OCR_SERVICE_URL}{endpoint}"
    try:
        with open(file_path, "rb") as file_handle:
            response = requests.post(
                url,
                files={"file": (Path(file_path).name, file_handle, "application/pdf")},
                data=data,
                timeout=config.OCR_TIMEOUT_SECONDS,
            )
    except requests.exceptions.ConnectionError as exc:
        raise OcrServiceError("No fue posible conectar con el servicio OCR.") from exc
    except requests.exceptions.Timeout as exc:
        raise OcrServiceError("El procesamiento OCR excedió el tiempo máximo configurado.") from exc
    except requests.exceptions.RequestException as exc:
        raise OcrServiceError(f"Error de comunicación con el servicio OCR: {exc}") from exc

    if response.status_code != 200:
        detail = response.text[:400].strip()
        suffix = f" Detalle: {detail}" if detail else ""
        raise OcrServiceError(f"El servicio OCR respondió con código {response.status_code}.{suffix}")
    return response


def create_searchable_pdf(input_path: str, output_path: str) -> str:
    """Genera un PDF buscable con capa de texto mediante POST /ocr."""
    response = _post_pdf("/ocr", input_path, {
        "language": config.OCR_LANGUAGE,
        "rotate_pages": str(config.OCR_ROTATE_PAGES).lower(),
        "deskew": str(config.OCR_DESKEW).lower(),
    })
    content = response.content
    if not content.startswith(b"%PDF"):
        raise OcrServiceError("El servicio OCR no devolvió un PDF válido.")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output.with_suffix(output.suffix + ".tmp")
    temp_path.write_bytes(content)
    os.replace(temp_path, output)
    return str(output)


def extract_text_from_pdf(file_path: str) -> str:
    """Extrae texto estructurado del PDF ya OCRizado para búsqueda."""
    response = _post_pdf("/text", file_path, {
        "fast": "true" if config.OCR_USE_FAST_MODE else "false",
        "types": "all",
    })
    try:
        segments = response.json()
    except ValueError as exc:
        raise OcrServiceError("El servicio OCR devolvió una respuesta de texto inválida.") from exc

    if isinstance(segments, dict) and "text" in segments:
        return segments["text"] or ""
    if not isinstance(segments, list):
        return str(segments)
    return "\n\n".join(
        segment.get("text", "").strip()
        for segment in segments
        if segment.get("type") not in _EXCLUDED_SEGMENT_TYPES and segment.get("text", "").strip()
    )


def process_pdf(input_path: str, output_path: str) -> tuple[str, str]:
    searchable_pdf = create_searchable_pdf(input_path, output_path)
    extracted_text = extract_text_from_pdf(searchable_pdf)
    return searchable_pdf, extracted_text


def check_service_health() -> bool:
    try:
        response = requests.get(f"{config.OCR_SERVICE_URL}/info", timeout=5)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False
