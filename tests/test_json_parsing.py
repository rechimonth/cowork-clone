"""Tests del parseo robusto de JSON devuelto por el LLM (Fase 2.3).

El LLM no es un contrato confiable: puede envolver el objeto en prosa, usar
fences de markdown, comillas simples o literales de Python. Estos tests fijan
ese comportamiento porque de el depende que un fallo de parseo no se convierta
en un renombre erroneo.
"""

from __future__ import annotations

import pytest

from ai_engine import (
    _iter_json_objects,
    _normalize_json_like,
    _parse_json_object,
    parse_file_analysis_json,
)

VALID = '{"category": "invoice", "suggested_name": "factura", "reason": "ok"}'


def test_parses_plain_json():
    assert _parse_json_object(VALID)["category"] == "invoice"


def test_parses_json_with_text_before_and_after():
    raw = f"Claro, aqui esta el analisis:\n{VALID}\nEspero que sirva."

    parsed = _parse_json_object(raw)

    assert parsed["suggested_name"] == "factura"


def test_parses_json_inside_markdown_fence():
    raw = f"```json\n{VALID}\n```"

    assert _parse_json_object(raw)["category"] == "invoice"


def test_parses_json_inside_unclosed_fence():
    raw = f"```json\n{VALID}"

    assert _parse_json_object(raw)["category"] == "invoice"


def test_parses_json_with_single_quotes():
    raw = "{'category': 'invoice', 'suggested_name': 'factura', 'reason': 'ok'}"

    parsed = _parse_json_object(raw)

    assert parsed["category"] == "invoice"


def test_parses_json_with_python_literals():
    raw = "{'category': 'invoice', 'suggested_name': 'f', 'reason': None, 'ok': True}"

    parsed = _parse_json_object(raw)

    assert parsed["reason"] is None
    assert parsed["ok"] is True


def test_unwraps_ollama_response_wrapper():
    raw = '{"response": "{\\"category\\": \\"invoice\\", \\"suggested_name\\": \\"f\\"}"}'

    assert _parse_json_object(raw)["category"] == "invoice"


def test_raises_on_empty_response():
    with pytest.raises(ValueError):
        _parse_json_object("")


def test_raises_when_no_json_present():
    with pytest.raises(ValueError):
        _parse_json_object("Lo siento, no puedo procesar ese archivo.")


def test_ignores_nested_braces_outside_strings():
    raw = '{"category": "a", "suggested_name": "b", "extra": {"n": 1}}'

    parsed = _parse_json_object(raw)

    assert parsed["extra"] == {"n": 1}


def test_brace_inside_string_does_not_break_scanning():
    raw = '{"category": "a", "suggested_name": "b", "reason": "usa {llaves} aqui"}'

    parsed = _parse_json_object(raw)

    assert parsed["reason"] == "usa {llaves} aqui"


def test_iter_json_objects_skips_invalid_and_finds_valid():
    raw = '{no soy json} luego {"category": "a", "suggested_name": "b"}'

    objects = list(_iter_json_objects(raw))

    assert any(o.get("category") == "a" for o in objects)


def test_normalize_json_like_converts_literals():
    normalized = _normalize_json_like("{'a': None, 'b': True, 'c': False}")

    assert normalized == '{"a": null, "b": true, "c": false}'


def test_parse_file_analysis_returns_model_on_valid_response():
    analysis = parse_file_analysis_json(VALID, fallback_to="original.pdf")

    assert analysis.category == "invoice"
    assert analysis.suggested_name == "factura"


def test_parse_file_analysis_falls_back_on_garbage():
    analysis = parse_file_analysis_json("no hay json", fallback_to="original.pdf")

    # Fallback seguro: conserva el nombre original en lugar de inventar uno.
    assert analysis.category == "unknown"
    assert analysis.suggested_name == "original.pdf"


def test_parse_file_analysis_falls_back_on_schema_violation():
    # Falta "suggested_name": el schema de pydantic debe rechazarlo.
    raw = '{"category": "invoice"}'

    analysis = parse_file_analysis_json(raw, fallback_to="original.pdf")

    assert analysis.suggested_name == "original.pdf"


def test_parse_file_analysis_sanitizes_malicious_suggested_name():
    raw = '{"category": "a", "suggested_name": "../../etc/passwd"}'

    analysis = parse_file_analysis_json(raw, fallback_to="original.pdf")

    assert "/" not in analysis.suggested_name
    assert ".." not in analysis.suggested_name


def test_parse_file_analysis_tries_next_candidate_after_bad_schema():
    raw = (
        '{"category": "solo_esto"} '
        '{"category": "invoice", "suggested_name": "buena", "reason": "ok"}'
    )

    analysis = parse_file_analysis_json(raw, fallback_to="original.pdf")

    assert analysis.suggested_name == "buena"
