"""Konstanten Mahnwesen."""

DUNNING_LEVELS = {
    0: "—",
    1: "Erinnerung",
    2: "1. Mahnung",
    3: "2. Mahnung",
}

DUNNING_LABELS_DE = {
    1: "Zahlungserinnerung",
    2: "1. Mahnung",
    3: "2. Mahnung (Letzte)",
}

# Default-Mahngebühren in Cent (überschrieben durch company_settings)
DEFAULT_FEES_CENTS = {1: 0, 2: 500, 3: 1500}

# Aging-Buckets (Tage seit Fälligkeit)
AGING_BUCKETS = [
    ("Aktuell", 0, 0),       # nicht überfällig
    ("1-30 Tage", 1, 30),
    ("31-60 Tage", 31, 60),
    ("61-90 Tage", 61, 90),
    (">90 Tage", 91, 99999),
]

# Default-Verzugszinssatz pro Jahr (BGB §288 = Basiszinssatz +5/+9 %)
# Vereinfacht: 9% p.a. bei B2B (Basiszinssatz aktuell ~3.62%, +9 = 12.62%; konservativ 9 %)
DEFAULT_INTEREST_RATE_PCT = 9.0
