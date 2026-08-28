# Contribuir a db_visualizer

## Flujo de trabajo

1. Actualiza `main` antes de comenzar.
2. Crea una rama breve desde `main`:
   - `feature/<nombre>` para nuevas funcionalidades.
   - `fix/<nombre>` para correcciones.
   - `docs/<nombre>` para documentación.
3. Mantén los commits acotados y utiliza mensajes descriptivos.
4. Ejecuta las comprobaciones antes de hacer push:

```bash
python -m compileall -q app.py config.py src pages scripts
pytest -q
```

5. Haz push de la rama y abre un pull request hacia `main`.
6. Fusiona únicamente después de que CI finalice correctamente y el cambio haya sido revisado.

## Entorno local

Utiliza siempre un archivo `.env` privado. Nunca publiques contraseñas, dumps de PostgreSQL, PDFs subidos ni archivos OCR.

Inicia el stack con:

```bash
docker compose up --build -d
```

Consulta logs con:

```bash
docker compose logs -f app
```

## Cambios de base de datos

Los cambios de esquema persistente deben ser compatibles hacia atrás cuando sea razonablemente posible. Las modificaciones de esquema deben implementarse en `src/database.py` y acompañarse de pruebas.

## Pull requests

Un pull request debe indicar:

- qué cambió;
- por qué es necesario;
- cómo fue probado;
- si afecta configuración, base de datos, OCR o almacenamiento persistente.
