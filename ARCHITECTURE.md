# Arquitectura

`cowork-clone` es un agente autónomo de gestión de archivos con aprobación humana.
Su diseño separa **decidir** de **ejecutar**: el LLM propone, el humano aprueba y
una capa con whitelist es la única que toca el filesystem.

## Ciclo de ejecución

```
scan ─▶ plan ──▶ HITL ──▶ execute ──▶ audit
  │        │        │         │           │
  │        │        │         │           └─ audit_logger.py  (JSONL)
  │        │        │         ─ os_commands.py   (whitelist + path traversal)
  │        │        └─ user_validation.py (timeout 60s, rechazo por defecto)
  │        └─ planner.py / ai_engine.py (LLM sugiere, nunca ejecuta)
  └─ file_manager.py (límites de escaneo y previews)
```

Cada etapa degrada de forma segura: si el LLM falla se usa un análisis neutro,
si la lectura de un archivo falla se omite ese archivo y si el humano no
responde se rechaza la operación.

## Capas

| Módulo | Responsabilidad | Confianza |
| --- | --- | --- |
| `main.py` | Orquesta el ciclo en `CoworkAgent` | — |
| `file_manager.py` | Escanea y lee previews (con límites) | entrada no confiable |
| `ai_engine.py` | Cliente LLM, parseo de JSON, fallbacks | **no confiable** |
| `planner.py` | Convierte análisis en `ExecutionPlan` | no confiable |
| `user_validation.py` | HITL: aprueba, rechaza, timeout | frontera de confianza |
| `os_commands.py` | Única capa que toca el filesystem | **confiable** |
| `transaction_manager.py` | Rollback ante fallo parcial | confiable |
| `audit_logger.py` | Traza append-only en JSONL | confiable |
| `storage_manager.py` | Persistencia SQLite (planes, acciones) | confiable |
| `models.py` | Schemas Pydantic compartidos | — |

La regla de diseño es que **ninguna decisión de filesystem depende de la salida
del LLM**. El modelo solo aporta una categoría y un nombre sugerido; ambos pasan
por `safe_filename` y por la validación de `os_commands`.

## Invariantes de seguridad

1. **Whitelist de operaciones.** Solo `mkdir` y `rename`. Cualquier otro tipo
   declarado en el plan aborta el plan completo antes de tocar un solo archivo
   (`SAFE_OPERATIONS` en `os_commands.py`).
2. **Confinamiento al root.** `_ensure_within_root` resuelve las rutas con
   `Path.resolve()` y compara por jerarquía de componentes, no por prefijo de
   string. Bloquea `..`, rutas absolutas externas, symlinks que escapan y
   prefijos engañosos como `/root_evil` frente a `/root`.
3. **Sin colisiones destructivas.** Si el destino existe, `_find_unique_path`
   genera `archivo_1.txt`, `archivo_2.txt`, ... en lugar de sobrescribir. Si se
   agota `MAX_UNIQUE_PATH_ATTEMPTS` se lanza `FileExistsError` y el plan falla.
4. **Rechazo por defecto en HITL.** Timeout, EOF o tres entradas inválidas
   seguidas producen rechazo explícito, nunca aprobación implícita.
5. **Saneamiento de nombres.** `safe_filename` elimina separadores, caracteres
   de control, nombres reservados de Windows y puntos iniciales, y acota la
   longitud preservando la extensión.

## Desacoplamiento de I/O

`CoworkAgent` recibe un objeto `AgentCallbacks` con `input_fn` y `output_fn`
inyectables. Esto permite reutilizar el orquestador sin terminal:

```python
agent = CoworkAgent(
    root_dir=root,
    callbacks=AgentCallbacks(
        input_fn=lambda prompt: cola_http.get(),  # llega de una petición
        output_fn=lambda texto: ws.send(texto),  # sale hacia el frontend
    ),
)
```

El mismo patrón aplica en `request_user_approval(..., approval_provider=...)`
para resolver la aprobación de forma totalmente asíncrona.

## Camino a la arquitectura End-to-End

El objetivo es un backend FastAPI y un frontend Tauri/React. Lo que ya está
preparado:

- [x] Orquestador encapsulado en una clase reutilizable.
- [x] I/O inyectable (callbacks en lugar de `print`/`input` fijos).
- [x] Endpoint y API key del LLM configurables por entorno.
- [x] Contrato de aprobación `ApprovalDecision` serializable.
- [x] Backend FastAPI con sesiones, HITL por HTTP y stream por WebSocket.
- [x] Cliente React sobre el contrato de eventos ya publicado.
- [x] Shell Tauri (`frontend/src-tauri/`) con CSP restringida a los orígenes locales del backend.
- [x] Empaquetado de Tauri verificado en CI (job `tauri`).
- [x] Persistencia de sesiones entre reinicios del backend.

## Backend FastAPI (`api/`)

La capa HTTP no reimplementa el agente: instancia el mismo `CoworkAgent` y
sustituye los callbacks de I/O. Ese es el punto central del diseño.

- `api/config.py`: `ApiConfig.from_env()` + `validate_root_dir()`. Postura
  cerrada por defecto: sin token no arranca, y las raíces del sistema
  (`/etc`, `/usr`, `/bin`, …) se rechazan resolviendo symlinks antes de comparar.
- `api/schemas.py`: contrato Pydantic que consume el frontend. Todo lo que sale
  por HTTP o WebSocket pasa por aquí.
