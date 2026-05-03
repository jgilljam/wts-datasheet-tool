"""End-to-End-Test: pull_all_mailboxes → incoming_mails → ggf. KI-Klassifikation.

Ohne Streamlit-Runtime — lädt secrets.toml direkt in os.environ-Stil und
überschreibt st.secrets via Monkey-Patch.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Streamlit-Secrets monkey-patchen
import streamlit as st
with open(ROOT / ".streamlit" / "secrets.toml", "rb") as f:
    _cfg = tomllib.load(f)


class _FakeSecrets(dict):
    def get(self, k, default=None):
        return super().get(k, default)


# st.secrets ist sonst ein SecretsBackend — wir überschreiben es
st.secrets = _FakeSecrets(_cfg)  # type: ignore


from lib import imap_inbox

print("Starte Pull …")
results = imap_inbox.pull_all_mailboxes()
for mb, res in results.items():
    print(f"  {mb}@: {res}")
