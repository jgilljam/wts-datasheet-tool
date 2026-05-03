# WTS-Tool

Browser-basiertes ERP/Mitarbeiter-Tool für WTS Trading & Service: Datenblätter, Aufträge, Lieferungen, Lager, Rechnungen, Mahnungen, Eingangsrechnungen, Angebote, Bestellungen.

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
GEMINI_API_KEY = "AIzaSy..."
GEMINI_MODEL = "gemini-2.5-flash-lite"
SUPABASE_URL = "https://<projekt>.supabase.co"
SUPABASE_SECRET_KEY = "..."
SUPABASE_PAT = "..."         # nur lokal — für Migrations
SUPABASE_PROJECT_REF = "..." # nur lokal — für Migrations
```

Starten:

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib streamlit run streamlit_app.py
```

Browser öffnet sich automatisch auf `http://localhost:8501`.

## Erst-Einrichtung (beim ersten Start)

1. App starten — beim ersten Aufruf erscheint der Erst-Einrichtungs-Wizard
2. Email + Name + Passwort (≥8 Zeichen) eingeben → erster Admin-Account wird angelegt
3. Einloggen → in „Mitarbeiter" → „Mein Account" 2FA aktivieren (empfohlen)
4. Über „Mitarbeiter" → „Neuen Mitarbeiter anlegen" weitere Accounts hinzufügen

## Online-Deployment (Streamlit Community Cloud)

1. **GitHub-Repo** committen + pushen (ohne `.streamlit/secrets.toml`!)
2. Auf https://share.streamlit.io anmelden, neue App, Repo auswählen, Hauptdatei `streamlit_app.py`
3. Im App-Settings unter „Secrets" eintragen: `GEMINI_*`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`. **NICHT** in Cloud: `SUPABASE_PAT` (nur lokal für Migrations).
4. Deploy → Link an Mitarbeiter — beim ersten Klick führt der Erst-Einrichtungs-Wizard durch

`packages.txt` enthält die System-Pakete (Pango etc.), die Streamlit Cloud via apt installiert.

## Sicherheit

- **Multi-User-Auth:** bcrypt-gehashte Passwörter, optional TOTP-2FA (Google/Microsoft Authenticator)
- **Failed-Login-Lockout:** 5 Fehlversuche → 15 min Sperre
- **Session-Timeout:** 60 min Inaktivität → Auto-Logout
- **Append-only Audit-Log:** alle Login-Versuche + alle Beleg-Mutationen
- **GoBD-konforme Belegführung:** atomare lückenlose Nummern, eingefrorene Stammdaten-Snapshots, byte-stable PDF-Archiv mit SHA-256-Hash, Items-Lock-Trigger
- **Rollen:** `admin` (Mitarbeiter-Verwaltung), `mitarbeiter` (voller Beleg-Zugriff), `viewer` (read-only — vorbereitet, noch nicht enforced)
- Secrets nur in Streamlit-Settings, nie im Code

## Was die App NICHT macht

- Sie schreibt **nicht** automatisch in die WTS-Astro-Site. Mitarbeiter laden JSON + PDFs herunter; das Einspielen ins Site-Repo + Deploy bleibt manuell (oder via separatem `datasheet-normalizer --publish` lokal auf Julians Mac).
- Sie speichert keine PDFs / JSONs persistent — alles läuft nur in der jeweiligen Session.
