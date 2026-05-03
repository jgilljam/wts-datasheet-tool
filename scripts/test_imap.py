"""Quick IMAP-Connection-Test — liest secrets.toml und checkt Login + INBOX-Stats.
Gibt KEIN Passwort aus, nur Status."""

from __future__ import annotations

import imaplib
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore

SECRETS = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml"


def test(mailbox: str) -> None:
    pfx = f"IMAP_{mailbox.upper()}"
    with open(SECRETS, "rb") as f:
        cfg = tomllib.load(f)
    user = cfg.get(f"{pfx}_USER")
    password = cfg.get(f"{pfx}_PASSWORD")
    host = cfg.get(f"{pfx}_HOST", "imap.ionos.de")
    port = int(cfg.get(f"{pfx}_PORT", 993))

    if not user or not password:
        print(f"[{mailbox}@] ❌ kein User/Passwort in secrets.toml")
        return

    print(f"[{mailbox}@] verbinde {host}:{port} als {user} …")
    try:
        M = imaplib.IMAP4_SSL(host, port, timeout=15)
        M.login(user, password)
    except imaplib.IMAP4.error as e:
        print(f"[{mailbox}@] ❌ LOGIN-Fehler: {e}")
        return
    except Exception as e:
        print(f"[{mailbox}@] ❌ Connection-Fehler: {type(e).__name__}: {e}")
        return

    try:
        typ, data = M.select("INBOX")
        if typ != "OK":
            print(f"[{mailbox}@] ⚠️  SELECT INBOX failed: {typ}")
            return
        total = int(data[0])
        typ, data = M.search(None, "UNSEEN")
        unseen = len((data[0] or b"").split())
        print(f"[{mailbox}@] ✅ OK — INBOX: {total} mails total, {unseen} ungelesen")
    finally:
        try:
            M.close()
        except Exception:
            pass
        M.logout()


if __name__ == "__main__":
    mailboxes = sys.argv[1:] or ["sales", "invoice"]
    for mb in mailboxes:
        test(mb)
