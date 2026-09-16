from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any


class AuditLogger:
    """Registro append-only de eventos en JSON Lines.

    ``session_id`` es opcional: cuando está presente se agrega a *todos* los
    registros. Eso permite correlacionar en el log los eventos del núcleo del
    agente (que no conoce el concepto de sesión HTTP) con la sesión de la API
    que los originó, sin tener que enhebrar el id por cada llamada.
    """

    def __init__(self, log_path: str, session_id: str | None = None):
        self.log_path = log_path
        self.session_id = session_id
        log_dir = os.path.dirname(os.path.abspath(log_path))
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    def log_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "event_type": event_type,
            "payload": payload or {},
        }
        if self.session_id is not None:
            record["session_id"] = self.session_id
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_llm_interaction(
        self,
        *,
        prompt: str,
        response: Any,
        duration: float,
        retry: int,
        error: str | None,
        fallback: bool,
        model: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        self.log_event(
            "LLM_INTERACTION",
            {
                "prompt": prompt,
                "response": response,
                "duration": duration,
                "retry": retry,
                "error": error,
                "fallback": fallback,
                "model": model,
                "endpoint": endpoint,
            },
        )
