"""Konstanten für Eingangsrechnungen."""

INCOMING_STATUSES = [
    "received",     # PDF hochgeladen, OCR durch — wartet auf Review
    "in_review",    # Wird gerade geprüft
    "approved",     # Freigegeben zur Zahlung
    "paid",         # Bezahlt (entweder bezahlt oder via Banking-Match)
    "disputed",     # Reklamation — Streit mit Lieferant
    "cancelled",    # Storniert/Falscherfassung
]

INCOMING_STATUS_LABELS = {
    "received":  "Eingegangen",
    "in_review": "In Prüfung",
    "approved":  "Freigegeben",
    "paid":      "Bezahlt",
    "disputed":  "Reklamation",
    "cancelled": "Storniert",
}

INCOMING_STATUS_COLORS = {
    "received":  "#9CA3AF",
    "in_review": "#FBBF24",
    "approved":  "#3B82F6",
    "paid":      "#059669",
    "disputed":  "#EF4444",
    "cancelled": "#6B7280",
}

INCOMING_DONE_STATUSES = {"paid", "cancelled"}

INCOMING_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "received":  {"in_review", "approved", "disputed", "cancelled"},
    "in_review": {"approved", "disputed", "cancelled"},
    "approved":  {"paid", "disputed"},
    "disputed":  {"in_review", "approved", "cancelled"},
    "paid":      {"disputed"},   # nachträgliche Reklamation
    "cancelled": set(),          # terminal
}

CONFIDENCE_LABELS = {
    "high":   "🟢 Hoch",
    "medium": "🟡 Mittel",
    "low":    "🔴 Niedrig",
}
