"""Tests del paralelismo de clasificacion y de los proveedores de analisis.

Verifican que ``_classify_pdfs`` paraleliza las llamadas I/O bound, preserva el
orden de entrada (para que el plan sea determinista) y que el proveedor offline
funciona sin red.
"""

from __future__ import annotations

import pathlib
import threading
import time

from ai_engine import (
    DEFAULT_LLM_MAX_WORKERS,
    FileAnalysis,
    OfflineFileAnalysisProvider,
    _classify_pdfs,
)
from models import FileItem


class FakeProvider:
    """Proveedor instrumentado que registra concurrencia y respeta el contrato."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.active = 0
        self.peak_concurrency = 0
        self.seen: list[str] = []
        self._lock = threading.Lock()

    def classify_document(
        self,
        original_filename: str,
        preview_text: str | None = None,
        ext: str | None = None,
    ) -> FileAnalysis:
        with self._lock:
            self.active += 1
            self.peak_concurrency = max(self.peak_concurrency, self.active)
            self.seen.append(original_filename)
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        return FileAnalysis(
            category="document",
            suggested_name=f"renombrado_{original_filename}",
            reason="fake",
        )


def _items(names: list[str]) -> list[FileItem]:
    return [FileItem(path=f"/root/{n}", filename=n, ext=pathlib.Path(n).suffix) for n in names]


def test_classify_pdfs_sequential_when_max_workers_is_one():
    provider = FakeProvider()
    pdfs = _items([f"doc{i}.pdf" for i in range(5)])

    results = _classify_pdfs(provider, pdfs, max_workers=1)

    assert len(results) == 5
    assert provider.peak_concurrency == 1
    assert provider.seen == [f"doc{i}.pdf" for i in range(5)]


def test_classify_pdfs_uses_threads_for_multiple_files():
    provider = FakeProvider(delay=0.05)
    pdfs = _items([f"doc{i}.pdf" for i in range(8)])

    started = time.perf_counter()
    results = _classify_pdfs(provider, pdfs, max_workers=4)
    elapsed = time.perf_counter() - started

    assert len(results) == 8
    # Con 4 workers y 8 tareas de 50ms el paralelismo debe evidenciarse.
    assert provider.peak_concurrency > 1
    assert elapsed < 0.05 * 8


def test_classify_pdfs_preserves_input_order():
    provider = FakeProvider(delay=0.01)
    names = ["b.pdf", "a.pdf", "c.pdf", "z.pdf", "m.pdf"]
    pdfs = _items(names)

    results = _classify_pdfs(provider, pdfs, max_workers=4)

    # El orden de salida debe mapear 1:1 con el de entrada, no con el de llegada.
    assert [r.suggested_name for r in results] == [f"renombrado_{n}" for n in names]


def test_classify_pdfs_single_file_skips_pool():
    provider = FakeProvider()
    results = _classify_pdfs(provider, _items(["solo.pdf"]), max_workers=8)

    assert len(results) == 1
    assert provider.peak_concurrency == 1


def test_classify_pdfs_empty_input():
    provider = FakeProvider()
    assert _classify_pdfs(provider, [], max_workers=4) == []
    assert provider.seen == []


def test_default_max_workers_is_modest():
    # No saturar al proveedor con un pool desproporcionado.
    assert 1 < DEFAULT_LLM_MAX_WORKERS <= 16


def test_offline_provider_classifies_by_extension():
    provider = OfflineFileAnalysisProvider()

    assert provider.classify_document("nota.txt").category == "text"
    assert provider.classify_document("doc.pdf").category == "document"
    assert provider.classify_document("datos.csv").category == "spreadsheet"
    assert provider.classify_document("run.log").category == "log"
    assert provider.classify_document("raro.xyz").category == "unknown"


def test_offline_provider_sanitizes_filename():
    provider = OfflineFileAnalysisProvider()

    result = provider.classify_document("../../etc/passwd.txt")

    assert "/" not in result.suggested_name
    assert ".." not in result.suggested_name


def test_offline_provider_uses_explicit_ext_over_filename():
    provider = OfflineFileAnalysisProvider()

    result = provider.classify_document("sin_extension", ext=".pdf")

    assert result.category == "document"
