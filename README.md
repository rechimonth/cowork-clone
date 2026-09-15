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
pytest --cov
```

Cubre la suite principal y `experimental/tests`. Umbral mínimo de cobertura
configurado en `pyproject.toml`.

## Calidad

```bash
ruff check .     # lint
ruff format .    # formato
mypy .           # tipos
bandit -r . -x ./tests,./experimental/tests
```

Todo esto corre en CI (`.github/workflows/ci.yml`) sobre Python 3.11 y 3.13.

## Variables de entorno

| Variable | Default | Descripción |
| --- | --- | --- |
| `OLLAMA_ENDPOINT` | `http://localhost:11434/api/generate` | Endpoint del LLM. Usar HTTPS fuera de localhost. |
| `OLLAMA_API_KEY` | *(vacío)* | Se envía como `Authorization: Bearer`. |

Si el endpoint usa HTTP sin cifrado y no apunta a localhost, el agente emite un
warning y registra `INSECURE_ENDPOINT` en la auditoría.

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

## Documentación

- [`ARCHITECTURE.md`](ARCHITECTURE.md): capas, invariantes de seguridad y camino a FastAPI + Tauri.
- [`SECURITY.md`](SECURITY.md): modelo de amenazas, controles y limitaciones conocidas.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): setup, reglas del proyecto y flujo de verificación.
- [`TODO.md`](TODO.md): estado de la Fase 1 y siguientes pasos.

## Próximas mejoras recomendadas

- Exponer el agente como API (`FastAPI`) y aprobar el plan vía HTTP/WebSocket
  para integrarlo con un frontend de React/Tauri.
- Endurecer la validación Pydantic del plan en el borde de la API.
- Incorporar herramientas adicionales (browser, automatización) desde
  `experimental/` cuando el flujo base esté cerrado.
- Métricas y dashboard operativo (ver `experimental/`).
