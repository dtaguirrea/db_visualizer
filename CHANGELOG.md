# Cambios

## 3.1.0

### Funcionalidades

- Sección Gráficos con tablas dinámicas y Documentos.
- Frecuencias, porcentajes con denominador explícito, indicador de categorías
  seleccionadas, filtro de igualdad, barras y circular.
- Series por año conocido con contadores de datos excluidos.
- Rankings por FK con conteo opcional de entidades distintas y destinos sin vínculos.
- Descarga CSV de resúmenes. Consultas agregadas en SQL, sin cargar el texto OCR.
- README con instalación, inicio, cierre, actualización, respaldo y diagnóstico del build.

### Correcciones

- Cerrar sesión o invalidar un token elimina snapshots, uploads y formularios privados.
- Los intervalos de fechas parciales no se recortan al día de carga.
- Migración idempotente de los límites derivados ya almacenados; no cambia la fecha original.
- Los filtros cronológicos sin año muestran un error en vez de ignorarse silenciosamente.
- Valores de opciones inválidos en CSV se detectan antes de insertar y se informa fila y columna.
- `.env.example` muestra la versión correcta y elimina variables OCR sin uso en Compose.

### Archivos agregados

- `src/analytics.py`: consultas y cálculos.
- `src/analytics_ui.py`: gráficos y descargas.
- `src/session_state.py`: limpieza de sesión.
- `pages/analytics.py`: nueva página.
- `tests/conftest.py`, `tests/test_analytics.py`, `tests/test_regressions.py`,
  `tests/test_analytics_page.py`: pruebas de datos, regresión y página.
- `requirements-dev.txt`, `README.md`, `CHANGELOG.md`, `VALIDATION.md`.

### Archivos modificados

- `app.py`, `src/navigation.py`.
- `src/field_values.py`, `src/database.py`, `src/search.py`, `src/csv_tables.py`.
- `requirements.txt`, `VERSION`, `docker-compose.yml`, `.env.example`.

No se agregan gráficos a partir de nombres de columnas inexistentes ni se modifica
el esquema de las tablas de usuario para habilitar el análisis. Las secciones
retiradas en 3.0.0 continúan fuera de la navegación.
