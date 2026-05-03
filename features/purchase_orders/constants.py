"""Domain-Konstanten für Einkaufs-Bestellungen — Stati, DE-Labels, Farben."""

# Status-Flow (DB-seitig per CHECK-Constraint enforced; siehe schema.sql)
PO_STATUSES = [
    "draft",          # Entwurf — frei editierbar
    "sent",           # An Lieferant rausgeschickt; Items GoBD-gesperrt
    "confirmed",      # Lieferant hat bestätigt (AB-Datum + Termin)
    "in_production",  # In Produktion / Versand vorbereitet
    "shipped",        # Versandt durch Lieferant
    "partial",        # Teilweise erhalten
    "received",       # Komplett erhalten + eingelagert
    "cancelled",      # Storniert
]

# Hauptflow für Stepper
PO_FLOW = ["draft", "sent", "confirmed", "in_production", "shipped", "partial", "received"]
PO_TERMINAL = {"cancelled"}

PO_STATUS_LABELS = {
    "draft": "Entwurf",
    "sent": "Versendet",
    "confirmed": "Bestätigt (Lieferant)",
    "in_production": "In Produktion",
    "shipped": "Versandt durch Lieferant",
    "partial": "Teilweise erhalten",
    "received": "Erhalten",
    "cancelled": "Storniert",
}

PO_STATUS_COLORS = {
    "draft": "#9CA3AF",
    "sent": "#3B82F6",
    "confirmed": "#3B82F6",
    "in_production": "#8B5CF6",
    "shipped": "#F59E0B",
    "partial": "#FBBF24",
    "received": "#059669",
    "cancelled": "#6B7280",
}

# Stati, ab denen Items GoBD-gesperrt sind
PO_LOCKED_STATUSES = {"sent", "confirmed", "in_production", "shipped", "partial", "received"}

# Stati, die als „erledigt" gelten (Default-Filter blendet sie aus)
PO_DONE_STATUSES = {"received", "cancelled"}

# Empfohlener nächster Schritt pro Status
PO_NEXT_ACTION = {
    "draft":         ("sent",          "✉ An Lieferant senden"),
    "sent":          ("confirmed",     "✓ Lieferanten-Bestätigung erfassen"),
    "confirmed":     ("in_production", "🔧 In Produktion setzen"),
    "in_production": ("shipped",       "🚚 Versand durch Lieferant"),
    "shipped":       ("received",      "📦 Wareneingang abschließen"),
    "partial":       ("received",      "📦 Komplett erhalten markieren"),
}

# Erlaubte Status-Übergänge
PO_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft":         {"sent", "cancelled"},
    "sent":          {"confirmed", "cancelled"},
    "confirmed":     {"in_production", "shipped", "partial", "cancelled"},
    "in_production": {"shipped", "partial", "cancelled"},
    "shipped":       {"partial", "received", "cancelled"},
    "partial":       {"received", "cancelled"},
    "received":      set(),
    "cancelled":     set(),
}

TAX_RATE_DEFAULT = 19  # Prozent
TAX_RATE_REVERSE_CHARGE = 0  # EU-Lieferant mit USt-ID + Reverse-Charge

INCOTERMS_2020 = ["EXW", "FCA", "FAS", "FOB", "CPT", "CIP", "CFR", "CIF", "DAP", "DPU", "DDP"]
