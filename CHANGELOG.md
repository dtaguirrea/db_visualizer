# Changelog

Este archivo registra cambios relevantes de `db_visualizer`.

El proyecto utiliza versionado semántico (`MAJOR.MINOR.PATCH`).

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
