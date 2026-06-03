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
- [ ] Conectar un LLM real (Ollama/llama.cpp) + parseo robusto del JSON con Pydantic.
- [ ] Añadir validación adicional: evitar colisiones de nombres y garantizar unicidad del destino.


