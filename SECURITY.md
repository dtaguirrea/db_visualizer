# Política de seguridad

## Versión soportada

Las correcciones de seguridad se aplican a la línea estable `1.x` mientras sea la versión mantenida del proyecto.

## Información sensible

No deben incorporarse al repositorio:

- archivos `.env`;
- contraseñas o tokens de sesión;
- dumps de PostgreSQL;
- documentos fuente privados;
- PDFs procesados mediante OCR;
- datos privados de investigación.

`.gitignore` excluye estos archivos por defecto, pero cada contribuidor es responsable de revisar lo que incluye antes de hacer push.

## Despliegue

Para un despliegue accesible desde Internet:

- utiliza HTTPS;
- mantén `AUTH_COOKIE_SECURE=true`;
- utiliza contraseñas largas y únicas;
- publica únicamente los puertos 80 y 443;
- mantiene PostgreSQL y OCR dentro de la red privada de Docker;
- configura backups externos;
- mantén actualizado el sistema operativo del servidor y Docker.

## Reporte de vulnerabilidades

No publiques credenciales ni información documental sensible en un issue público de GitHub. Los problemas de seguridad deben comunicarse de forma privada al responsable del repositorio.
