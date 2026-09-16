"""Tests de los modulos experimentales.

`experimental/` esta fuera del flujo principal del agente, pero no puede quedar
sin ninguna verificacion: son modulos que reapareceran en fases posteriores
(memoria a largo plazo, metricas, indexacion documental). Estos tests cubren su
comportamiento basico y, sobre todo, que degradan sin romper el proceso.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from experimental import document_indexer, metrics, supervisor_agent
from experimental.memory_manager import (
    LongTermMemoryAdapter,
    ShortTermMemory,
)

# --------------------------- memoria a corto plazo ---------------------------


def test_short_term_memory_evicts_oldest_when_full():
    memory = ShortTermMemory(max_items=2)
    memory.remember("a", "uno")
    memory.remember("b", "dos")
    memory.remember("c", "tres")

    assert memory.recall("a") is None  # el mas viejo fue desalojado
    assert memory.recall("b") is not None
    assert memory.recall("c") is not None


def test_short_term_memory_search_and_forget():
    memory = ShortTermMemory()
    memory.remember("factura_amazon", "Factura de Amazon de enero")

    results = memory.search("amazon")
    assert len(results) == 1
    assert memory.forget("factura_amazon") is True
    assert memory.forget("factura_amazon") is False


def test_short_term_memory_clear():
    memory = ShortTermMemory()
    memory.remember("k", "v")
    memory.clear()

    assert memory.recall("k") is None


# --------------------------- memoria a largo plazo ---------------------------


def test_long_term_memory_roundtrip(tmp_path):
    db = tmp_path / "memory.db"
    memory = LongTermMemoryAdapter(str(db))
    memory.remember("clave", "valor", tags={"origen": "test"})

    item = memory.recall("clave")
    assert item is not None
    assert item.value == "valor"

    # Persiste entre instancias: es el punto de este adaptador.
    reopened = LongTermMemoryAdapter(str(db))
    assert reopened.recall("clave") is not None


def test_long_term_memory_forget_and_clear(tmp_path):
    memory = LongTermMemoryAdapter(str(tmp_path / "memory.db"))
    memory.remember("k", "v")

    assert memory.forget("k") is True
    assert memory.forget("k") is False

    memory.remember("k2", "v2")
    memory.clear()
    assert memory.recall("k2") is None


# ------------------------------- metricas ------------------------------------


def test_metrics_summary_counts_recorded_events(tmp_path):
    db = tmp_path / "metrics.db"
    metrics.init_metrics_db(str(db))
    metrics.record_execution_time(str(db), "exec_1", 1.5)
    metrics.record_error(str(db), "executor", "boom")

    summary = metrics.get_metrics_summary(str(db))

    assert summary["total"] >= 2


# ----------------------------- indexador -------------------------------------


def test_offline_indexer_records_documents(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "nota.txt").write_text("contenido de la nota")
    (root / "doc.pdf").write_bytes(b"%PDF-1.4 fake")

    indexer = document_indexer.DocumentIndexer(
        str(tmp_path / "index.db"),
        provider=document_indexer.OfflineFileAnalysisProvider(),
    )
    records = indexer.index_directory(str(root), recursive=False)

    assert len(records) == 2
    assert {r.path.rsplit("/", 1)[-1] for r in records} == {"nota.txt", "doc.pdf"}


def test_indexer_degrades_when_provider_raises(tmp_path):
    class BrokenProvider:
        def classify_document(self, *args, **kwargs):
            raise ValueError("proveedor roto")

    root = tmp_path / "docs"
    root.mkdir()
    (root / "nota.txt").write_text("x")

    indexer = document_indexer.DocumentIndexer(
        str(tmp_path / "index.db"), provider=BrokenProvider()
    )
    records = indexer.index_directory(str(root), recursive=False)

    # Degrada al proveedor offline en lugar de propagar la excepcion.
    assert len(records) == 1


# ----------------------------- supervisor ------------------------------------


def test_supervisor_reports_without_inputs():
    report = supervisor_agent.SupervisorAgent().create_report()

    assert report.file_plan is None
    assert report.browser_snapshot is None
    assert any("root_dir" in note for note in report.notes)


def test_supervisor_plans_files_without_executing(tmp_path):
    (tmp_path / "2026-01-02 factura.pdf").write_bytes(b"%PDF-1.4 fake")

    report = supervisor_agent.SupervisorAgent().create_report(root_dir=str(tmp_path))

    assert report.file_plan is not None
    # El supervisor solo planifica: la carpeta no debe existir.
    assert not (tmp_path / "PDFs").exists()
