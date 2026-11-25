"""Small text-only PDF writer. No extra package and no embedded workforce files."""

from __future__ import annotations


def text_pdf(lines: list[str], *, title: str = "Clynotion") -> bytes:
    """One or more pages of plain lines."""
    pages: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        text = (line or "").replace("\t", "    ")
        if len(current) >= 46:
            pages.append(current)
            current = []
        current.append(text[:110])
    if not pages and not current:
        current = [title]
    if current:
        pages.append(current)

    chunks: list[bytes] = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets: list[int] = []

    def obj(number: int, body: bytes) -> None:
        offsets.append(sum(len(part) for part in chunks))
        chunks.append(f"{number} 0 obj\n".encode("ascii"))
        chunks.append(body)
        chunks.append(b"\nendobj\n")

    page_count = len(pages)
    kids = " ".join(f"{4 + (index * 2)} 0 R" for index in range(page_count))
    obj(1, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    obj(2, f"<< /Type /Pages /Count {page_count} /Kids [{kids}] >>".encode("ascii"))
    for index, lines_on_page in enumerate(pages):
        shown = lines_on_page
        commands = ["BT", "/F1 10 Tf", "50 760 Td", "14 TL"]
        for line_index, line in enumerate(shown):
            if line_index:
                commands.append("T*")
            safe = (line or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({safe}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1", errors="replace")
        content = b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        obj(3 + index * 2, content)
        page = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {3 + index * 2} 0 R /Resources << /Font << /F1 1 0 R >> >> >>"
        ).encode("ascii")
        obj(4 + index * 2, page)
    catalog_number = 3 + page_count * 2
    obj(catalog_number, b"<< /Type /Catalog /Pages 2 0 R >>")
    startxref = sum(len(part) for part in chunks)
    count = catalog_number + 1
    chunks.append(f"xref\n0 {count}\n".encode("ascii"))
    chunks.append(b"0000000000 65535 f \n")
    for offset in offsets:
        chunks.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    chunks.append(
        f"trailer\n<< /Size {count} /Root {catalog_number} 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode("ascii")
    )
    return b"".join(chunks)
