from __future__ import annotations

import argparse
import os
import sqlite3

from metrics import get_metrics_summary

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except Exception:  # pragma: no cover - only used without optional dependency
    Console = None
    Panel = None
    Table = None


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _safe_count(conn: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()["c"])


def _render_plain(db_path: str) -> None:
    print("cowork-clone dashboard")
    print(f"DB path: {os.path.abspath(db_path)}")
    if not os.path.exists(db_path):
        print("SQLite: no encontrado")
        return
    with _connect(db_path) as conn:
        for table_name in ("plans", "actions", "executions", "errors", "metrics", "documents"):
            print(f"{table_name}: {_safe_count(conn, table_name)}")
    print(get_metrics_summary(db_path))


def render_dashboard(db_path: str) -> None:
    """Render a read-only operational dashboard."""
    if Console is None or Table is None or Panel is None:
        _render_plain(db_path)
        return

    console = Console()
    console.print(Panel.fit("cowork-clone dashboard", style="bold cyan"))

    status = Table(title="Estado del sistema")
    status.add_column("Clave")
    status.add_column("Valor")
    status.add_row("SQLite", "OK" if os.path.exists(db_path) else "No encontrado")
    status.add_row("DB path", os.path.abspath(db_path))
    status.add_row("SAFE_OPERATIONS", "mkdir, rename")
    status.add_row("Agentes", "solo planes / lectura")
    console.print(status)

    if not os.path.exists(db_path):
        console.print("[yellow]La base SQLite todavía no existe.[/yellow]")
        return

    with _connect(db_path) as conn:
        counts = Table(title="Conteos")
        counts.add_column("Tabla")
        counts.add_column("Filas")
        for table_name in ("plans", "actions", "executions", "errors", "metrics", "documents"):
            counts.add_row(table_name, str(_safe_count(conn, table_name)))
        console.print(counts)

        plans = Table(title="Planes")
        plans.add_column("plan_id")
        plans.add_column("created_at")
        plans.add_column("status")
        if _table_exists(conn, "plans"):
            rows = conn.execute(
                "SELECT plan_id, created_at, status FROM plans ORDER BY id DESC LIMIT 10"
            ).fetchall()
            for row in rows:
                plans.add_row(str(row["plan_id"]), str(row["created_at"]), str(row["status"]))
        console.print(plans)

        executions = Table(title="Ejecuciones")
        executions.add_column("execution_id")
        executions.add_column("plan_id")
        executions.add_column("status")
        if _table_exists(conn, "executions"):
            rows = conn.execute(
                "SELECT execution_id, plan_id, status FROM executions ORDER BY id DESC LIMIT 10"
            ).fetchall()
            for row in rows:
                executions.add_row(str(row["execution_id"]), str(row["plan_id"]), str(row["status"]))
        console.print(executions)

        errors = Table(title="Errores")
        errors.add_column("execution_id")
        errors.add_column("timestamp")
        errors.add_column("error")
        if _table_exists(conn, "errors"):
            rows = conn.execute(
                "SELECT execution_id, timestamp, error_message FROM errors ORDER BY id DESC LIMIT 10"
            ).fetchall()
            for row in rows:
                errors.add_row(str(row["execution_id"]), str(row["timestamp"]), str(row["error_message"]))
        console.print(errors)

    summary = get_metrics_summary(db_path)
    metrics_table = Table(title="Métricas")
    metrics_table.add_column("Métrica")
    metrics_table.add_column("Cantidad")
    metrics_table.add_column("Total")
    for name, data in summary["by_name"].items():
        metrics_table.add_row(name, str(data["count"]), str(data["total_value"]))
    console.print(metrics_table)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dashboard CLI de cowork-clone")
    parser.add_argument("--db", default="./cowork-clone.db", help="Ruta a SQLite")
    args = parser.parse_args()
    render_dashboard(args.db)


if __name__ == "__main__":
    main()
