# cowork-clone (MVP)

## Qué hace (Fase 1)
- Escanea un directorio local.
- Lee previews de `.txt/.md/.csv/.log` y de PDFs (limitado a primeras páginas).
- Genera un **Plan de Ejecución** (solo `mkdir` y `rename`) usando un planner seguro.
- Muestra el plan y requiere confirmación explícita `Y/N`.
- Ejecuta únicamente operaciones seguras tras aprobación.

> Seguridad: el sistema NO soporta borrados (rm/unlink/rmdir/remove). Está bloqueado por whitelist.

## Setup

```bash
cd cowork-clone
pip install -r requirements.txt
```

## Ejecución

```bash
python main.py "C:\\ruta\\a\\tu\\carpeta"
```

Opciones:
- `--no-recursive`: escanea solo el nivel superior.
- `--log cowork-clone.log`: ubicación del log.

## Próximas mejoras recomendadas
- Conectar un LLM real (Ollama/llama.cpp) y parsear con Pydantic validando el JSON.
- Añadir `audit_logger` más detallado.
- Añadir herramientas adicionales (browser, automatización) en fases posteriores.

