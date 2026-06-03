from ai_engine import FileAnalysis, fallback_file_analysis


def test_fallback_structure():
    out = fallback_file_analysis(original_filename="mi:archivo?.pdf")

    assert isinstance(out, FileAnalysis)
    assert out.category == "unknown"
    assert out.reason == "fallback"
    assert out.suggested_name == "mi:archivo?.pdf"
