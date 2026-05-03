"""Domain-Konstanten für Verkaufs-Aufträge — Stati, DE-Labels, Farben."""

# Status-Flow (DB-seitig per CHECK-Constraint enforced; siehe schema.sql)
ORDER_STATUSES = [
    "draft",          # Entwurf — frei editierbar
    "confirmed",      # Bestätigt — Auftragsbestätigung verschickt; Items GoBD-gesperrt
    "in_production",  # In Produktion / Beschaffung läuft
    "partial",        # Teilweise geliefert (mind. 1 Lieferung erstellt, aber nicht alles)
    "shipped",        # Komplett geliefert
    "done",           # Abgeschlossen + bezahlt
    "cancelled",      # Storniert
]

# Hauptflow (für Stepper) — terminale Sonderzustände werden separat gerendert
ORDER_FLOW = ["draft", "confirmed", "in_production", "partial", "shipped", "done"]
ORDER_TERMINAL = {"cancelled"}

ORDER_STATUS_LABELS = {
    "draft": "Entwurf",
    "confirmed": "Bestätigt",
    "in_production": "In Produktion",
    "partial": "Teilgeliefert",
    "shipped": "Geliefert",
    "done": "Abgeschlossen",
    "cancelled": "Storniert",
}

ORDER_STATUS_COLORS = {
    "draft": "#9CA3AF",
    "confirmed": "#3B82F6",
    "in_production": "#8B5CF6",
    "partial": "#FBBF24",
    "shipped": "#10B981",
    "done": "#059669",
    "cancelled": "#6B7280",
}

# Stati, ab denen Items GoBD-gesperrt sind (kein replace_items mehr)
ORDER_LOCKED_STATUSES = {"confirmed", "in_production", "partial", "shipped", "done"}

# Stati, die als „erledigt" gelten (Default-Filter blendet sie aus)
ORDER_DONE_STATUSES = {"done", "cancelled"}

# Empfohlener nächster Schritt pro Status (für dominanten Aktions-Button)
ORDER_NEXT_ACTION = {
    "draft":         ("confirmed",     "✓ Bestätigen"),
    "confirmed":     ("in_production", "🔧 In Produktion setzen"),
    "in_production": ("shipped",       "📦 Geliefert markieren"),
    "partial":       ("shipped",       "📦 Komplett geliefert markieren"),
    "shipped":       ("done",          "✓ Abschließen"),
}

# USt-Default-Sätze
TAX_RATE_DEFAULT = 19  # Prozent (DE Regel-USt)
TAX_RATE_REDUCED = 7
TAX_RATE_REVERSE_CHARGE = 0

INCOTERMS_2020 = ["EXW", "FCA", "FAS", "FOB", "CPT", "CIP", "CFR", "CIF", "DAP", "DPU", "DDP"]
