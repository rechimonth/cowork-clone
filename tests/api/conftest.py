"""Fixtures compartidas de los tests de la API."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.config import ApiConfig  # noqa: E402

TOKEN = "token-de-test-suficientemente-largo"


@pytest.fixture
def api_config(tmp_path) -> ApiConfig:
    """Configuración de API con token y estado en un tmp_path aislado."""
    return ApiConfig(
        token=TOKEN,
        allowed_roots=(),
        cors_origins=("http://localhost:1420",),
        state_dir=tmp_path / "state",
        approval_timeout_s=10,
        allow_insecure=False,
        max_sessions=50,
    )


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def root_dir(tmp_path) -> Path:
    """Directorio raíz de trabajo con un par de archivos."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "notas.txt").write_text("contenido de notas")
    (root / "informe.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return root
