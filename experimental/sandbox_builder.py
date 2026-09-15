from __future__ import annotations

import argparse
from pathlib import Path


MINIMAL_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 54 >>
stream
BT /F1 12 Tf 30 100 Td (Factura demo sandbox) Tj ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000203 00000 n
trailer
<< /Root 1 0 R /Size 5 >>
startxref
306
%%EOF
"""


def _write_if_missing(path: Path, data: str | bytes) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return True


def build_sandbox(base_dir: str = "sandbox") -> list[Path]:
    """Create a reproducible non-destructive sandbox fixture."""
    root = Path(base_dir).resolve()
    files: dict[str, str | bytes] = {
        "txt/notas.txt": "Notas de prueba para cowork-clone.\nCliente: Amazon\nFecha: 2026-01-15\n",
        "csv/datos.csv": "fecha,proveedor,total\n2026-01-15,Amazon,123.45\n",
        "md/README.md": "# Sandbox cowork-clone\n\nArchivos de prueba para planificación segura.\n",
        "pdf/factura_2026-01-15.pdf": MINIMAL_PDF,
        "nested/level1/level2/resumen.txt": "Archivo anidado para escaneo recursivo.\n",
    }
    paths: list[Path] = []
    for relative, content in files.items():
        path = root / relative
        _write_if_missing(path, content)
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Crear sandbox de prueba para cowork-clone")
    parser.add_argument("--dir", default="sandbox", help="Directorio sandbox")
    args = parser.parse_args()
    paths = build_sandbox(args.dir)
    print("Sandbox listo:")
    for path in paths:
        print(f"- {path}")


if __name__ == "__main__":
    main()
