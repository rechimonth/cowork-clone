# Seguridad

`cowork-clone` mueve archivos reales del usuario, así que asume que **todo lo que
viene de fuera es hostil**: nombres propuestos por el LLM, rutas dentro de un
plan, y cualquier valor que llegue por entorno o por una petición.

## Modelo de amenazas

| Amenaza | Vector | Defensa |
| --- | --- | --- |
| Borrado destructivo | Plan con operaciones `delete`/`rmdir` | Whitelist `SAFE_OPERATIONS` aborta el plan antes de ejecutar |
| Path traversal | `dst` con `..`, ruta absoluta o symlink | `_ensure_within_root` resuelve y compara por componentes |
| Sobrescritura de datos | `dst` ya existente | `_find_unique_path` desambigua en lugar de pisar |
| Nombre malicioso del LLM | `../../etc/passwd` como nombre sugerido | `safe_filename` sanea y recorta puntos iniciales |
| Aprobación implícita | Usuario ausente o indeciso | Timeout 60 s y 3 entradas inválidas ⇒ rechazo explícito |
| Exfiltración por red | Endpoint HTTP remoto sin cifrado | Aviso `INSECURE_ENDPOINT` en log y auditoría |
| Inyección SQL | Nombre de tabla interpolado | Whitelist `ALLOWED_TABLES` en el dashboard |
| Fallo silencioso | Excepción atrapada y descartada | Excepciones específicas + log con tipo y ruta |

## Controles implementados

### Whitelist de operaciones

Único conjunto permitido: `mkdir`, `rename`. Se valida sobre los tipos
*declarados* en el plan antes de tocar el filesystem, de modo que un plan mixto
no deja efectos parciales.

### Confinamiento al directorio raíz

`_ensure_within_root(root, target)`:

1. Resuelve ambas rutas con `Path.resolve()` — normaliza `..`/`.` y sigue symlinks.
2. Comprueba pertenencia por jerarquía de `pathlib`, no por prefijo de string.
3. Lanza `ValueError` con la ruta ofensiva si el destino escapa.

La comparación por prefijo de string sería un fallo real: `/tmp/root_evil/x`
empieza con `/tmp/root` pero no pertenece a ese root.

### Resolución de colisiones

`_find_unique_path` agrega `_1`, `_2`, ... preservando la extensión. Tras
`MAX_UNIQUE_PATH_ATTEMPTS` falla con `FileExistsError` en lugar de arriesgar una
sobrescritura.

### Human-in-the-Loop

`request_user_approval` espera `[A] Aprobar`, `[R] Rechazar` o `[V] Ver detalle`:

- Timeout de 60 s (implementado con `select`, con degradación si no está
  disponible) ⇒ `decision="timeout"`.
- EOF ⇒ `decision="eof"`.
- Tres entradas inválidas consecutivas ⇒ `decision="invalid_input_limit"`.

Los tres casos resultan en **rechazo**. La decisión queda registrada con su
motivo, y `ApprovalDecision` es *falsy* cuando no está aprobado.

### Proveedor LLM

`OLLAMA_ENDPOINT` y `OLLAMA_API_KEY` se leen del entorno. Si el endpoint usa
HTTP sin cifrado y no apunta a localhost, se emite un warning y se registra
`INSECURE_ENDPOINT`: el prompt puede contener previews de documentos y la API
key viajaría en texto plano.

### Manejo de errores

Las excepciones se capturan por tipo explícito (`OSError`, `ValueError`,
`UnicodeDecodeError`, `PdfReadError`, `requests.RequestException`, ...) y los
fallos de lectura se registran con `event_type`, ruta y tipo de excepción — nunca
con el contenido leído.

## Despliegue

- La API key **nunca** debe quedar en el repositorio; se inyecta por entorno.
- Usar HTTPS para cualquier Ollama que no esté en la misma máquina.
- Revisar `audit.jsonl` ante comportamiento inesperado: registra planes,
  decisiones HITL, ejecuciones y errores.
- El endpoint HTTP del agente todavía no existe; cuando se añada FastAPI deberá
  autenticar y autorizar cada petición antes de invocar `CoworkAgent`.

## Limitaciones conocidas

- **TOCTOU**: la validación de rutas ocurre antes de la llamada al filesystem.
  Un symlink creado en esa ventana podría redirigir la operación. Mitigado en la
  práctica por el HITL, que introduce revisión humana entre plan y ejecución.
- El HITL por terminal es de un solo usuario; el control de acceso multi-usuario
  llega con la API.
- Los previews de documentos se envían al LLM. Con un proveedor remoto esto
  implica transferir contenido del usuario fuera de la máquina: usar un modelo
  local o un endpoint propio si eso no es aceptable.

## Reportar una vulnerabilidad

Abrir un issue privado en <https://github.com/rechimonth/cowork-clone> describiendo
el impacto y, si es posible, un caso mínimo de reproducción.