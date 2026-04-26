"""Holt ein PDF von einer URL und liefert die Bytes (mit Sanity-Limits)."""

from __future__ import annotations

import io
import urllib.request
import urllib.error


MAX_BYTES = 25 * 1024 * 1024  # 25 MB
TIMEOUT_S = 30
USER_AGENT = "WTS-Datasheet-Bot/1.0 (+https://wts-trading.de)"


class PdfFetchError(RuntimeError):
    pass


def fetch_pdf(url: str) -> bytes:
    """GET die URL, gib PDF-Bytes zurück. Wirft PdfFetchError bei Problemen."""
    if not url.lower().startswith(("http://", "https://")):
        raise PdfFetchError(f"Ungültige URL: {url}")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "pdf" not in ctype and not url.lower().endswith(".pdf"):
                raise PdfFetchError(
                    f"URL liefert kein PDF (Content-Type: {ctype or 'unbekannt'})"
                )

            buf = io.BytesIO()
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise PdfFetchError(
                        f"PDF zu groß (> {MAX_BYTES // (1024*1024)} MB)"
                    )
                buf.write(chunk)
            return buf.getvalue()
    except urllib.error.HTTPError as e:
        raise PdfFetchError(f"HTTP {e.code} beim Abruf von {url}") from e
    except urllib.error.URLError as e:
        raise PdfFetchError(f"Netzwerkfehler beim Abruf von {url}: {e.reason}") from e
    except TimeoutError as e:
        raise PdfFetchError(f"Timeout (>{TIMEOUT_S}s) beim Abruf von {url}") from e
