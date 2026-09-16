"""Backend FastAPI del agente cowork-clone.

Expone el ciclo scan -> plan -> HITL -> execute por HTTP y WebSocket, para que
un frontend (React dentro de Tauri) pueda dirigir el agente sin bloquear una
terminal. El núcleo del agente no cambia: esta capa solo lo orquesta mediante
`CoworkAgent` y sus callbacks inyectables.
"""

from __future__ import annotations
