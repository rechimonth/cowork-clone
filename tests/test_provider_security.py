"""Tests de configuracion por entorno, aviso HTTP inseguro y audit log.

Fase 3 (seguridad del proveedor LLM): el endpoint y la API key deben poder
configurarse sin tocar codigo, y un endpoint HTTP remoto debe producir un aviso
explicito porque el prompt viajaria en texto plano.
"""

from __future__ import annotations

import json
import logging

import pytest

from ai_engine import (
    OLLAMA_ENDPOINT,
    OllamaProvider,
    _unsafe_http_warning,
)
from audit_logger import AuditLogger

# --------------------------- endpoint inseguro --------------------------------


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://ollama.remoto.cl:11434/api/generate",
        "http://10.0.0.5:11434/api/generate",
        "http://mi-servidor-interno/api/generate",
    ],
)
def test_http_remote_endpoint_is_flagged(endpoint):
    warning = _unsafe_http_warning(endpoint)

    assert warning is not None
    assert "no es apto para producción" in warning


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434/api/generate",
        "http://127.0.0.1:11434/api/generate",
        "http://127.0.0.53:11434/api/generate",
        "http://[::1]:11434/api/generate",
    ],
)
def test_http_localhost_is_allowed(endpoint):
    assert _unsafe_http_warning(endpoint) is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://ollama.empresa.com/api/generate",
        "https://10.0.0.5:11434/api/generate",
    ],
)
def test_https_is_never_flagged(endpoint):
    assert _unsafe_http_warning(endpoint) is None


def test_provider_warns_on_insecure_remote_http(caplog):
    with caplog.at_level(logging.WARNING, logger="ai_engine"):
        OllamaProvider(endpoint="http://ollama.remoto.cl:11434/api/generate")

    assert any("no es apto para producción" in r.message for r in caplog.records)


def test_provider_does_not_warn_for_local_http(caplog):
    with caplog.at_level(logging.WARNING, logger="ai_engine"):
        OllamaProvider(endpoint="http://localhost:11434/api/generate")

    assert not any("no es apto para producción" in r.message for r in caplog.records)


def test_provider_logs_insecure_endpoint_to_audit(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))

    OllamaProvider(endpoint="http://ollama.remoto.cl:11434/api/generate", audit_logger=logger)

    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert events[0]["event_type"] == "INSECURE_ENDPOINT"
    assert events[0]["payload"]["endpoint"].startswith("http://ollama.remoto.cl")


# --------------------------- configuracion por entorno ------------------------


def test_endpoint_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_ENDPOINT", "https://ollama.miempresa.com/api/generate")

    provider = OllamaProvider()

    assert provider.endpoint == "https://ollama.miempresa.com/api/generate"


def test_explicit_endpoint_overrides_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_ENDPOINT", "https://desde.env/api/generate")

    provider = OllamaProvider(endpoint="https://explicito/api/generate")

    assert provider.endpoint == "https://explicito/api/generate"


def test_endpoint_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("OLLAMA_ENDPOINT", raising=False)

    provider = OllamaProvider()

    assert provider.endpoint == OLLAMA_ENDPOINT


def test_api_key_from_env_is_used_as_bearer(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "secreto-de-prueba")

    provider = OllamaProvider()

    assert provider._request_headers() == {"Authorization": "Bearer secreto-de-prueba"}


def test_no_api_key_means_no_auth_header(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    provider = OllamaProvider()

    assert provider._request_headers() == {}


def test_explicit_api_key_overrides_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "desde-env")

    provider = OllamaProvider(api_key="explicita")

    assert provider._request_headers()["Authorization"] == "Bearer explicita"


# ------------------------------- audit logger ---------------------------------


def test_audit_logger_creates_missing_directories(tmp_path):
    log_path = tmp_path / "anidado" / "logs" / "audit.jsonl"

    logger = AuditLogger(str(log_path))
    logger.log_event("TEST_EVENT", {"clave": "valor"})

    assert log_path.exists()
    record = json.loads(log_path.read_text().splitlines()[0])
    assert record["event_type"] == "TEST_EVENT"
    assert record["payload"] == {"clave": "valor"}
    assert record["ts"].endswith("Z")


def test_audit_logger_appends_events(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))

    logger.log_event("UNO")
    logger.log_event("DOS", {"n": 2})

    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["payload"] == {"n": 2}


def test_audit_logger_serializes_non_ascii(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    AuditLogger(str(log_path)).log_event("ACENTOS", {"texto": "año, ñandú"})

    raw = log_path.read_text(encoding="utf-8")
    assert "ñandú" in raw  # ensure_ascii=False preserva UTF-8 legible


def test_log_llm_interaction_records_fields(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))

    logger.log_llm_interaction(
        prompt="p",
        response="r",
        duration=0.5,
        retry=1,
        error=None,
        fallback=False,
        model="qwen",
        endpoint="http://localhost:11434/api/generate",
    )

    payload = json.loads(log_path.read_text().splitlines()[0])["payload"]
    assert payload["model"] == "qwen"
    assert payload["fallback"] is False
    assert payload["duration"] == 0.5
