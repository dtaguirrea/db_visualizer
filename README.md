# db_visualizer 3.1.0

Plataforma colaborativa de investigación histórica y documental. Interfaz en español,
con Streamlit, PostgreSQL 17 y OCR HURIDOCS ejecutado por CPU.

## Funcionalidades

- Carga, búsqueda, edición y visualización de PDF; descarga de la copia con OCR.
- Cola OCR persistente de hasta 20 documentos, procesados secuencialmente.
- Usuarios, sesiones, auditoría y detección de conflictos al editar registros.
- Tablas personalizadas, llaves foráneas, campos con opciones y agregado de columnas.
- Fechas históricas completas o parciales, sin inventar precisión.
- Importación transaccional y exportación CSV. Importar agrega filas; no deduplica.
- **Gráficos:** distribuciones, porcentajes, series anuales y rankings por relaciones.

Los datos y PDF se conservan en volúmenes Docker. Detener la app no los borra.

## Uso diario: iniciar y cerrar

Requisitos: Docker Desktop abierto y funcionando, PowerShell y una instalación ya
configurada en `C:\db_visualizer`.

**Iniciar todos los servicios:**

```powershell
Set-Location C:\db_visualizer
docker compose up -d --no-build
docker compose ps
```

Abre <http://localhost:8501>. Se espera que `db` y `app` estén `healthy` una vez
terminado el arranque; `ocr_service` y `ocr_worker` deben estar en ejecución.
El worker no tiene healthcheck HTTP: no mostrar `healthy` no es un error.
Si falta una imagen, sigue la instalación inicial o la actualización; no se construye
una imagen automáticamente con `--no-build`.

**Cerrar tu sesión:** usa **Cerrar sesión** en la barra lateral. Cerrar una pestaña no
detiene el servidor ni necesariamente cierra la sesión persistente del navegador.

**Detener toda la aplicación:**

```powershell
Set-Location C:\db_visualizer
docker compose stop
docker compose ps -a
```

Los contenedores deben figurar detenidos (`Exited`). Puedes cerrar Docker Desktop
después. Los demás investigadores perderán acceso mientras el servidor esté detenido.
Si se interrumpe un trabajo OCR, el worker lo devolverá a la cola al volver a iniciar.
Para reanudar, utiliza los mismos comandos de inicio.

**Ver errores:**

```powershell
docker compose logs --tail 100 app ocr_worker
docker compose logs --tail 100 ocr_service
```

Si agregas `-f`, `Ctrl+C` deja de seguir los logs; no detiene los servicios.
No uses `docker compose down -v`: elimina los volúmenes que contienen los datos.

## Primera instalación, solo si no existe una instalación previa

1. Instala Docker Desktop con contenedores Linux y extráe el ZIP en
   `C:\db_visualizer`. `VERSION` y `docker-compose.yml` deben estar directamente
   dentro de esa carpeta.
2. Crea el archivo de configuración **sin sobrescribir uno existente**:

