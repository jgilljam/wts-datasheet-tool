"""Domain-Konstanten für Angebote — Stati, DE-Labels, Übergänge."""

QUOTATION_STATUSES = [
    "draft",       # Editierbar
    "sent",        # An Kunden verschickt — wartet auf Antwort
    "accepted",    # Kunde hat angenommen — wird gleich konvertiert
    "rejected",    # Kunde lehnt ab
    "expired",     # Gültigkeit abgelaufen ohne Antwort
    "converted",   # Umgewandelt in Auftrag (terminal)
    "cancelled",   # Vor sent verworfen
]

QUOTATION_FLOW = ["draft", "sent", "accepted", "converted"]
QUOTATION_TERMINAL = {"rejected", "expired", "converted", "cancelled"}

QUOTATION_STATUS_LABELS = {
    "draft":     "Entwurf",
    "sent":      "Versendet",
    "accepted":  "Angenommen",
    "rejected":  "Abgelehnt",
    "expired":   "Abgelaufen",
    "converted": "Konvertiert",
    "cancelled": "Storniert",
}

QUOTATION_STATUS_COLORS = {
    "draft":     "#9CA3AF",
    "sent":      "#3B82F6",
    "accepted":  "#10B981",
    "rejected":  "#EF4444",
    "expired":   "#A78BFA",
    "converted": "#059669",
    "cancelled": "#6B7280",
}

QUOTATION_DONE_STATUSES = {"converted", "rejected", "expired", "cancelled"}

QUOTATION_NEXT_ACTION = {
    "draft":    ("sent",      "📤 An Kunden senden"),
    "sent":     ("accepted",  "✅ Angenommen verbuchen"),
    "accepted": ("converted", "🔄 In Auftrag umwandeln"),
    "expired":  ("draft",     "📝 Reaktivieren"),
}

# Erlaubte Übergänge
QUOTATION_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft":     {"sent", "cancelled"},
    "sent":      {"accepted", "rejected", "expired", "draft"},
    "accepted":  {"converted", "rejected"},
    "rejected":  {"draft"},   # Reaktivierung erlaubt
    "expired":   {"draft"},
    "converted": set(),       # terminal
    "cancelled": set(),
}

DEFAULT_VALIDITY_DAYS = 30  # Standard-Gültigkeit für neue Angebote
