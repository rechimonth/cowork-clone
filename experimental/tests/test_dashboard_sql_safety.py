"""Tests de seguridad del dashboard experimental (inyeccion SQL).

El nombre de tabla se interpola en el SQL, asi que la unica defensa es la
whitelist ``ALLOWED_TABLES``. Estos tests verifican que un identificador
malicioso es rechazado antes de llegar a sqlite y que no puede destruir datos.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

import pytest

# dashboard.py vive en experimental/ y usa imports relativos (`.metrics`), por
# lo que se importa como parte del paquete `experimental`.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from experimental import dashboard


def _make_db(db_path: pathlib.Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE plans (plan_id TEXT)")
        conn.execute("INSERT INTO plans VALUES ('p1')")


def test_allowed_tables_is_a_closed_whitelist():
    assert (
        frozenset({"plans", "actions", "executions", "errors", "metrics", "documents"})
        == dashboard.ALLOWED_TABLES
    )


def test_safe_count_returns_count_for_allowed_table(tmp_path):
    db = tmp_path / "cowork.db"
    _make_db(db)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        assert dashboard._safe_count(conn, "plans") == 1


def test_safe_count_rejects_sql_injection_attempt(tmp_path):
    db = tmp_path / "cowork.db"
    _make_db(db)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(ValueError):
            dashboard._safe_count(conn, "plans; DROP TABLE plans; --")


def test_injection_attempt_does_not_destroy_data(tmp_path):
    db = tmp_path / "cowork.db"
    _make_db(db)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(ValueError):
            dashboard._safe_count(conn, "plans; DROP TABLE plans; --")
        # La tabla y su fila siguen intactas.
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1


def test_safe_count_rejects_unknown_table(tmp_path):
    db = tmp_path / "cowork.db"
    _make_db(db)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(ValueError):
            dashboard._safe_count(conn, "sqlite_master")


def test_safe_count_returns_zero_for_missing_allowed_table(tmp_path):
    db = tmp_path / "vacia.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        # "actions" esta en la whitelist pero la tabla no existe todavia.
        assert dashboard._safe_count(conn, "actions") == 0


def test_render_dashboard_plain_uses_whitelist(tmp_path, capsys, monkeypatch):
    db = tmp_path / "cowork.db"
    _make_db(db)

    # Se fuerza el camino sin `rich` para que el test no dependa de si la
    # dependencia opcional esta instalada en el entorno.
    monkeypatch.setattr(dashboard, "Console", None)
    monkeypatch.setattr(dashboard, "Panel", None)
    monkeypatch.setattr(dashboard, "Table", None)

    dashboard.render_dashboard(str(db))

    out = capsys.readouterr().out
    assert "plans: 1" in out
    # Nunca consulta tablas fuera de la whitelist.
    assert "sqlite_master" not in out


def test_render_dashboard_plain_reports_missing_db(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(dashboard, "Console", None)
    monkeypatch.setattr(dashboard, "Panel", None)
    monkeypatch.setattr(dashboard, "Table", None)

    dashboard.render_dashboard(str(tmp_path / "no-existe.db"))

    assert "no encontrado" in capsys.readouterr().out


@pytest.mark.skipif(dashboard.Console is None, reason="rich no esta instalado en este entorno")
def test_render_dashboard_with_rich_handles_partial_schema(tmp_path):
    """El schema puede tener menos columnas que las esperadas por el dashboard.

    Antes de la correccion, un `plans` sin `created_at` abortaba el render con
    `sqlite3.OperationalError`. Ahora solo se consultan las columnas presentes.
    """
    db = tmp_path / "cowork.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE plans (id INTEGER PRIMARY KEY, plan_id TEXT)")
        conn.execute("INSERT INTO plans (plan_id) VALUES ('p1')")

    # No debe lanzar.
    dashboard.render_dashboard(str(db))


@pytest.mark.skipif(dashboard.Console is None, reason="rich no esta instalado en este entorno")
def test_render_dashboard_with_rich_reports_missing_db(tmp_path):
    dashboard.render_dashboard(str(tmp_path / "no-existe.db"))
