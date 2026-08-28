## Resumen

Describe el cambio y el problema que resuelve.

## Validación

- [ ] `python -m compileall -q app.py config.py src pages scripts`
- [ ] `pytest -q`
- [ ] El flujo de interfaz relacionado fue probado manualmente

## Impacto sobre datos o configuración

Indica cualquier migración de base de datos, variable de entorno, cambio en OCR, almacenamiento o despliegue. Escribe `Ninguno` si no corresponde.

## Checklist

- [ ] No se incluyeron credenciales, PDFs privados, dumps de base de datos ni archivos `.env`.
- [ ] La documentación fue actualizada cuando cambió el comportamiento o configuración.
- [ ] Se consideró compatibilidad con datos persistentes existentes.
