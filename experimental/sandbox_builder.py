from __future__ import annotations

import argparse
from pathlib import Path


def _build_minimal_pdf() -> bytes:
    """Construye un PDF de una pagina con offsets xref calculados.

    Los offsets de la tabla ``xref`` deben ser las posiciones byte reales de
    cada objeto. Escribirlos a mano es fragil: si no coinciden, ``pypdf``
    reconstruye el archivo a la fuerza (avisa "incorrect startxref pointer") y
    la extraccion de texto puede fallar. Generarlos programaticamente mantiene
    el fixture valido aunque cambie su contenido.
    """
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 12 Tf 30 100 Td (Factura demo sandbox) Tj ET"
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_position = len(out)
    size = len(objects) + 1
    out += b"xref\n0 " + str(size).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(size).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_position).encode() + b"\n%%EOF\n"
    return bytes(out)


MINIMAL_PDF = _build_minimal_pdf()


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
