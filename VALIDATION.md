# Validación de la entrega 3.1.0

## Ejecutado

- `python -m pytest -q`: 19 pruebas aprobadas.
- Validación de porcentajes con y sin valores faltantes y después de filtrar.
- Agrupación de categorías conservando cantidades y porcentajes.
- Series anuales con fechas completas, parciales, sin año e inválidas.
- Rankings de tablas intermedias: vínculos repetidos, IDs distintos y destinos sin vínculos.
- Rechazo de tablas internas/columnas no autorizadas y filtros tratados como parámetros.
- Limpieza del estado privado de sesión.
- CSV inválido sin inserciones parciales y con identificación de fila/columna.
- Agregado de columnas conservando registros y auditoría.
- Conflictos de edición y bloqueo de eliminación de registros referenciados.
- Migración de límites de fechas ejecutada dos veces para comprobar idempotencia.
- Streamlit AppTest: vistas vacías, documentos, ausencia de relaciones, porcentaje
  destacado, cambio de denominador y gráfico circular.

Entorno: Python 3.12, SQLite temporal, Streamlit 1.66.0, SQLAlchemy 2.1.4,
Pandas 2.2.3 y Altair 6.3.0. Todos los datos de prueba son sintéticos.

## Pendiente de comprobar en el servidor

- PostgreSQL 17 y migración sobre una copia de la base real.
- Construcción e inicio de Docker Desktop en Windows; comandos PowerShell del README.
- OCR real con HURIDOCS y descarga de los PDF existentes.
- Cookies, logout y navegación en un navegador real; AppTest no ejecuta el componente
  JavaScript de cookies ni representa una inspección visual completa.
- Rendimiento con el volumen real de datos y varias sesiones simultáneas.

## Límites conocidos

- Gráficos por categoría: máximo 1.000 valores distintos después del filtro.
  Fechas: hasta 10.000 valores distintos antes de agrupar por año. Al superar el
  límite se informa el problema; no se presenta una muestra como si fuera el total.
- Los datos faltantes no se imputan. Las entidades duplicadas necesitan limpieza
  por parte del equipo; los gráficos cuentan registros o IDs según la selección.
- El editor de Tablas conserva la carga completa de registros de 3.0.0; esta entrega
  no introduce paginación. Los gráficos utilizan agregación SQL independiente.
- Se mantiene el healthcheck del worker desactivado. Su estado se revisa con logs.
- La migración antigua de 3.0.0 sigue existiendo para instalaciones anteriores;
  esta entrega no valida esa conversión PostgreSQL con datos de producción.
- Las contraseñas y el flujo de autenticación heredados no recibieron una auditoría
  de seguridad completa. La corrección de sesión se limita a limpiar el estado privado.
