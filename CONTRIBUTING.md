# Contribuir

## Setup

Requiere Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Verificación antes de cada commit

```bash
ruff check .          # lint
ruff format .         # formato
mypy .                # tipos (solo código activo)
pytest --cov          # tests + cobertura
```

El mismo conjunto corre en CI (`.github/workflows/ci.yml`) sobre Python 3.11 y
3.13, más un escaneo de `bandit`.

## Reglas del proyecto

1. **Respetar la estructura existente.** Modificar los archivos actuales en vez
   de crear variantes paralelas. `experimental/` es el único lugar para código
   exploratorio.
2. **Una única capa toca el filesystem.** Toda operación de archivos pasa por
   `os_commands.py`. Nunca llamar a `os.rename`, `shutil.move` o `Path.rename`
   desde otro módulo de producción.
3. **Nunca ampliar la whitelist sin discusión explícita.** Agregar una operación
   destructiva requiere revisión de `SECURITY.md`.
4. **La salida del LLM no es confiable.** Validar con Pydantic, sanear nombres
   con `safe_filename` y mantener un fallback seguro para cada camino.
5. **Capturar excepciones específicas.** `except Exception` solo se justifica si
   va acompañado de un log con el tipo de excepción y de un fallback explícito.
6. **Sin I/O directo en la lógica del agente.** Usar `AgentCallbacks` en lugar de
   `print`/`input` para que el orquestador sea reutilizable desde FastAPI.

## Añadir una operación nueva

1. Agregar el tipo a `SAFE_OPERATIONS` en `os_commands.py` y a `ALLOWED_OPERATIONS`
   en `models.py`.
2. Validar rutas con `_ensure_within_root` antes de ejecutar.
3. Resolver colisiones con `_find_unique_path` si el destino puede existir.
4. Registrar la acción en `TransactionManager` para permitir rollback.
5. Añadir tests de path traversal y de colisión antes de abrir el PR.

## Estilo

- Type hints en todas las firmas; `mypy` es obligatorio.
- Docstrings en español, en el idioma del resto del proyecto.
- Errores esperados como excepciones propias (`OllamaProviderError`,
  `BrowserSafetyError`) en lugar de códigos de retorno.
- Los comentarios explican *por qué*, no *qué*; no narrar el diff.

## Commits

Mensajes en imperativo y con alcance claro, por ejemplo
`fix(os_commands): desambiguar destino existente en rename`. Un commit por
cambio lógico; separar refactor de cambio de comportamiento.