```powershell
Set-Location C:\db_visualizer
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Completa `POSTGRES_PASSWORD` y `BOOTSTRAP_ADMIN_PASSWORD` con contraseñas propias.
La contraseña inicial del administrador debe tener al menos 12 caracteres.
No publiques `.env`. No cambies la contraseña de PostgreSQL en `.env` como método
de actualización: cambiar esa variable no cambia la contraseña de una base existente.

3. Construye e inicia:

```powershell
docker compose build ocr_service app
if ($LASTEXITCODE -ne 0) { throw 'Falló la construcción; revisa el error antes de continuar.' }
docker compose run --rm --no-deps --entrypoint python app -c "from pathlib import Path; print(Path('/app/VERSION').read_text())"
if ($LASTEXITCODE -ne 0) { throw 'No se pudo verificar la imagen.' }
# Debe mostrar 3.1.0. Si muestra otra versión, no continúes.
docker compose up -d --no-build
docker compose ps
```

La primera construcción descarga el motor OCR y puede tardar bastante.
Entra en <http://localhost:8501> con el administrador definido en `.env` y cambia
su contraseña cuando se solicite. Desde **Cuenta**, el administrador puede crear usuarios.

## Actualizar una instalación 3.0.0

No reemplaces `.env`, `.git` ni volúmenes. Conserva el nombre del proyecto Compose
(`db_visualizer`) para seguir utilizando los mismos volúmenes.

### 1. Respaldo obligatorio, antes de copiar código

Ejecuta los bloques en la misma ventana de PowerShell. Si falla un paso, detente.
El respaldo contiene información privada y debe guardarse en un lugar protegido.

```powershell
$ErrorActionPreference = 'Stop'
Set-Location C:\db_visualizer
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = "C:\db_visualizer_backups\pre_310_$stamp"
New-Item -ItemType Directory -Path $backup | Out-Null
docker compose up -d db
if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar PostgreSQL.' }
$appId = (docker compose ps -aq app | Select-Object -First 1)
if (-not $appId) { throw 'No se encuentra el contenedor app para respaldar los PDF.' }
docker compose stop app ocr_worker
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron detener los procesos que escriben datos.' }
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/pre_310.dump'
if ($LASTEXITCODE -ne 0) { throw 'Falló el respaldo de PostgreSQL. No continúes.' }
docker compose exec -T db pg_restore --list /tmp/pre_310.dump
if ($LASTEXITCODE -ne 0) { throw 'El respaldo no pudo ser leído por pg_restore.' }
docker compose cp db:/tmp/pre_310.dump "$backup\database.dump"
if ($LASTEXITCODE -ne 0) { throw 'No se pudo copiar el respaldo de PostgreSQL.' }
docker cp "${appId}:/app/uploaded_files" "$backup\uploaded_files"
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron respaldar los PDF.' }
Copy-Item -LiteralPath 'C:\db_visualizer' -Destination "$backup\source" -Recurse
Get-Item "$backup\database.dump"
Get-ChildItem "$backup\uploaded_files"
Write-Host "Respaldo guardado en $backup"
```

`database.dump` debe existir y tener contenido. Revisa que los PDF esperados estén
en el respaldo. La lectura con `pg_restore --list` comprueba el catálogo del dump;
no sustituye una prueba de restauración completa. No copies el código nuevo si falla el respaldo.

### 2. Copiar únicamente el código de la entrega

```powershell
$zip = "$env:USERPROFILE\Downloads\db_visualizer_3.1.0.zip"
$stage = "C:\db_visualizer_stage_310_$stamp"
Expand-Archive -LiteralPath $zip -DestinationPath $stage
if ((Get-Content "$stage\VERSION" -Raw).Trim() -ne '3.1.0') {
    throw 'El ZIP no contiene la versión esperada en su raíz.'
}
$files = @('app.py', 'config.py', 'VERSION', 'requirements.txt', 'requirements-dev.txt',
    'Dockerfile', 'Dockerfile.ocr', 'docker-compose.yml', 'docker-compose.production.yml',
    'Caddyfile', '.dockerignore', '.env.example', 'README.md', 'CHANGELOG.md', 'VALIDATION.md')
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $stage $file) -Destination (Join-Path 'C:\db_visualizer' $file) -Force
}
foreach ($folder in @('src', 'pages', 'scripts', 'tests')) {
    New-Item -ItemType Directory -Path "C:\db_visualizer\$folder" -Force | Out-Null
    Copy-Item -Path "$stage\$folder\*" -Destination "C:\db_visualizer\$folder" -Recurse -Force
}
```

Esta copia conserva los archivos ajenos a la entrega. No copia `.env` ni modifica `.git`.

### 3. Construir una vez y verificar la versión antes de arrancar

```powershell
Set-Location C:\db_visualizer
Get-Content VERSION
docker compose build app
if ($LASTEXITCODE -ne 0) { throw 'Falló la construcción.' }
docker compose run --rm --no-deps --entrypoint python app -c "from pathlib import Path; print(Path('/app/VERSION').read_text())"
if ($LASTEXITCODE -ne 0) { throw 'No se pudo verificar la imagen.' }
```

Ambas lecturas deben mostrar **3.1.0**. App y worker comparten la misma imagen;
no hace falta construirla dos veces. No cambió el motor OCR en esta entrega.

Si la versión de la imagen es incorrecta, no repitas builds indiscriminadamente:
comprueba `docker compose config --services`, `docker compose config --images`,
el directorio actual y los archivos `COMPOSE_FILE`/overrides que uses. La salida
completa de `docker compose config` puede contener contraseñas; no la compartas.

Como alternativa al problema de contexto detectado en instalaciones anteriores,
construye desde un TAR del directorio de preparación, que contiene solo esta entrega:

```powershell
$buildTar = "C:\db_visualizer_source_310_$stamp.tar"
tar -cf $buildTar -C $stage .
if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el TAR.' }
cmd.exe /d /c "docker build -t db_visualizer-app:3.1.0 - < `"$buildTar`""
if ($LASTEXITCODE -ne 0) { throw 'Falló la construcción desde TAR.' }
docker compose run --rm --no-deps --entrypoint python app -c "from pathlib import Path; print(Path('/app/VERSION').read_text())"
```

El envío binario lo hace `cmd.exe`, no una tubería de texto de PowerShell.
Continúa solo cuando la imagen muestre la versión correcta.

### 4. Arrancar y comprobar

