# experimental/

Código **fuera del flujo principal** del agente. Se conserva porque apunta a
fases posteriores del roadmap, pero no debe importarse desde `main.py` ni desde
la futura API de producción. Las dependencias externas que necesita este código
no están en `requirements.txt`.

| Módulo | Qué hace | Estado |
| --- | --- | --- |
| `supervisor_agent.py` | Consultas de solo lectura: planifica archivos e inspecciona URLs sin ejecutar | probado |
| `document_indexer.py` | Índice documental en SQLite; base de la memoria a largo plazo | probado |
| `memory_manager.py` | Memoria a corto plazo (LRU) y largo plazo (SQLite) | probado |
| `metrics.py` | Registro de métricas de ejecución, fallbacks y errores | probado |
| `dashboard.py` | Dashboard operativo de SQLite (consola, opcionalmente con `rich`) | parcial |
| `sandbox_builder.py` | Genera un árbol de archivos de prueba reproducible (incluye un PDF válido) | probado |
| `browser_agent.py` | Inspección de URLs en modo lectura con política de bloqueo | solo la validación de URLs |

## Por qué está separado

Mover estos módulos fuera de la raíz elimina ~2.000 líneas de código no usado por
el ciclo `scan -> plan -> HITL -> execute` sin perderlo. Los que reaparecerán más
adelante (índice documental, memoria, métricas) ya tienen tests, de modo que su
reactivación no parte de cero.

## Riesgos a resolver antes de promoverlos

- `browser_agent.py` depende de `playwright`, que no es dependencia del proyecto.
- `dashboard.py` usa `rich` de forma opcional y hoy no tiene cobertura del camino
  con `rich` instalado.
- Estos módulos no están cubiertos por `mypy` (ver `exclude` en `pyproject.toml`).

## Tests

```bash
python -m pytest experimental/tests -q
```

Se ejecutan junto con la suite principal (`pytest`), no por separado.