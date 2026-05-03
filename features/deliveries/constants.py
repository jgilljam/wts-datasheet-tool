"""Domain-Konstanten für Lieferungen — Stati, Methoden, Locations, Labels."""


# ---------- Status (App-seitig enforced; DB lässt Text frei für Flexibilität) ----------

INBOUND_STATUSES = [
    "announced", "ordered", "confirmed", "in_production",
    "shipped", "in_transit", "arrived", "partial_received",
    "received", "inspected", "stored", "complaint", "cancelled",
]

OUTBOUND_STATUSES = [
    "draft", "picking", "packed", "ready_for_pickup",
    "handed_to_carrier", "in_transit", "delivered",
    "returned", "cancelled",
]

# Erlaubte Übergänge (App-seitig enforced — DB lässt Text frei).
# Forward-Flow + jederzeit cancelled erlaubt.
INBOUND_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "announced":         {"ordered", "confirmed", "cancelled"},
    "ordered":           {"confirmed", "in_production", "shipped", "cancelled"},
    "confirmed":         {"in_production", "shipped", "in_transit", "cancelled"},
    "in_production":     {"shipped", "in_transit", "cancelled"},
    "shipped":           {"in_transit", "arrived", "partial_received", "received", "cancelled"},
    "in_transit":        {"arrived", "partial_received", "received", "cancelled"},
    "arrived":           {"partial_received", "received", "inspected", "complaint", "cancelled"},
    "partial_received":  {"received", "inspected", "complaint", "cancelled"},
    "received":          {"inspected", "stored", "complaint", "cancelled"},
    "inspected":         {"stored", "complaint", "cancelled"},
    "stored":            {"complaint"},
    "complaint":         {"received", "stored", "cancelled"},
    "cancelled":         set(),
}

OUTBOUND_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft":              {"picking", "cancelled"},
    "picking":            {"packed", "ready_for_pickup", "cancelled"},
    "packed":             {"ready_for_pickup", "handed_to_carrier", "cancelled"},
    "ready_for_pickup":   {"handed_to_carrier", "in_transit", "delivered", "cancelled"},
    "handed_to_carrier":  {"in_transit", "delivered", "returned", "cancelled"},
    "in_transit":         {"delivered", "returned", "cancelled"},
    "delivered":          {"returned"},
    "returned":           set(),
    "cancelled":          set(),
}

STATUS_LABELS_DE = {
    # inbound
    "announced": "Angekündigt",
    "ordered": "Bestellt",
    "confirmed": "Bestätigt",
    "in_production": "In Produktion",
    "shipped": "Versandt (Lieferant)",
    "arrived": "Angekommen",
    "partial_received": "Teilweise erhalten",
    "received": "Erhalten",
    "inspected": "Geprüft",
    "stored": "Eingelagert",
    "complaint": "Reklamation",
    # outbound
    "draft": "Entwurf",
    "picking": "Kommissionierung",
    "packed": "Verpackt",
    "ready_for_pickup": "Versandbereit",
    "handed_to_carrier": "An Spediteur übergeben",
    "delivered": "Zugestellt",
    "returned": "Retoure",
    # both
    "in_transit": "In Transit",
    "cancelled": "Storniert",
}

# Status-Farbe für Pills (Streamlit st.markdown CSS)
STATUS_COLORS = {
    "announced": "#9CA3AF", "ordered": "#3B82F6", "confirmed": "#3B82F6",
    "in_production": "#8B5CF6", "shipped": "#F59E0B", "in_transit": "#F59E0B",
    "arrived": "#10B981", "partial_received": "#FBBF24",
    "received": "#10B981", "inspected": "#10B981", "stored": "#059669",
    "complaint": "#EF4444",
    "draft": "#9CA3AF", "picking": "#3B82F6", "packed": "#8B5CF6",
    "ready_for_pickup": "#F59E0B", "handed_to_carrier": "#F59E0B",
    "delivered": "#10B981", "returned": "#EF4444",
    "cancelled": "#6B7280",
}


# ---------- Logistik ----------

SHIPPING_METHODS = ["paket", "stueckgut", "spedition", "kurier", "abholung", "direktlieferung"]
SHIPPING_METHOD_LABELS = {
    "paket": "Paket", "stueckgut": "Stückgut", "spedition": "Spedition",
    "kurier": "Kurier", "abholung": "Abholung", "direktlieferung": "Direktlieferung (Strecke)",
}

PALLET_TYPES = ["none", "euro", "einweg", "gitterbox", "other"]
PALLET_LABELS = {
    "none": "Keine Palette", "euro": "Europalette", "einweg": "Einwegpalette",
    "gitterbox": "Gitterbox", "other": "Sonstige",
}

TERMIN_TYPES = ["fix", "ca", "kw", "asap"]
TERMIN_LABELS = {
    "fix": "Fix-Termin", "ca": "Ca.-Termin",
    "kw": "Kalenderwoche", "asap": "Schnellstmöglich",
}

INCOTERMS_2020 = ["EXW", "FCA", "FAS", "FOB", "CPT", "CIP", "CFR", "CIF", "DAP", "DPU", "DDP"]


# ---------- Lager ----------

LOCATIONS = ["keller", "garage"]
LOCATION_LABELS = {"keller": "Keller", "garage": "Garage"}


# ---------- Anhänge ----------

DOCUMENT_KINDS = [
    "delivery_note", "invoice", "order_confirmation", "customs_declaration",
    "adr_paper", "photo", "damage_photo", "signature", "other",
]
DOCUMENT_KIND_LABELS = {
    "delivery_note": "Lieferschein",
    "invoice": "Rechnung",
    "order_confirmation": "Auftragsbestätigung",
    "customs_declaration": "Zollerklärung",
    "adr_paper": "ADR-Beförderungspapier",
    "photo": "Foto",
    "damage_photo": "Schaden-Foto",
    "signature": "Unterschrift",
    "other": "Sonstiges",
}


# ---------- ADR (Gefahrgut) — gängige Kältemittel ----------

ADR_PRESETS = {
    "R32":   {"un_nr": "UN 3252", "class": "2.1", "proper_name": "Difluormethan (Kältemittel R 32)"},
    "R290":  {"un_nr": "UN 1978", "class": "2.1", "proper_name": "Propan (Kältemittel R 290)"},
    "R744":  {"un_nr": "UN 1013", "class": "2.2", "proper_name": "Kohlendioxid (R 744)"},
    "R717":  {"un_nr": "UN 1005", "class": "2.3", "proper_name": "Ammoniak, wasserfrei (R 717)"},
    "R134a": {"un_nr": "UN 3159", "class": "2.2", "proper_name": "1,1,1,2-Tetrafluorethan (R 134a)"},
    "R407C": {"un_nr": "UN 3340", "class": "2.2", "proper_name": "Kältemittel-Gas, n.a.g. (R 407C)"},
    "R410A": {"un_nr": "UN 3163", "class": "2.2", "proper_name": "Verflüssigtes Gas, n.a.g. (R 410A)"},
    "N2":    {"un_nr": "UN 1066", "class": "2.2", "proper_name": "Stickstoff, verdichtet"},
    "He":    {"un_nr": "UN 1046", "class": "2.2", "proper_name": "Helium, verdichtet"},
}
