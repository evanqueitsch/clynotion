"""DOCX to PDF via LibreOffice, plus a one-page acceptance certificate.

Calibri is substituted with Carlito through fontconfig in the image.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import zipfile
from io import BytesIO
from pathlib import Path


class PdfRenderError(RuntimeError):
    pass


_CONVERT_LOCK = threading.Lock()


def _escape(text: str) -> str:
    return (
        text.replace("\\", " ")
        .replace("(", "[")
        .replace(")", "]")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


def text_pdf(lines: list[str]) -> bytes:
    """A single-page PDF with a few lines of text. No third-party renderer."""
    commands = ["BT", "/F1 11 Tf", "54 740 Td", "16 TL"]
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        commands.append(f"({_escape(line)[:120]}) Tj")
    commands.append("ET")
    stream = ("\n".join(commands) + "\n").encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


def soffice_available() -> bool:
    return shutil.which("soffice") is not None or shutil.which("libreoffice") is not None


def soffice_profile() -> Path:
    path = Path(os.environ.get("ATTUNE_SOFFICE_PROFILE") or "/tmp/clynotion-soffice")
    path.mkdir(parents=True, exist_ok=True)
    return path


def soffice_command(binary: str, source: Path, folder: Path) -> list[str]:
    """Reuse one LibreOffice profile so the first real letter is not a cold start."""
    profile = soffice_profile().resolve().as_uri()
    return [
        binary,
        f"-env:UserInstallation={profile}",
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        "pdf",
        "--outdir",
        str(folder),
        str(source),
    ]


def _tiny_docx() -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Warm</w:t></w:r></w:p></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", rels)
        package.writestr("word/document.xml", document)
    return buf.getvalue()


def docx_to_pdf(docx: bytes) -> bytes:
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    if not binary:
        raise PdfRenderError("LibreOffice is not installed")
    with _CONVERT_LOCK:
        with tempfile.TemporaryDirectory(prefix="clynotion-offer-") as folder:
            source = Path(folder) / "offer.docx"
            source.write_bytes(docx)
            try:
                completed = subprocess.run(
                    soffice_command(binary, source, Path(folder)),
                    check=False,
                    capture_output=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired as exc:
                raise PdfRenderError("PDF conversion timed out") from exc
            pdf = Path(folder) / "offer.pdf"
            if completed.returncode != 0 or not pdf.is_file():
                raise PdfRenderError("PDF conversion failed")
            return pdf.read_bytes()


def warm_offer_pdf() -> None:
    """Start LibreOffice once at boot. Later letter saves reuse that profile."""
    if os.environ.get("ATTUNE_MODE") != "real" and os.environ.get("ATTUNE_OFFER_PDF") == "stub":
        return
    if not soffice_available():
        return
    try:
        docx_to_pdf(_tiny_docx())
    except Exception:
        return


def render_offer_pdf(docx: bytes) -> bytes:
    """Production always uses LibreOffice. Tests may set ATTUNE_OFFER_PDF=stub."""
    if os.environ.get("ATTUNE_MODE") != "real" and os.environ.get("ATTUNE_OFFER_PDF") == "stub":
        return text_pdf(["Offer letter", "Synthetic test render"])
    return docx_to_pdf(docx)


def append_certificate(pdf: bytes, lines: list[str]) -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    reader = PdfReader(io_bytes(pdf))
    for page in reader.pages:
        writer.add_page(page)
    extra = PdfReader(io_bytes(text_pdf(lines)))
    writer.add_page(extra.pages[0])
    sink = tempfile.SpooledTemporaryFile()
    writer.write(sink)
    sink.seek(0)
    return sink.read()


def io_bytes(blob: bytes):
    import io

    return io.BytesIO(blob)
