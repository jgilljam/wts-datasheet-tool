# WTS Datenblatt-Tool (Web-UI)

Browser-Tool für Mitarbeiter: Hersteller-PDF reinziehen → Komponenten-JSON + WTS-gebrandete Datenblätter (DE/EN) zum Download.

Drei Tools auf einer Seite gebündelt:
1. PDF-Text-Extraktion (pypdf)
2. KI-Normalisierung (Gemini + Leitplanken-Check)
3. PDF-Erzeugung im WTS-Branding (WeasyPrint)

## Lokal ausführen

Voraussetzung: Python 3.11+, Pango/Cairo (`brew install pango`).

```bash
cd ~/wts-tools/datasheet-webapp
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Secrets liegen lokal in `.streamlit/secrets.toml` (nicht im Git, siehe `.gitignore`):

```toml
APP_PASSWORD = "wts-2026"
GEMINI_API_KEY = "AIzaSy..."
GEMINI_MODEL = "gemini-2.5-flash-lite"
```

Starten:

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib streamlit run streamlit_app.py
```

Browser öffnet sich automatisch auf `http://localhost:8501`.

## Online-Deployment (Streamlit Community Cloud, kostenlos)

1. **GitHub-Repo anlegen** und diesen Ordner pushen (ohne `.streamlit/secrets.toml`!).
2. Auf https://share.streamlit.io anmelden mit demselben GitHub-Account.
3. **Neue App** anlegen, Repo auswählen, Hauptdatei `streamlit_app.py`.
4. Im App-Settings unter „Secrets" denselben Inhalt wie in der lokalen `secrets.toml` einfügen.
5. Deploy → Link bekommt jeder Mitarbeiter, einloggen mit dem `APP_PASSWORD`.

`packages.txt` enthält die System-Pakete (Pango etc.), die Streamlit Cloud automatisch via apt installiert.

## Sicherheit

- Single-Password-Login. Für mehr Komfort später: SSO (Google) via `streamlit-authenticator`.
- Secrets nur in Streamlit-Settings, nie im Code.
- Free-Tier-Limit von Gemini: 1000 Calls/Tag bei `flash-lite`. Reicht für ein paar Mitarbeiter; bei Engpass Billing aktivieren.

## Was die App NICHT macht

- Sie schreibt **nicht** automatisch in die WTS-Astro-Site. Mitarbeiter laden JSON + PDFs herunter; das Einspielen ins Site-Repo + Deploy bleibt manuell (oder via separatem `datasheet-normalizer --publish` lokal auf Julians Mac).
- Sie speichert keine PDFs / JSONs persistent — alles läuft nur in der jeweiligen Session.
