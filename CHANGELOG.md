# Changelog

Este archivo registra cambios relevantes de `db_visualizer`.

El proyecto utiliza versionado semántico (`MAJOR.MINOR.PATCH`).

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