- `api/sessions.py`: `SessionManager` + `Session`. Cada sesión corre el ciclo del
  agente en un hilo propio y se sincroniza con el HITL mediante un `Event`.
- `api/events.py`: `EventBroker`, puente entre el hilo del agente (síncrono) y
  las colas asyncio de los WebSockets. `seed()` repuebla el historial desde disco
  al arrancar.
- `api/persistence.py`: `SessionStore` + `SessionSnapshot`. Estado en disco con
  escritura atómica; el `session_id` se valida antes de construir rutas para que
  un fichero manipulado no derive en path traversal.
- `api/app.py`: `create_app(config)` — autenticación, endpoints REST y WebSocket.

### Invariantes del backend

- El HITL solo se resuelve si la sesión está en `awaiting_approval`; en cualquier
  otro estado responde `409`. Nunca se aprueba por defecto.
- Al expirar el timeout del HITL la sesión se rechaza, no se aprueba.
- La clasificación por defecto es offline (`OfflineFileAnalysisProvider`): una
  petición HTTP no puede quedar colgada esperando a Ollama. Para usar el LLM real
  se inyecta `OllamaProvider()` en `SessionManager`.
- Todo evento del agente se registra en un JSONL por sesión con `session_id` en
  cada línea, incluidos los que emite el núcleo (que no conoce el concepto de
  sesión).
- Persistir es best-effort: un fallo de disco degrada a warning y la sesión
  continúa. Perder histórico es menos grave que abortar un ciclo en curso.
- Al recargar nunca se reanuda la ejecución. Una sesión con worker vivo pasa a
  `expired`, porque reintentar un `rename` a ciegas podría mover archivos ya
  movidos; y su `root_dir` se revalida contra la configuración *actual*, de modo
  que endurecer `COWORK_ALLOWED_ROOTS` no reabra accesos antiguos.

### Defensa contra TOCTOU

Validar la ruta resuelta y después operar sobre el string deja una ventana: entre
la comprobación y el `rename` un atacante con escritura en el root puede
reemplazar un componente por un symlink y desviar la operación fuera del root.
Repetir la validación no lo arregla — siempre queda un hueco entre el último
chequeo y el uso.

`os_commands` lo cierra no volviendo a resolver rutas: abre el root una vez y
navega cada componente con `openat` (`dir_fd`) y `O_NOFOLLOW`, de modo que un
componente que sea symlink falla con `ELOOP` en el instante de abrirlo. La
operación se aplica relativa al descriptor del directorio ya abierto
(`os.rename(..., dst_dir_fd=fd)`), así que la resolución ocurre una sola vez.

Como el `rename` pisa el destino en silencio, la escritura de archivos usa
`os.link` + `os.unlink`: `link` es atómico sobre la existencia del destino
(`EEXIST` si está ocupado) y el origen se desenlaza solo tras enlazar, de modo que
un fallo nunca pierde datos. Los directorios, que no admiten enlace duro, se
renombran directamente.

`_ensure_within_root` se mantiene como validación temprana —barata y con buenos
mensajes— para abortar planes malformados antes de abrir ningún descriptor, pero
la garantía la da la navegación por descriptores. En Windows, sin `dir_fd`, se
degrada al modo por ruta y el proceso lo advierte por el log.

## Shell de escritorio (Tauri)

`frontend/src-tauri/` empaqueta el cliente React como aplicación nativa. El shell
Rust **no** reimplementa nada del agente: solo abre el webview. Todo el acceso al
filesystem vive en el backend FastAPI, que corre como proceso aparte. Ese reparto
es deliberado — el agente necesita permisos sobre el filesystem y no conviene
dárselos al webview.

Piezas del empaquetado:

- `tauri.conf.json`: ventana 1024x768, CSP que solo permite conectarse a los
  orígenes locales del backend, y `bundle.icon` con los formatos de cada
  plataforma. `category: Utility` clasifica la app en los menús del sistema.
- `capabilities/default.json`: permisos mínimos (`core:default`). El webview no
  puede tocar el filesystem ni el shell; si algún día necesita un permiso extra,
  debe declararse aquí explícitamente.
- `icons/`: juego completo generado con `npm run tauri icon <png>`. Es un
  requisito del build, no un adorno: `tauri::generate_context!()` falla al
  compilar si falta `icons/icon.png`, y el empaquetado de AppImage falla si no
  hay un icono cuadrado declarado en `bundle.icon`.
- `Cargo.lock`: versionado para que la resolución de dependencias sea
  reproducible en CI.

El job `tauri` de CI compila el shell, ejecuta `cargo fmt --check` y
`cargo clippy -D warnings`, y genera los instaladores `.deb` y `.AppImage` como
artefactos. Verificarlo solo con `tsc`/`vite` no bastaba: el shell podía estar
roto sin que ningún job lo notara.

## Módulos experimentales

`experimental/` contiene código **fuera del flujo principal**, conservado para
fases posteriores y movido ahí para no ensuciar la raíz del proyecto. Ver
`experimental/README.md`. `experimental/document_indexer.py` está cubierto por
tests porque será la base de la memoria a largo plazo.

## Calidad

- `ruff` para lint y formato, `mypy` en modo estricto sobre el código activo.
- `pytest` con umbral de cobertura mínima configurado en `pyproject.toml`.
- `bandit` en CI para análisis estático de seguridad.
- `experimental/` queda excluido de `mypy` pero incluido en lint y tests.