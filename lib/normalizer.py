"""PDF-Text → Komponente (Pydantic) via Gemini Structured Output."""

from datetime import date
from pathlib import Path

from google import genai
from google.genai import types

from .leitplanken import (
    Violation,
    format_violations,
    normalize_hersteller_visibility,
    scan_text,
)
from .schema import Komponente


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class NormalizerError(RuntimeError):
    pass


def _load_system_prompt(name: str = "system_de.txt") -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _run_gemini(
    contents: str,
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    temperature: float,
) -> Komponente:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=Komponente,
            temperature=temperature,
        ),
    )

    parsed: Komponente | None = response.parsed
    if parsed is None:
        raise NormalizerError(
            "Gemini hat kein gültiges JSON nach Schema geliefert.\n"
            f"Raw: {(response.text or '')[:500]}"
        )
    return parsed


def _finalize(parsed: Komponente) -> Komponente:
    today = date.today().isoformat()
    if parsed.publishedAt in ("", "TODAY"):
        parsed.publishedAt = today
    if parsed.updatedAt in ("", "TODAY"):
        parsed.updatedAt = today

    data = parsed.model_dump()
    data = normalize_hersteller_visibility(data)
    parsed = Komponente.model_validate(data)

    post_violations = _scan_komponente(parsed)
    if post_violations:
        raise NormalizerError(format_violations(post_violations))
    return parsed


def normalize(datasheet_text: str, *, api_key: str, model: str = "gemini-2.5-flash-lite") -> tuple[Komponente, list[str]]:
    """PDF-Text → Komponente. Returns (komponente, warnings)."""
    warnings: list[str] = []

    pre_violations = scan_text(datasheet_text, location="datasheet-input")
    if pre_violations:
        terms = sorted({v.term for v in pre_violations})
        warnings.append(
            f"Quell-PDF enthält Begriffe ({', '.join(terms)}) — "
            "Gemini formuliert um, Output wird gegen Leitplanken geprüft."
        )

    parsed = _run_gemini(
        datasheet_text,
        api_key=api_key,
        model=model,
        system_prompt=_load_system_prompt("system_de.txt"),
        temperature=0.2,
    )
    return _finalize(parsed), warnings


def normalize_from_description(
    description: str, *, api_key: str, model: str = "gemini-2.5-flash-lite"
) -> tuple[Komponente, list[str]]:
    """Stichworte/Freitext → vollständige Komponente. KI darf plausibel ergänzen.

    Liefert immer eine Standard-Warnung mit, weil Werte teilweise generiert wurden —
    der Mitarbeiter soll vor dem Veröffentlichen gegenchecken.
    """
    warnings: list[str] = [
        "Werte wurden teilweise von der KI ergänzt (kein Hersteller-PDF als Quelle). "
        "Bitte im Tab „Bearbeiten“ gegenchecken, bevor du die Komponente veröffentlichst."
    ]

    pre_violations = scan_text(description, location="description-input")
    if pre_violations:
        terms = sorted({v.term for v in pre_violations})
        warnings.append(
            f"Eingabe enthält Begriffe ({', '.join(terms)}) — "
            "Gemini formuliert um, Output wird gegen Leitplanken geprüft."
        )

    parsed = _run_gemini(
        description,
        api_key=api_key,
        model=model,
        system_prompt=_load_system_prompt("system_from_text_de.txt"),
        temperature=0.4,
    )
    return _finalize(parsed), warnings


def _scan_komponente(k: Komponente) -> list[Violation]:
    fields_to_scan: list[tuple[str, str]] = [
        ("titel", k.titel),
        ("titel_en", k.titel_en),
        ("kurzbeschreibung", k.kurzbeschreibung),
        ("kurzbeschreibung_en", k.kurzbeschreibung_en),
        ("beschreibung", k.beschreibung),
        ("beschreibung_en", k.beschreibung_en),
    ]
    for i, s in enumerate(k.specs):
        fields_to_scan.append((f"specs[{i}].label", s.label))
        fields_to_scan.append((f"specs[{i}].value", s.value))
        fields_to_scan.append((f"specs[{i}].label_en", s.label_en))
        fields_to_scan.append((f"specs[{i}].value_en", s.value_en))
    for i, t in enumerate(k.tags):
        fields_to_scan.append((f"tags[{i}]", t))
    for i, a in enumerate(k.anwendungen):
        fields_to_scan.append((f"anwendungen[{i}]", a))
    for i, a in enumerate(k.anwendungen_en):
        fields_to_scan.append((f"anwendungen_en[{i}]", a))

    found: list[Violation] = []
    for location, text in fields_to_scan:
        if text:
            found.extend(scan_text(text, location=location))
    return found
