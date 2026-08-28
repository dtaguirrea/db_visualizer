# db_visualizer

**db_visualizer** es una plataforma colaborativa para gestión documental y estructuración de datos. Permite trabajar con documentos PDF, aplicar OCR, consultar el texto reconocido, organizar información en tablas relacionales y mantener trazabilidad sobre las modificaciones realizadas por los usuarios.

**Versión estable actual:** `1.1.0`

La interfaz de usuario está en español. Los identificadores del código y la configuración técnica utilizan nombres en inglés cuando corresponde.

## Funcionalidades principales

- Carga y administración de documentos PDF con título, procedencia y fecha opcionales.
- Generación de PDFs buscables mediante OCR con [HURIDOCS PDF Document Layout Analysis](https://github.com/huridocs/pdf-document-layout-analysis).
- Visualización, búsqueda y descarga del PDF procesado con OCR.
- Conservación separada del documento original y de su copia OCR.
- Registro de extracciones de información asociadas a documentos.
- Creación de tablas personalizadas desde la interfaz.
- Edición colaborativa de registros mediante PostgreSQL.
- Llaves foráneas entre tablas personalizadas.
- Llaves foráneas hacia documentos mediante un selector desplegable con título, procedencia y fecha.
- Apertura directa del documento asociado desde una tabla.
- Búsqueda global sobre texto OCR, extracciones y tablas creadas por los usuarios.
- Detección de conflictos cuando dos usuarios intentan modificar simultáneamente un mismo registro.
- Auditoría de creación, modificación y eliminación de datos.
- Sesiones autenticadas persistentes entre pestañas del navegador.
- Ejecución completa mediante Docker Compose.

## Arquitectura

```text
Navegador
   |
   v
Aplicación Streamlit
   |---------------------> Servicio OCR HURIDOCS
   |
   +---------------------> PostgreSQL
   |
   +---------------------> Volumen persistente de PDFs
```

El entorno Docker contiene tres servicios principales:

| Servicio | Función |
| --- | --- |
| `app` | Aplicación web desarrollada con Streamlit |
| `db` | Base de datos PostgreSQL compartida |
| `ocr_service` | Servicio de OCR proporcionado por HURIDOCS |

PostgreSQL y HURIDOCS permanecen dentro de la red privada de Docker y no publican puertos directamente al host.

## Requerimientos

Para la instalación recomendada con Docker se necesita:

- Docker Engine 20.10 o superior, o una versión actual de Docker Desktop.
- Docker Compose v2.
- Navegador web moderno.
- Aproximadamente 10 GB o más de espacio libre en disco para imágenes, dependencias y modelos de OCR.
- CPU de varios núcleos recomendada.

El servicio HURIDOCS puede funcionar sin GPU, aunque el procesamiento OCR y de layout puede ser considerablemente más lento. Su documentación indica Docker Desktop 4.25+, Docker Engine 20.10+, Compose 2.0+, un mínimo de 2 GB de RAM y aproximadamente 10 GB de almacenamiento para el servicio y sus modelos. Para `db_visualizer` se recomienda disponer de memoria adicional si varios usuarios procesarán documentos simultáneamente.

## Instalación inicial

### 1. Clonar el repositorio

```bash
git clone <URL_DEL_REPOSITORIO>
cd db_visualizer
```

Si todavía no has publicado el repositorio en GitHub, utiliza la sección [Primera publicación en GitHub](#primera-publicación-en-github).

### 2. Crear el archivo de configuración

Linux/macOS:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Edita `.env` y define, como mínimo:

```env
POSTGRES_PASSWORD=una-clave-larga-y-aleatoria-para-postgresql
BOOTSTRAP_ADMIN_PASSWORD=otra-clave-larga-y-aleatoria-para-el-administrador
```

No utilices la misma contraseña para PostgreSQL y para la cuenta administradora.

El administrador inicial se crea únicamente cuando la tabla de usuarios está vacía. El usuario predeterminado es `admin`, pero puede modificarse mediante `BOOTSTRAP_ADMIN_USERNAME` antes del primer inicio.

Al iniciar sesión por primera vez, el administrador debe cambiar la contraseña inicial. Una vez que existen usuarios en la base de datos, `BOOTSTRAP_ADMIN_PASSWORD` deja de utilizarse.

### 3. Iniciar los servicios

```bash
docker compose up --build -d
```

Verifica el estado de los contenedores:

```bash
docker compose ps
```

Logs de la aplicación:

```bash
docker compose logs -f app
```

Logs del servicio OCR:

```bash
docker compose logs -f ocr_service
```

### 4. Abrir db_visualizer

En el navegador abre:

```text
http://localhost:8501
```

Inicia sesión con el administrador configurado en `.env` y cambia la contraseña inicial cuando la aplicación lo solicite.


### Reutilizar una imagen OCR ya descargada

Si el equipo ya tiene localmente una imagen funcional de HURIDOCS y no se desea descargar otra, se puede indicar explícitamente en `.env`. Por ejemplo:

```env
OCR_IMAGE=ghcr.io/huridocs/pdf-document-layout-analysis:latest
OCR_PULL_POLICY=never
```

Con `OCR_PULL_POLICY=never`, Docker utiliza exclusivamente la imagen almacenada en el equipo y falla si esa imagen no existe localmente. Esto es útil en conexiones limitadas; para instalaciones nuevas se recomienda `missing`.

## Actualización desde 1.0.x

La actualización a `1.1.0` conserva los volúmenes existentes. No es necesario recrear PostgreSQL ni volver a cargar los documentos.

```bash
git pull
docker compose up --build -d
```

Al iniciar, la aplicación agrega los nuevos metadatos documentales y reconcilia automáticamente los metadatos de tablas dinámicas afectados por diferencias de mayúsculas/minúsculas. Esta migración no elimina las filas de las tablas creadas por los usuarios.

No utilices `docker compose down -v` durante una actualización, ya que `-v` elimina deliberadamente los volúmenes persistentes.

## Trabajo colaborativo en una red local

Por seguridad, la configuración predeterminada publica la aplicación únicamente en `127.0.0.1`.

Para un uso temporal dentro de una LAN confiable, cambia en `docker-compose.yml`:

```yaml
127.0.0.1:${APP_PORT:-8501}:8501
```

por:

```yaml
0.0.0.0:${APP_PORT:-8501}:8501
```

Reinicia los servicios después del cambio.

Los demás usuarios podrán conectarse usando la dirección IP del computador que ejecuta Docker, por ejemplo:

```text
http://192.168.1.20:8501
```

No utilices este modo para exponer la aplicación directamente a Internet.

## Despliegue de producción con HTTPS

El archivo `docker-compose.production.yml` agrega un reverse proxy Caddy. Caddy recibe las conexiones HTTPS y reenvía las solicitudes al contenedor de Streamlit mediante la red privada de Docker.

Antes de publicar la aplicación:

1. Configura un dominio o subdominio apuntando a la IP pública del servidor.
2. Permite los puertos TCP 80 y 443 en el firewall. UDP 443 puede habilitarse para HTTP/3.
3. Configura el dominio en `.env`:

```env
APP_DOMAIN=db.example.org
```

4. Utiliza contraseñas de producción largas, únicas y aleatorias.
5. Inicia el entorno:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up --build -d
```

La configuración de producción fuerza cookies de autenticación seguras (`Secure`). Caddy administra automáticamente los certificados TLS cuando el dominio y la red están correctamente configurados.

En un despliegue público no expongas directamente PostgreSQL ni el API de HURIDOCS.

## Variables de entorno

| Variable | Valor predeterminado | Descripción |
| --- | --- | --- |
| `POSTGRES_DB` | `db_visualizer` | Nombre de la base PostgreSQL |
| `POSTGRES_USER` | `db_visualizer` | Usuario de PostgreSQL |
| `POSTGRES_PASSWORD` | obligatorio | Contraseña de PostgreSQL |
| `BOOTSTRAP_ADMIN_USERNAME` | `admin` | Usuario administrador inicial |
| `BOOTSTRAP_ADMIN_FULL_NAME` | `Administrador` | Nombre visible del administrador inicial |
| `BOOTSTRAP_ADMIN_PASSWORD` | obligatorio | Contraseña inicial del administrador |
| `APP_PORT` | `8501` | Puerto local de la aplicación |
| `AUTH_SESSION_DAYS` | `7` | Duración de las sesiones autenticadas |
| `AUTH_COOKIE_SECURE` | `false` | Requiere HTTPS para la cookie; producción lo fuerza a `true` |
| `MIN_PASSWORD_LENGTH` | `12` | Largo mínimo permitido para contraseñas |
| `SEARCH_RESULT_LIMIT` | `100` | Límite de resultados por categoría de búsqueda |
| `OCR_IMAGE` | `huridocs/pdf-document-layout-analysis:v0.0.35` | Imagen del servicio OCR |
| `OCR_PULL_POLICY` | `missing` | Política de descarga de la imagen OCR (`missing` o `never`) |
| `OCR_LANGUAGE` | `es` | Idioma enviado al servicio OCR |
| `OCR_TIMEOUT_SECONDS` | `600` | Tiempo máximo de una solicitud OCR |
| `OCR_USE_FAST_MODE` | `false` | Habilita el modo rápido de HURIDOCS cuando aplica |
| `OCR_ROTATE_PAGES` | `true` | Permite rotar páginas durante el OCR |
| `OCR_DESKEW` | `true` | Corrige inclinación de páginas durante el OCR |
| `APP_DOMAIN` | sin valor | Dominio utilizado por la configuración HTTPS |

El archivo `.env` contiene secretos y **nunca debe subirse a GitHub**. Está excluido mediante `.gitignore`.

## Persistencia de datos

Docker utiliza volúmenes persistentes para que los datos sobrevivan a reinicios y reconstrucciones:

- `db_visualizer_postgres_data`: base de datos PostgreSQL.
- `db_visualizer_uploaded_files`: PDFs originales y procesados con OCR.
- `db_visualizer_caddy_data`: certificados y datos TLS en producción.
- `db_visualizer_caddy_config`: configuración interna de Caddy en producción.

Detener los contenedores normalmente no elimina información:

```bash
docker compose down
docker compose up -d
```

No ejecutes:

```bash
docker compose down -v
```

salvo que quieras eliminar deliberadamente la base de datos y los documentos persistentes.

## Reiniciar desde una instalación vacía

Si todavía no existen datos valiosos y deseas comenzar completamente desde cero:

```bash
docker compose down -v
```

Después vuelve a iniciar el entorno:

```bash
docker compose up --build -d
```

El comando con `-v` elimina tanto PostgreSQL como los documentos almacenados en los volúmenes de Docker.

## Copias de seguridad

Una copia de seguridad de producción debe incluir **la base de datos y los archivos PDF**.

Para generar un dump SQL de PostgreSQL:

```bash
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > db_visualizer.sql
```

En PowerShell también puedes utilizar directamente los valores definidos en `.env` si el manejo de variables de tu shell es diferente.

Los PDFs deben respaldarse desde el volumen `db_visualizer_uploaded_files` o migrarse a un almacenamiento persistente externo.

Un backup no debe considerarse confiable hasta haber probado su restauración.

## Gestión de usuarios

Los administradores pueden crear usuarios y restablecer contraseñas desde la sección **Cuenta**.

También existe una utilidad de línea de comandos.

Listar usuarios:

```bash
docker compose exec app python scripts/manage_users.py list
```

Crear un usuario:

```bash
docker compose exec app python scripts/manage_users.py add USUARIO "Nombre Completo" investigador CONTRASEÑA_TEMPORAL
```

Restablecer una contraseña:

```bash
docker compose exec app python scripts/manage_users.py reset-password USUARIO CONTRASEÑA_TEMPORAL
```

Las contraseñas temporales deben cumplir el largo mínimo configurado y deben reemplazarse posteriormente.

## Desarrollo y pruebas

La imagen Docker de producción instala únicamente dependencias necesarias en ejecución. Las herramientas de pruebas están separadas en `requirements-dev.txt`.

Crear entorno virtual:

```bash
python -m venv .venv
```

Activar en Linux/macOS:

```bash
source .venv/bin/activate
```

Activar en PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Instalar dependencias:

```bash
pip install -r requirements-dev.txt
```

Ejecutar pruebas:

```bash
pytest -q
```

Comprobar sintaxis de los módulos Python:

```bash
python -m compileall -q app.py config.py src pages scripts
```

GitHub Actions ejecuta estas comprobaciones automáticamente en los `push` y `pull requests` dirigidos a `main`.

## Estructura del repositorio

```text
db_visualizer/
├── .github/                    # CI y configuración de pull requests
├── .streamlit/                 # Configuración de Streamlit
├── pages/                      # Páginas de la aplicación
├── scripts/                    # Utilidades administrativas
├── src/                        # Base de datos, auth, OCR, búsqueda y tablas
├── tests/                      # Pruebas automatizadas
├── app.py                      # Punto de entrada de Streamlit
├── config.py                   # Configuración central
├── Caddyfile                   # Reverse proxy HTTPS para producción
├── docker-compose.yml          # Stack principal
├── docker-compose.production.yml
├── Dockerfile
├── requirements.txt            # Dependencias de producción
├── requirements-dev.txt        # Dependencias de desarrollo/pruebas
├── VERSION                     # Versión actual
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
└── README.md
```

## Flujo de trabajo con Git y GitHub

El nombre oficial del proyecto es **`db_visualizer`**. La primera versión estable del repositorio es **`v1.0.1`**. Las denominaciones internas anteriores utilizadas durante el prototipado no forman parte del versionado oficial.

Modelo de ramas recomendado:

- `main`: código estable y desplegable.
- `feature/<nombre>`: nuevas funcionalidades.
- `fix/<nombre>`: correcciones de bugs.
- `docs/<nombre>`: cambios exclusivamente documentales.

Ejemplo de trabajo diario:

```bash
git switch main
git pull
git switch -c feature/mi-cambio
# realizar cambios
git add .
git commit -m "Add my change"
git push -u origin feature/mi-cambio
```

Luego crea un pull request hacia `main`. La recomendación es fusionarlo únicamente cuando las pruebas de CI hayan terminado correctamente.

## Primera publicación en GitHub

Crea primero un repositorio vacío llamado `db_visualizer` en GitHub.

Desde esta carpeta ejecuta:

```bash
git init
git branch -M main
git add .
git commit -m "Release db_visualizer 1.0.0"
git remote add origin https://github.com/<TU_USUARIO>/db_visualizer.git
git push -u origin main
```

Después crea el tag del primer release oficial:

```bash
git tag -a v1.0.1 -m "db_visualizer 1.0.0"
git push origin v1.0.1
```

En GitHub puedes crear un **Release** a partir de ese tag y utilizar el contenido de `CHANGELOG.md` como notas de la versión.

## Versionado

`db_visualizer` utiliza versionado semántico:

```text
MAJOR.MINOR.PATCH
```

Ejemplos:

- `1.0.1`: corrección compatible de un bug.
- `1.1.0`: nueva funcionalidad compatible.
- `2.0.0`: cambio incompatible que puede requerir migración o cambios de uso.

En cada release deben actualizarse `VERSION` y `CHANGELOG.md`.

## Seguridad

- Las contraseñas se almacenan mediante hashes bcrypt.
- Los tokens de sesión son aleatorios y PostgreSQL conserva únicamente su hash.
- Los cambios de contraseña revocan sesiones anteriores.
- PostgreSQL y HURIDOCS se mantienen en la red privada de Docker.
- La aplicación escucha únicamente en localhost por defecto.
- El despliegue HTTPS utiliza cookies `Secure`.
- PDFs, archivos OCR, `.env` y dumps de base de datos están excluidos de Git.
- El contenedor de la aplicación se ejecuta con un usuario sin privilegios de root.

Consulta [SECURITY.md](SECURITY.md) antes de publicar la aplicación en Internet.

## Dependencia OCR

`db_visualizer` utiliza HURIDOCS `pdf-document-layout-analysis` para realizar OCR. La configuración Docker fija la imagen de Docker Hub `huridocs/pdf-document-layout-analysis:v0.0.35`, en lugar de utilizar `latest`, para evitar cambios inesperados de comportamiento provocados por actualizaciones automáticas de la dependencia.

Proyecto original:

https://github.com/huridocs/pdf-document-layout-analysis

## Licencia

Esta versión de `db_visualizer` no incluye todavía una licencia propia. Antes de publicar el código como proyecto open source, agrega un archivo `LICENSE` con los términos que quieras aplicar. Las dependencias y servicios de terceros mantienen sus respectivas licencias.
