"""Reads the start of an uploaded document so the chat can show a short preview.

PDF, Word, Excel, PowerPoint and plain-text files. It only reads text: no macros, no OCR, nothing inside the document is
ever run. Every limit that guards against hostile files (file type check on the first bytes, zip-bomb size, macros) lives
here. The reader libraries are imported lazily, so the app starts without them until a document is actually opened.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

MAX_UNPACKED_BYTES = 400 * 1024 * 1024      # zip-bomb guard for Word / Excel / PowerPoint
PREVIEW_CHARS = 1500


def scrub(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def check_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as z:
        total = sum(i.file_size for i in z.infolist())
        if total > MAX_UNPACKED_BYTES or len(z.infolist()) > 20000:
            raise ValueError("That file unpacks to something far too big to be a normal document.")
        if any(i.filename.lower().endswith("vbaproject.bin") for i in z.infolist()):
            raise ValueError("That file contains macros. Save a copy without macros and attach that.")


def extract(path: Path, ext: str) -> dict:
    """Return {"kind", "pages", "preview"}; raises ValueError with a plain message when the file is not usable."""
    with path.open("rb") as source:
        head = source.read(8)
    if ext == ".pdf":
        if not head.startswith(b"%PDF"):
            raise ValueError("That does not look like a real PDF.")
        from pypdf import PdfReader
        rd = PdfReader(str(path))
        if rd.is_encrypted:
            raise ValueError("That PDF is password protected. Remove the password and try again.")
        n = len(rd.pages)
        out = ""
        for page in rd.pages[:6]:
            out += (page.extract_text() or "") + "\n\n"
            if len(out) > PREVIEW_CHARS * 2:
                break
        return {"kind": "PDF", "pages": n, "preview": scrub(out)[:PREVIEW_CHARS],
                "scanned": not scrub(out) and n > 0}
    if ext in (".docx", ".xlsx", ".pptx"):
        if not head.startswith(b"PK"):
            raise ValueError("That does not look like a real %s file." % ext.lstrip("."))
        check_zip(path)
        if ext == ".docx":
            import docx
            d = docx.Document(str(path))
            paras = [p.text for p in d.paragraphs if p.text.strip()]
            text = "\n".join(paras)
            return {"kind": "Word document", "pages": None, "preview": scrub(text)[:PREVIEW_CHARS],
                    "extra": "%d paragraphs, %d tables" % (len(paras), len(d.tables))}
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            try:
                names = wb.sheetnames
                lines = []
                ws = wb[names[0]] if names else None
                if ws is not None:
                    for i, row in enumerate(ws.iter_rows(values_only=True)):
                        if i >= 12:
                            break
                        lines.append(" | ".join("" if c is None else str(c) for c in row[:12]))
                return {"kind": "Excel workbook", "pages": None, "preview": scrub("\n".join(lines))[:PREVIEW_CHARS],
                        "extra": "sheets: " + ", ".join(names[:10]) + ("…" if len(names) > 10 else "")}
            finally:
                wb.close()
        from pptx import Presentation
        pr = Presentation(str(path))
        lines = []
        for idx, slide in enumerate(pr.slides, 1):
            if idx > 8:
                break
            bits = [sh.text_frame.text.strip() for sh in slide.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
            if bits:
                lines.append("Slide %d: %s" % (idx, " / ".join(bits)[:300]))
        return {"kind": "PowerPoint deck", "pages": len(pr.slides), "preview": scrub("\n".join(lines))[:PREVIEW_CHARS]}
    # plain text family
    raw = path.read_bytes()[:200_000]
    if b"\x00" in raw:
        raise ValueError("That looks like a binary file, not text.")
    text = raw.decode("utf-8", errors="replace")
    return {"kind": "text file", "pages": None, "preview": scrub(text)[:PREVIEW_CHARS],
            "extra": "%d lines in the part read" % (text.count("\n") + 1)}
