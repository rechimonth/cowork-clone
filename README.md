# cowork-clone (MVP)

## Qué hace

- Escanea un directorio local (recursivo por defecto).
- Lee previews de `.txt/.md/.csv/.log` y de PDFs (limitado a las primeras páginas).
- Clasifica cada archivo con un LLM (`OllamaProvider`) y sugiere un nombre.
- Genera un **Plan de Ejecución** usando un planner seguro.
- Muestra el plan y requiere confirmación explícita `[A]/[R]/[V]`.
- Ejecuta únicamente operaciones seguras tras la aprobación.

> **Seguridad:** el sistema NO soporta borrados (`rm`/`unlink`/`rmdir`/`remove`).
> Solo se permiten `mkdir` y `rename`, y cualquier ruta fuera del directorio raíz
> es rechazada por whitelist y validación de path traversal.

## Setup

Requiere Python 3.11+.

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
- `--log cowork-clone.log`: ubicación del log de auditoría.
- `--dry-run`: muestra el plan sin ejecutar ningún cambio local.

Ejemplo en modo simulación:

```bash
python main.py "./mi_carpeta" --dry-run --log audit.jsonl
```

## Tests

```bash
python -m pytest tests -q
```

## Estructura

- `main.py`: orquestación `scan -> plan -> HITL -> execute` (clase `CoworkAgent`).
- `planner.py`: traduce la extracción a un `ExecutionPlan`.
- `ai_engine.py`: capa LLM (Ollama) + parseo robusto del JSON + fallbacks.
- `file_manager.py`: escaneo de directorios, lectura de texto y previews de PDF.
- `os_commands.py`: whitelist segura (`mkdir`, `rename`) con protección path traversal.
- `user_validation.py`: HITL (aprobación con timeout).
- `audit_logger.py`: registro de acciones, decisiones y errores.
- `models.py`: schemas Pydantic compartidos.
- `transaction_manager.py`, `storage_manager.py`: soporte de rollback y persistencia (usados por tests).
- `experimental/`: módulos exploratorios fuera del flujo principal.

## Próximas mejoras recomendadas

- Validar el JSON del LLM con Pydantic de forma estricta en todos los caminos.
- Exponer el agente como API (`FastAPI`) y desacoplar la aprobación HITL del terminal
  para integrarla con un frontend de React/Tauri.
- Añadir herramientas adicionales (browser, automatización) en fases posteriores.
- Métricas y dashboard operativo (ver `experimental/`).
