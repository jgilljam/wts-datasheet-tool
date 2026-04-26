"""PDF → Text. Bewusst dünn gehalten — Gemini soll den Rest machen."""

from pathlib import Path
from typing import IO, Union

from pypdf import PdfReader


def extract_text(source: Union[Path, IO[bytes]]) -> str:
    """Akzeptiert einen Dateipfad oder ein file-like object (z.B. Streamlit-UploadedFile)."""
    name = getattr(source, "name", None) or (source.name if isinstance(source, Path) else "PDF")
    reader = PdfReader(str(source) if isinstance(source, Path) else source)

    chunks: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()
        if not text:
            continue
        chunks.append(f"--- Seite {i} ---\n{text}")
    if not chunks:
        raise ValueError(f"Kein Text aus {name} extrahierbar (Scan-PDF?).")
    return "\n\n".join(chunks)


def extract_text_from_uploaded(uploaded) -> str:
    """Streamlit-UploadedFile-Wrapper. Setzt den Stream-Zeiger zurück, falls schon gelesen."""
    uploaded.seek(0)
    return extract_text(uploaded)
