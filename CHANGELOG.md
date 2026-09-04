# Changelog

Este archivo registra cambios relevantes de `db_visualizer`.

El proyecto utiliza versionado semántico (`MAJOR.MINOR.PATCH`).

## [2.1.0] - 2026-09-04

### Añadido
- Selección de idioma OCR por documento, tanto al cargar como al editar sus metadatos.
- Selector de documentos integrado en el Visor, manteniendo los enlaces directos desde tablas y búsquedas.
- Acceso directo desde el Visor al espacio de Extracción del documento seleccionado.
- Página de Extracción convertida en un flujo de fichaje de evidencias con página, categoría, información clave, cita literal, cruces y estado de revisión.
- Métricas, filtro, edición, eliminación confirmada y exportación CSV de las fichas de cada documento.
- Pestaña explícita para eliminar uno o varios registros de cualquier tabla personalizada.

### Modificado
- La indexación de texto usa por defecto la capa OCR del PDF de forma local, evitando la costosa llamada a `/text` de HURIDOCS.
- Se puede elegir `local`, `huridocs` o `disabled` mediante `OCR_TEXT_EXTRACTION_MODE`.
- Los documentos completados también pueden reprocesarse después de cambiar su idioma OCR.

### Seguridad e integridad
- La eliminación múltiple conserva auditoría y control de concurrencia.
- Las eliminaciones se bloquean cuando otros registros todavía dependen de la selección.
- Las migraciones agregan idioma OCR y número de página sin borrar documentos ni extracciones existentes.

## [2.0.0] - 2026-09-04

### Añadido
- Cola OCR persistente con una capacidad configurable de 20 trabajos activos por defecto.
- Carga múltiple de hasta 20 archivos PDF y procesamiento estrictamente secuencial mediante un trabajador independiente.
- Vista de estado de la cola con documento activo, posiciones, intentos y espacios disponibles.
- Edición de título, procedencia, fecha y formato de los documentos cargados.
- Eliminación confirmada de documentos, extracciones asociadas y archivos PDF almacenados.
- Recuperación automática de trabajos interrumpidos después de reiniciar el trabajador.

### Corregido
- El selector de fecha ahora permite documentos desde el 1 de enero de 1800 hasta la fecha actual.
- La carga deja de bloquear la sesión de Streamlit mientras HURIDOCS procesa un PDF.
- El reprocesamiento fallido vuelve al final de la cola en vez de iniciar otro OCR concurrente.

### Seguridad e integridad
- La capacidad de la cola se reserva atómicamente para cargas simultáneas.
- PostgreSQL impide que más de un trabajador OCR consuma la cola a la vez.
- No se permite eliminar un documento mientras está siendo procesado ni cuando una tabla personalizada lo referencia.
- Las operaciones de cola, edición, reprocesamiento y eliminación quedan registradas en auditoría.

## [1.1.0] - 2026-08-28

### Añadido
- Los documentos pueden registrar título o nombre, origen/procedencia y fecha del documento.
- Los tres metadatos documentales son opcionales y pueden almacenarse como `NULL`.
- Los selectores de llaves foráneas a documentos priorizan el título y muestran procedencia y fecha cuando existen.
- La búsqueda global incluye título, procedencia y fecha de los documentos.
- La imagen OCR y su política de descarga pueden configurarse mediante `OCR_IMAGE` y `OCR_PULL_POLICY`.

### Corregido
- Se corrige el error `DuplicateColumn` que podía generar pares como `Nombre`/`nombre` y `Mencion`/`mencion` al insertar registros en PostgreSQL.
- Las tablas y columnas dinámicas nuevas se normalizan a identificadores técnicos en minúsculas.
- Al iniciar, los metadatos de tablas dinámicas existentes se reconcilian con el esquema físico para eliminar duplicados por mayúsculas/minúsculas sin borrar registros de usuario.
- La inserción de registros canonicaliza defensivamente los nombres de columnas y evita emitir SQL con la misma columna más de una vez.
- La búsqueda de tablas dinámicas ignora metadatos que no correspondan a columnas físicas reales.

## [1.0.1] - 2026-08-28

### Corregido
- Se corrige la referencia de la imagen HURIDOCS: `v0.0.35` está publicada en Docker Hub como `huridocs/pdf-document-layout-analysis:v0.0.35`, no en GHCR.
- El servicio OCR vuelve a poder descargarse con `docker compose up --build -d`.

## [1.0.0] - 2026-08-28

### Añadido
- Gestión documental colaborativa respaldada por PostgreSQL.
- Creación y edición de tablas dinámicas con trazabilidad por usuario.
- Llaves foráneas entre tablas dinámicas y documentos.
- Selectores de documentos y navegación directa hacia el visor.
- Carga de PDFs y generación de copias buscables mediante OCR.
- Descarga del PDF OCR y del documento fuente original.
- Búsqueda global sobre OCR, extracciones y tablas personalizadas.
- Sesiones persistentes compartidas entre pestañas del navegador.
- Detección de conflictos de edición concurrente.
- Despliegue con Docker Compose, PostgreSQL y HURIDOCS.
- Configuración opcional de HTTPS para producción mediante Caddy.
- Integración continua mediante GitHub Actions.

### Modificado
- El proyecto adopta oficialmente el nombre `db_visualizer`.
- El historial de versiones de prototipo se reemplaza por el primer release estable `1.0.0`.
- Se eliminan nombres internos de contenedores y volúmenes utilizados durante el prototipado.
- Se eliminan usuarios demo y contraseñas predeterminadas compartidas.
- La cuenta administradora inicial se crea mediante variables de entorno controladas.

### Seguridad
- PostgreSQL y HURIDOCS no publican puertos directamente al host.
- La aplicación se vincula a localhost por defecto.
- El despliegue público dispone de terminación HTTPS.
- Los tokens de sesión se almacenan como hashes en PostgreSQL.
- El contenedor de aplicación utiliza un usuario sin privilegios de root.
