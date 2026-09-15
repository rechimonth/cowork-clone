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
- [ ] Endpoint `POST /scan` y `POST /approve` en FastAPI.
- [ ] Canal WebSocket para emitir el plan y recibir la decisión.
- [ ] Ejecución en worker de fondo con estado consultable.

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