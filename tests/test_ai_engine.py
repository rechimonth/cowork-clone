from typing import Any

from ai_engine import FileAnalysis, OllamaProvider, parse_file_analysis_json, safe_filename


def test_safe_filename_windows_compatible():
    assert safe_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"
    assert safe_filename("  nombre   con   espacios...") == "nombre con espacios"
    assert len(safe_filename("x" * 200)) == 120


def test_parser_json_with_extra_text():
    analysis = parse_file_analysis_json(
        'respuesta: {"category": "invoice", "suggested_name": "2026_Invoice_Amazon", '
        '"reason": "Factura emitida por Amazon"}'
    )

    assert isinstance(analysis, FileAnalysis)
    assert analysis.category == "invoice"
    assert analysis.suggested_name == "2026_Invoice_Amazon"


def test_parser_json_from_ollama_wrapper():
    analysis = parse_file_analysis_json(
        '{"response": "{\\"category\\": \\"invoice\\", '
        '\\"suggested_name\\": \\"2026_Invoice_Amazon\\", '
        '\\"reason\\": \\"Factura emitida por Amazon\\"}"}'
    )

    assert analysis.category == "invoice"
    assert analysis.reason == "Factura emitida por Amazon"


def test_ollama_provider_validates_response(monkeypatch):
    class FakeResp:
        status_code = 200
        text = (
            '{"response": "{\\"category\\": \\"invoice\\", '
            '\\"suggested_name\\": \\"2026_Invoice_Amazon\\", '
            '\\"reason\\": \\"Factura emitida por Amazon\\"}"}'
        )

    calls: list[dict[str, Any]] = []

    def fake_post(*args: Any, **kwargs: Any):
        calls.append({"args": args, "kwargs": kwargs})
        return FakeResp()

    provider = OllamaProvider(request_timeout_s=1, retries=1)
    monkeypatch.setattr("ai_engine.requests.post", fake_post)

    out = provider.classify_document(
        original_filename="amazon.pdf",
        preview_text="Factura emitida por Amazon",
        ext=".pdf",
    )

    assert out.category == "invoice"
    assert out.suggested_name == "2026_Invoice_Amazon"
    assert calls[0]["kwargs"]["timeout"] == 1
    assert calls[0]["kwargs"]["json"]["format"] == "json"


def test_ollama_provider_fallback_on_bad_json(monkeypatch):
    class FakeResp:
        status_code = 200
        text = "{not json}"

    def fake_post(*args: Any, **kwargs: Any):
        return FakeResp()

    provider = OllamaProvider(request_timeout_s=1, retries=1)
    monkeypatch.setattr("ai_engine.requests.post", fake_post)

    out = provider.classify_document(
        original_filename="file.pdf",
        preview_text="x",
        ext=".pdf",
    )

    assert out.category == "unknown"
    assert out.suggested_name == "file.pdf"
    assert out.reason == "fallback"
