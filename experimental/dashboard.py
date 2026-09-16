from __future__ import annotations

import argparse
import os
import sqlite3

from .metrics import get_metrics_summary

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:  # pragma: no cover - only used without optional dependency
    Console = None
    Panel = None
    Table = None


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# Tablas que el dashboard tiene permitido consultar. Es una whitelist explicita
# porque el nombre de tabla se interpola en el SQL (los placeholders `?` de
# sqlite3 solo sirven para valores, no para identificadores) y nunca debe
# provenir de datos externos.
ALLOWED_TABLES = frozenset({"plans", "actions", "executions", "errors", "metrics", "documents"})


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _safe_count(conn: sqlite3.Connection, table_name: str) -> int:
    """Cuenta filas de ``table_name`` validandolo contra ``ALLOWED_TABLES``.

    El nombre de tabla no puede parametrizarse con `?`, asi que se valida
    contra una whitelist cerrada antes de interpolarlo. Sin esta validacion un
    valor como ``"plans; DROP TABLE plans; --"`` se ejecutaria como SQL
    arbitrario (inyeccion SQL).
    """
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Tabla no permitida: {table_name}")
    if not _table_exists(conn, table_name):
        return 0
    # El identificador ya fue validado contra ALLOWED_TABLES (línea anterior) y
    # no puede parametrizarse con `?`; la interpolación es segura por esa
    # whitelist cerrada. Se marca para bandit con la razón explícita.
    query = f"SELECT COUNT(*) AS c FROM {table_name}"  # noqa: S608  # nosec B608
    return int(conn.execute(query).fetchone()["c"])


def _render_plain(db_path: str) -> None:
    print("cowork-clone dashboard")
    print(f"DB path: {os.path.abspath(db_path)}")
    if not os.path.exists(db_path):
        print("SQLite: no encontrado")
        return
    with _connect(db_path) as conn:
        for table_name in sorted(ALLOWED_TABLES):
            print(f"{table_name}: {_safe_count(conn, table_name)}")
    print(get_metrics_summary(db_path))


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Devuelve las columnas reales de ``table_name``.

    El schema puede haber evolucionado entre versiones, asi que el dashboard
    consulta solo las columnas que existen en lugar de asumir un contrato fijo:
    un ``SELECT`` sobre una columna ausente abortaria el render entero.
    """
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _render_recent(
    console,
    conn: sqlite3.Connection,
    *,
    title: str,
    table_name: str,
    columns: tuple[str, ...],
    limit: int = 10,
) -> None:
    """Renderiza las ultimas ``limit`` filas de una tabla tolerando schema."""
    table = Table(title=title)
    for column in columns:
        table.add_column(column)
    console.print(table)

    if not _table_exists(conn, table_name):
        return

    available = tuple(c for c in columns if c in _table_columns(conn, table_name))
    if not available:
        return

    selected = ", ".join(available)
    order_by = " ORDER BY id DESC" if "id" in _table_columns(conn, table_name) else ""
    query = f"SELECT {selected} FROM {table_name}{order_by} LIMIT {int(limit)}"  # noqa: S608  # nosec B608
    for row in conn.execute(query).fetchall():
        table.add_row(*(str(row[column]) for column in available))


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
        for table_name in sorted(ALLOWED_TABLES):
            counts.add_row(table_name, str(_safe_count(conn, table_name)))
        console.print(counts)

        _render_recent(
            console,
            conn,
            title="Planes",
            table_name="plans",
            columns=("plan_id", "created_at", "status"),
        )
        _render_recent(
            console,
            conn,
            title="Ejecuciones",
            table_name="executions",
            columns=("execution_id", "plan_id", "status"),
        )
        _render_recent(
            console,
            conn,
            title="Errores",
            table_name="errors",
            columns=("execution_id", "timestamp", "error_message"),
        )

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
