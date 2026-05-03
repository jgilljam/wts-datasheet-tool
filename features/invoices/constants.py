"""Domain-Konstanten für Rechnungen — Stati, DE-Labels, Farben."""

# Status-Flow (DB-seitig per CHECK-Constraint enforced; siehe 0006_invoices_full.sql)
INVOICE_STATUSES = [
    "draft",            # Editierbar, KEINE Rechnungsnr. (GoBD: Nr. erst bei issued vergeben)
    "issued",           # Festgeschrieben, Nr. vergeben, Items immutable
    "partially_paid",   # Anzahlung eingegangen
    "paid",             # Vollständig beglichen
    "overdue",          # Fälligkeitsdatum überschritten (auto via Cron — Phase K)
    "cancelled",        # Vor issued storniert (Draft weggeworfen)
    "reversed",         # Nach issued storniert; Storno-Beleg mit reverses_id existiert
]

# Hauptflow für Stepper
INVOICE_FLOW = ["draft", "issued", "partially_paid", "paid"]
INVOICE_TERMINAL = {"cancelled", "reversed", "overdue"}

INVOICE_STATUS_LABELS = {
    "draft": "Entwurf",
    "issued": "Gestellt",
    "partially_paid": "Teilbezahlt",
    "paid": "Bezahlt",
    "overdue": "Überfällig",
    "cancelled": "Storniert",
    "reversed": "Storniert (Gegenbeleg)",
}

INVOICE_STATUS_COLORS = {
    "draft": "#9CA3AF",
    "issued": "#3B82F6",
    "partially_paid": "#FBBF24",
    "paid": "#059669",
    "overdue": "#EF4444",
    "cancelled": "#6B7280",
    "reversed": "#6B7280",
}

# Stati, ab denen Items + Header GoBD-gesperrt sind (kein replace_items mehr)
INVOICE_LOCKED_STATUSES = {"issued", "partially_paid", "paid", "overdue", "reversed"}

# Stati, die als „erledigt" gelten (Default-Filter blendet aus)
INVOICE_DONE_STATUSES = {"paid", "cancelled", "reversed"}

# Empfohlener nächster Schritt pro Status
INVOICE_NEXT_ACTION = {
    "draft":          ("issued",         "✓ Festschreiben & ausstellen"),
    "issued":         ("paid",           "💶 Vollzahlung erfassen"),
    "partially_paid": ("paid",           "💶 Vollzahlung erfassen"),
    "overdue":        ("paid",           "💶 Vollzahlung erfassen"),
}

# Gründe für Storno (Pflicht-Dropdown beim Stornieren)
CANCELLATION_REASONS = [
    "Falscher Empfänger",
    "Falsche Beträge / Preise",
    "Doppelt fakturiert",
    "Komplett-Retoure des Kunden",
    "Kulanz / Storno auf Kundenwunsch",
    "Sonstiger Grund",
]

INCOTERMS_2020 = ["EXW", "FCA", "FAS", "FOB", "CPT", "CIP", "CFR", "CIF", "DAP", "DPU", "DDP"]

# USt-Sätze
TAX_RATE_DEFAULT = 19
TAX_RATE_REDUCED = 7
TAX_RATE_REVERSE_CHARGE = 0
