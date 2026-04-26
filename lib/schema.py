from typing import Literal, Optional, List
from pydantic import BaseModel, Field, field_validator

Kategorie = Literal["heizelemente", "regler", "schalter", "sensoren", "sonderloesungen"]
Branche = Literal["automotive", "hvac", "industrie", "labor", "lebensmittel", "maschinenbau"]
Verfuegbarkeit = Literal["auf_anfrage", "kurzfristig", "lager", "oem"]
Herkunftsregion = Literal["eu", "global"]
SpecGroup = Literal[
    "abmessungen", "bedienung", "elektrisch", "funktion", "geografie",
    "kommerziell", "kommunikation", "konfiguration", "konstruktion",
    "prozess", "qualitaet", "thermisch", "umgebung",
]


class Spec(BaseModel):
    label: str
    value: str
    label_en: str
    value_en: str
    group: SpecGroup


class Komponente(BaseModel):
    titel: str
    titel_en: str
    kategorie: Kategorie
    kurzbeschreibung: str
    kurzbeschreibung_en: str
    beschreibung: str
    beschreibung_en: str
    herkunftsregion: Herkunftsregion
    specs: List[Spec]
    tags: List[str]
    anwendungen: List[str]
    anwendungen_en: List[str]
    branchen: List[Branche]
    lieferzeit: str
    lieferzeit_en: str
    verfuegbarkeit: Verfuegbarkeit
    sortPriority: int = 50
    publishedAt: str
    updatedAt: str

    hersteller: Optional[str] = None
    herstellerSichtbar: bool = False
    artikelnummer: Optional[str] = None
    temperaturbereich: Optional[str] = None
    oemOnly: Optional[bool] = None
    mindestmenge: Optional[int] = None
    featured: Optional[bool] = None

    @field_validator("anwendungen", "anwendungen_en")
    @classmethod
    def at_least_two_anwendungen(cls, v: List[str]) -> List[str]:
        if len(v) < 2:
            raise ValueError("anwendungen müssen mindestens 2 Einträge haben")
        return v

    @field_validator("specs")
    @classmethod
    def at_least_three_specs(cls, v: List[Spec]) -> List[Spec]:
        if len(v) < 3:
            raise ValueError("specs müssen mindestens 3 Einträge haben")
        return v