```powershell
docker compose up -d --no-build
if ($LASTEXITCODE -ne 0) { throw 'Falló el inicio de los servicios.' }
docker compose ps
docker compose exec -T app cat /app/VERSION
docker compose exec -T ocr_worker cat /app/VERSION
docker compose logs --tail 80 app ocr_worker
```

Ambos contenedores deben mostrar `3.1.0`. Comprueba login, registros, descarga de un
PDF y **Gráficos**. Contrasta un gráfico con una cuenta manual de pocos registros.
No borres el respaldo después de estas verificaciones.

La migración 3.1.0 recalcula únicamente los límites derivados para buscar fechas
parciales y registra su ejecución. Conserva las fechas originales. Si vienes de
una versión anterior a 3.0.0, también se ejecuta la migración heredada que convierte
columnas de fechas `DATE` a `TEXT`: prueba esa actualización primero sobre una copia
de la base. Una instalación que ya registró 3.0.0 no repite esa conversión.

## Cómo usar los gráficos

Abre **Datos → Gráficos**. Las tablas Personas y Organizaciones se eligen por su
nombre real; no se crean tablas de ejemplo ni se insertan datos al abrir la sección.

| Vista | Uso |
| --- | --- |
| Explorar tablas | Elige tabla y columna; muestra cantidades, porcentajes, barras, circular o tabla. |
| Porcentaje destacado | Selecciona una o varias categorías, por ejemplo `Mujer` y `Femenino`. |
| Filtro opcional | Limita el análisis a un valor de otra columna, por ejemplo nacionalidad Chile. |
| Fechas | Agrupa por año conocido; informa cuántos registros no tienen fecha, año o fecha válida. |
| Documentos | Año, autor, procedencia, formato, idioma configurado para OCR y estado de procesamiento. |
| Relaciones | Ranking por una FK real. Puede contar filas o IDs distintos de otra FK de la tabla intermedia. |

El porcentaje usa todos los registros seleccionados o solo los que tienen valor,
según la casilla **Incluir registros sin información**. El denominador se muestra
siempre. Los espacios externos y los vacíos se normalizan para el análisis, sin
cambiar la base; no se fusionan automáticamente variantes como `Mujer` y `mujer`.
`F` no se interpreta automáticamente como mujer: selecciona explícitamente el valor
que corresponda. Las primeras categorías pueden agruparse con el resto en
**Otras categorías (agrupadas)**, conservando el total. El CSV conserva todo el resumen.

### Ideas que puedes realizar con esta versión

| Pregunta | Tabla/columna o relación necesaria | Presentación sugerida |
| --- | --- | --- |
| ¿Qué porcentaje de personas son mujeres? | Personas → género; seleccionar categorías pertinentes | Indicador y circular |
| ¿Qué nacionalidades predominan? | Personas → nacionalidad | Barras |
| ¿Qué profesiones o roles aparecen más? | Personas → profesión o rol | Barras |
| ¿Dónde están las organizaciones? | Organizaciones → país o ciudad | Barras |
| ¿Qué tipos de organización predominan? | Organizaciones → tipo/categoría | Barras o circular |
| ¿Cómo se distribuye el archivo en el tiempo? | Documentos → fecha | Barras por año |
| ¿Cuántos documentos faltan por procesar? | Documentos → estado OCR | Barras |
| ¿Qué documento tiene más personas vinculadas? | Personas → documento, o tabla intermedia Persona–Documento | Ranking; IDs distintos si hay tabla intermedia |
| ¿Qué organizaciones tienen más documentos? | Tabla intermedia Organización–Documento | Ranking de documentos distintos por organización |

Las relaciones dependen de las FK existentes. Un vínculo a un documento no demuestra
por sí solo que la persona esté mencionada: puede indicar autoría u otro rol.
El ranking muestra exactamente la relación seleccionada, sin interpretar su significado.
Los porcentajes describen los registros de la base, no una población histórica completa.

Cruces de dos categorías (género por nacionalidad), mapas y redes quedan como ideas
para una entrega posterior. La vista actual permite filtrar una categoría a la vez.

## OCR y acceso remoto

Se mantiene `/ocr` más extracción local con `pypdf`; `/text` no se activa por defecto.
La variable `OCR_TEXT_EXTRACTION_MODE=local` conserva ese comportamiento.
`ocr_language` es el idioma solicitado al motor, no el idioma detectado del documento.

Con Tailscale Serve ya configurado, el equipo puede entrar por la dirección habitual
del servidor mientras el computador y los servicios estén encendidos. `localhost`
solo funciona en el computador servidor. Esta entrega no modifica Tailscale.

## Pruebas para desarrollo (opcionales)

En una terminal con Python 3.11 o superior, desde la raíz del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Las pruebas utilizan SQLite temporal y datos sintéticos. No apuntan a la base de
producción. Consulta `VALIDATION.md` para los resultados y límites de esta entrega.
