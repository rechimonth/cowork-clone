# TODO - cowork-clone (Fase 1: Python CLI + HITL)

- [x] Crear estructura base del proyecto (carpeta `cowork-clone/`).
- [x] Implementar `main.py` (orquestación: scan -> plan -> HITL -> execution).
- [x] Implementar `models.py` (schemas para `ExecutionPlan`, `RenameAction`, etc.).
- [x] Implementar `file_manager.py` (escaneo directorios + lectura texto + extracción PDFs).
- [x] Implementar `ai_engine.py` (capa LLM; MVP con stub seguro).
- [x] Implementar `planner.py` (traduce extracción a `ExecutionPlan`).
- [x] Implementar `user_validation.py` (HITL: mostrar plan + confirmación Y/N).
- [x] Implementar `os_commands.py` (whitelist seguro: `mkdir`, `rename`; bloquear cualquier operación destructiva).
- [x] Implementar `audit_logger.py` (registro de acciones y decisiones del usuario).
- [x] Agregar `requirements.txt`.
- [x] Agregar `README.md` con instrucciones para correr y probar.
- [x] Probar localmente con una carpeta de ejemplo y verificar que NO se ejecuta nada sin Y/N.
- [x] Conectar un LLM real (Ollama/llama.cpp) + parseo robusto del JSON con Pydantic.
- [x] Añadir validación adicional: evitar colisiones de nombres y garantizar unicidad del destino.
- [x] HITL con timeout (60s) y límite de entradas inválidas (aborta por seguridad).
- [x] Endpoint/API key del LLM configurables por entorno (`OLLAMA_ENDPOINT`, `OLLAMA_API_KEY`).
- [x] Desacoplar I/O (`CoworkAgent` + `AgentCallbacks`) para reutilizar el orquestador desde una API.
- [x] Endurecer seguridad: path traversal, colisiones, saneamiento de nombres, whitelist de operaciones.
- [x] Configurar calidad: `ruff`, `mypy`, `pytest-cov` con umbral, `bandit` y CI en GitHub Actions.
- [x] Exponer el agente como API FastAPI y aprobar el plan vía HTTP/WebSocket (frontend Tauri).

## Fase 2 (Backend + Frontend)

- [x] `POST /scan` y `POST /plan` en FastAPI devolviendo el `ExecutionPlan` serializado.
- [x] `POST /approve` que resuelva `ApprovalDecision` sin bloquear la terminal.
- [x] Canal WebSocket para emitir progreso y recibir la decisión del frontend.
- [x] Autenticación y autorización por usuario antes de invocar `CoworkAgent`.
- [x] Ejecución en worker de fondo con estado consultable.
- [x] Cliente Tauri/React consumiendo la API.

## Deuda técnica pendiente

- [ ] Cerrar la ventana TOCTOU entre validación de rutas y ejecución (ver `SECURITY.md`).
- [ ] Incorporar `experimental/browser_agent.py` y `dashboard.py` al flujo principal
      cuando el camino base esté cerrado.
- [ ] Evaluar si `storage_manager.py` debe reemplazar el log JSONL por consultas SQLite.


