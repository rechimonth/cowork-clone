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

## API (FastAPI)

El mismo ciclo `scan -> plan -> HITL -> execute` está expuesto por HTTP y
WebSocket para que un frontend (React/Tauri) lo maneje sin bloquear la terminal.
El orquestador es único: CLI y API comparten `CoworkAgent`.

```bash
export COWORK_API_TOKEN="un-token-largo-y-secreto"
uvicorn api.app:create_app --factory --host 127.0.0.1 --port 8000
```

`create_app` es un *factory*: uvicorn necesita `--factory`.

Endpoints (todos requieren `Authorization: Bearer <token>` salvo `/health`):

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/health` | Estado del servicio (público). |
| `POST` | `/sessions` | Crea una sesión sobre un `root_dir` autorizado. |
| `GET` | `/sessions` | Lista las sesiones activas. |
| `GET` | `/sessions/{id}` | Estado y plan de la sesión. |
| `GET` | `/sessions/{id}/plan` | Solo el plan propuesto. |
| `POST` | `/sessions/{id}/approval` | Resuelve el HITL: `{"decision": "approve"\|"reject"}`. |
| `DELETE` | `/sessions/{id}` | Descarta una sesión terminada. |
| `GET` | `/sessions/{id}/events` | Eventos acumulados (polling). |
| `WS` | `/sessions/{id}/ws?token=...` | Stream de eventos en vivo. |

La configuración es explícita y sin valores peligrosos por defecto:

| Variable | Default | Descripción |
| --- | --- | --- |
| `COWORK_API_TOKEN` | *(ninguno)* | Obligatorio; sin él la API no arranca. Mínimo 16 caracteres. |
| `COWORK_ALLOWED_ROOTS` | *(ninguno)* | Raíces permitidas, separadas por comas. Vacío = cualquiera salvo las rutas sensibles bloqueadas. |
| `COWORK_STATE_DIR` | `./api-state` | Directorio de estado: snapshots de sesión, historial de eventos y logs de auditoría. |
| `COWORK_CORS_ORIGINS` | dev server de Vite + Tauri | Orígenes permitidos, separados por comas. |
| `COWORK_APPROVAL_TIMEOUT_S` | `300` | Timeout del HITL: al expirar se rechaza por seguridad. |
| `COWORK_ALLOW_INSECURE` | `false` | Permite correr sin token y sin TLS (solo para desarrollo local). |
| `COWORK_MAX_SESSIONS` | `50` | Sesiones concurrentes máximas. |

Controles de seguridad destacados:

- Las sesiones solo pueden operar dentro de un `root_dir` autorizado; `/`, `/etc`,
  `/usr` y el resto de raíces del sistema están bloqueados.
- La aprobación exige que la sesión esté en `awaiting_approval`; si no, responde
  `409` en lugar de aprobar por accidente.
- El token se acepta por cabecera `Authorization`, por query param o por
  subprotocolo WebSocket (útil para clientes que no pueden fijar cabeceras).
- Los eventos de cada sesión quedan en un log JSONL correlacionado por
  `session_id`.

## Persistencia entre reinicios

Las sesiones viven en memoria, pero su estado sobrevive a un reinicio del
backend. En `COWORK_STATE_DIR` se guarda, por sesión:

- `<id>.json`: snapshot del estado (plan, decisión HITL, renombres aplicados).
- `<id>.events.jsonl`: historial de eventos, para el replay del WebSocket.
- `<id>.jsonl`: log de auditoría.

Al arrancar, el servidor recarga lo persistido y:

- Revalida cada `root_dir` contra la denylist y `COWORK_ALLOWED_ROOTS` actuales.
  Una sesión cuyo directorio ya no existe, o que hoy estaría prohibido, se
  descarta en lugar de reabrirse.
- Marca como `expired` toda sesión que quedó en un estado con worker vivo
  (`scanning`, `awaiting_approval`, `executing`, …). No se reanuda sola:
  reintentar un `rename` a ciegas podría mover archivos ya movidos. Una sesión
  `expired` ya no se puede aprobar (`409`).
- Repuebla el historial de eventos, así que un cliente que se reconecta con
  `?since=<seq>` recupera el hilo donde lo dejó.

Las escrituras del snapshot son atómicas (`tmp` + `os.replace`), y los ficheros
corruptos o cortados se descartan reportándolo en el log sin impedir el arranque.
Un fallo de disco no aborta el agente: se registra como warning y la sesión
continúa.

## Frontend (React + Tauri)

`frontend/` es el cliente de escritorio. Consume la misma API por HTTP y
WebSocket: lista el plan, muestra los renombres propuestos y envía la decisión
HITL. No accede al filesystem ni ejecuta nada por su cuenta; toda la operación
sigue viviendo en el backend.

```bash
cd frontend
npm install
npm run dev        # http://localhost:1420
```

El dev server usa el puerto `1420` para coincidir con `COWORK_CORS_ORIGINS`; si
se cambia, hay que actualizar esa variable.

Para empaquetar la app de escritorio (requiere Rust y las dependencias nativas
de Tauri):

```bash
npm run tauri dev      # desarrollo dentro del webview
npm run tauri build    # instaladores (.deb, .rpm, .AppImage en Linux)
```

En Debian/Ubuntu las dependencias nativas son `libwebkit2gtk-4.1-dev`,
`libayatana-appindicator3-dev`, `librsvg2-dev` y `patchelf`. El juego de iconos
de `frontend/src-tauri/icons/` forma parte del build: si falta, la compilación
del shell falla, así que no debe borrarse ni añadirse a `.gitignore`.

El backend se ejecuta como proceso aparte; la ventana de Tauri solo carga el
webview. Esa separación es deliberada: el agente necesita permisos de
filesystem que no conviene conceder al webview. La CSP en
`frontend/src-tauri/tauri.conf.json` limita las conexiones a los orígenes
locales del backend.

El token se guarda solo en memoria (estado de React): nunca en `localStorage`
ni en cookies, para que no sobreviva al cierre de la app ni quede expuesto a
XSS.

## Tests

```bash
pytest --cov
```

Cubre la suite principal y `experimental/tests`. Umbral mínimo de cobertura
configurado en `pyproject.toml`.

Para el frontend:

```bash
cd frontend && npm run typecheck
```

## Calidad

```bash
ruff check .     # lint
ruff format .    # formato
mypy .           # tipos
bandit -r . -x ./tests,./experimental/tests
```

Todo esto corre en CI (`.github/workflows/ci.yml`) sobre Python 3.11 y 3.13,
más el job de frontend (tipos y build) sobre Node 22.

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
- `api/`: backend FastAPI (HTTP + WebSocket) que orquesta el mismo `CoworkAgent`.
- `frontend/`: cliente React; `frontend/src-tauri/` es el shell de escritorio.
- `experimental/`: módulos exploratorios fuera del flujo principal.

## Documentación

- [`ARCHITECTURE.md`](ARCHITECTURE.md): capas, invariantes de seguridad y camino a FastAPI + Tauri.
- [`SECURITY.md`](SECURITY.md): modelo de amenazas, controles y limitaciones conocidas.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): setup, reglas del proyecto y flujo de verificación.
- [`TODO.md`](TODO.md): estado de la Fase 1 y siguientes pasos.

## Próximas mejoras recomendadas

- Generar los tipos del frontend desde el OpenAPI del backend para eliminar la
  duplicación manual de `frontend/src/api/types.ts`.
- Endurecer la validación Pydantic del plan en el borde de la API.
- Empaquetar el backend junto a la app de Tauri (sidecar) para que el usuario no
  tenga que arrancar `uvicorn` a mano.
- Incorporar herramientas adicionales (browser, automatización) desde
  `experimental/` cuando el flujo base esté cerrado.
- Métricas y dashboard operativo (ver `experimental/`).
