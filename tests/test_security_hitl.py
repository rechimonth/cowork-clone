import pytest

from ai_engine import OllamaProvider, _unsafe_http_warning
from models import ExecutionPlan
from user_validation import ApprovalDecision, request_user_approval

# --- Fase 3: configuración por entorno del proveedor LLM ---


def test_provider_reads_endpoint_and_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_ENDPOINT", "https://ollama.example.com/api/generate")
    monkeypatch.setenv("OLLAMA_API_KEY", "secreto")
    provider = OllamaProvider()
    assert provider.endpoint == "https://ollama.example.com/api/generate"
    assert provider._request_headers() == {"Authorization": "Bearer secreto"}


def test_provider_defaults_without_env(monkeypatch):
    monkeypatch.delenv("OLLAMA_ENDPOINT", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    provider = OllamaProvider()
    assert provider.endpoint == "http://localhost:11434/api/generate"
    assert provider._request_headers() == {}


def test_provider_explicit_endpoint_beats_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_ENDPOINT", "https://ollama.example.com/api/generate")
    provider = OllamaProvider(endpoint="https://otro.example.com/api/generate")
    assert provider.endpoint == "https://otro.example.com/api/generate"


def test_unsafe_http_warning_flags_remote_http():
    assert _unsafe_http_warning("http://ollama.example.com/api/generate") is not None


def test_unsafe_http_warning_allows_localhost_and_https():
    assert _unsafe_http_warning("http://localhost:11434/api/generate") is None
    assert _unsafe_http_warning("http://127.0.0.1:11434/api/generate") is None
    assert _unsafe_http_warning("https://ollama.example.com/api/generate") is None


def test_provider_logs_warning_for_insecure_remote_endpoint(tmp_path, caplog):
    log_path = tmp_path / "audit.jsonl"
    from audit_logger import AuditLogger

    with caplog.at_level("WARNING"):
        OllamaProvider(
            endpoint="http://remoto.example.com/api/generate",
            audit_logger=AuditLogger(str(log_path)),
        )

    assert any("no es apto para producción" in r.message for r in caplog.records)
    assert "INSECURE_ENDPOINT" in log_path.read_text()


# --- Fase 3: HITL con timeout y límite de entradas inválidas ---

PLAN = ExecutionPlan(summary="x")


def _outputs():
    lines: list[str] = []
    return lines, lines.append


def test_approval_accepts_valid_answers():
    _, out = _outputs()
    decision = request_user_approval(PLAN, input_fn=lambda _: "a", output_fn=out)
    assert decision.approved is True
    assert decision.decision == "approved"
    assert bool(decision) is True


def test_approval_rejects_on_r():
    _, out = _outputs()
    decision = request_user_approval(PLAN, input_fn=lambda _: "r", output_fn=out)
    assert decision.approved is False
    assert decision.decision == "rejected"


def test_approval_aborts_after_three_invalid_inputs():
    lines, out = _outputs()
    decision = request_user_approval(PLAN, input_fn=lambda _: "zzz", output_fn=out)
    assert decision.approved is False
    assert decision.decision == "invalid_input_limit"
    assert any("entradas inválidas" in line for line in lines)


def test_approval_invalid_counter_resets_after_details():
    seq = iter(["zzz", "zzz", "v", "a"])
    _, out = _outputs()
    decision = request_user_approval(PLAN, input_fn=lambda _: next(seq), output_fn=out)
    assert decision.approved is True


def test_approval_rejects_on_eof():
    def raise_eof(_):
        raise EOFError

    _, out = _outputs()
    decision = request_user_approval(PLAN, input_fn=raise_eof, output_fn=out)
    assert decision.approved is False
    assert decision.decision == "eof"


def test_approval_provider_bypasses_terminal(monkeypatch):
    monkeypatch.setattr(
        "user_validation._read_answer",
        lambda *a, **k: pytest.fail("no debe leer de la terminal"),
    )
    decision = request_user_approval(
        PLAN,
        approval_provider=lambda p: ApprovalDecision(approved=True, decision="http_api"),
    )
    assert decision.decision == "http_api"


def test_approval_timeout_aborts_by_default(monkeypatch):
    # Simula select sin datos listos => timeout.
    monkeypatch.setattr("select.select", lambda *a, **k: ([], [], []))
    _, out = _outputs()

    class FakeStdin:
        def fileno(self):
            return 0

        def readline(self):
            return "a\n"

    monkeypatch.setattr("sys.stdin", FakeStdin())
    decision = request_user_approval(PLAN, output_fn=out, timeout_s=1)
    assert decision.approved is False
    assert decision.decision == "timeout"
