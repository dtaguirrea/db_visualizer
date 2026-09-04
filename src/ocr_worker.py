"""Punto de entrada del trabajador que consume la cola OCR secuencialmente."""
import logging

from src.database import init_db
from src.document_service import run_worker


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    init_db()
    run_worker()


if __name__ == "__main__":
    main()
