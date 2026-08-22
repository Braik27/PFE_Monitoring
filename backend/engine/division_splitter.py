"""
engine/division_splitter.py — Détection et séparation des divisions depuis le CONTENU des fichiers.

Logique métier (basée sur les images et le contexte fourni) :
────────────────────────────────────────────────────────────────
1. On lit la colonne OPERATING_UNIT_CODE (si présente) depuis le DataFrame
2. On lit aussi l'adresse client (INV_ORG_CODE, OPERATING_UNIT_NAME ou champ similaire)
3. On applique les règles de mapping :

   KOWEÏT   → OPERATING_UNIT_CODE contient : KLD, RMK, LEK
              OU texte contient : "KWAIT", "KUWAIT", "KWT"

   QATAR    → OPERATING_UNIT_CODE contient : DOH, QAT, DAW
   (Doha)     OU texte contient : "DOHA", "QATAR", "DAW7A", "DAW"

   KSA      → OPERATING_UNIT_CODE contient : PSC, KSA, RIY, JED, DMM, SAU
   (Arabie)   OU texte contient : "KSA", "SAUDI", "PSC", "RIYADH", "JEDDAH"

   SPG      → OPERATING_UNIT_CODE contient : SPG, SIN, SGP
              OU texte contient : "SPG", "SINGAPORE"

   LUX/FRT  → OPERATING_UNIT_CODE contient : LUX, FRT, LNH
   (Europe)   OU texte contient : "LUX", "LUXEMBOURG", "FRT", "LNH"

4. Si un fichier contient plusieurs divisions → split en sous-DataFrames
5. Chaque sous-DataFrame est analysé séparément → 1 rapport par division

Règle de priorité :
  OPERATING_UNIT_CODE > adresse/nom_client > nom_colonne_combinée
"""
from __future__ import annotations
import re
import logging
from typing import Dict, List, Tuple, Optional
import pandas as pd

log = logging.getLogger(__name__)

# ── Table de correspondance OPERATING_UNIT_CODE → Division ───────────

# Préfixes/codes OPERATING_UNIT_CODE connus (en majuscules)
OU_CODE_MAP: Dict[str, str] = {
    # Koweït
    "KLD": "KWT", "RMK": "KWT", "LEK": "KWT",
    "KWT": "KWT", "KUW": "KWT",
    # Qatar / Doha
    "DOH": "DAW7A", "QAT": "DAW7A", "DAW": "DAW7A",
    "DAW7A": "DAW7A", "DOHA": "DAW7A",
    # KSA / Arabie Saoudite (PSC dans les fichiers Excel)
    "PSC": "KSA", "KSA": "KSA", "SAU": "KSA",
    "RIY": "KSA", "JED": "KSA", "DMM": "KSA",
    "ABA": "KSA",
    # SPG / Singapore
    "SPG": "SPG", "SIN": "SPG", "SGP": "SPG",
    # LUX / Europe
    "LUX": "LUX", "FRT": "LUX", "LNH": "LUX",
    "LH6": "LUX",
}

# Mots-clés texte pour la détection par nom / adresse
TEXT_KEYWORDS: List[Tuple[str, str]] = [
    # Koweït (priorité haute car "kwait" non standard)
    ("KWAIT",     "KWT"),
    ("KUWAIT",    "KWT"),
    ("KWT",       "KWT"),
    # Qatar / Doha
    ("DAW7A",     "DAW7A"),
    ("DAWHA",     "DAW7A"),
    ("DOHA",      "DAW7A"),
    ("QATAR",     "DAW7A"),
    ("DAW",       "DAW7A"),
    # KSA
    ("KSA",       "KSA"),
    ("PSC",       "KSA"),
    ("SAUDI",     "KSA"),
    ("RIYADH",    "KSA"),
    ("JEDDAH",    "KSA"),
    ("DAMMAM",    "KSA"),
    # SPG
    ("SPG",       "SPG"),
    ("SINGAPORE", "SPG"),
    # LUX / Europe
    ("LUX",       "LUX"),
    ("LUXEMBOURG","LUX"),
    ("FRT",       "LUX"),
    ("LNH",       "LUX"),
]

# Colonnes candidates pour la détection (ordre de priorité)
DETECTION_COLUMNS = [
    "OPERATING_UNIT_CODE",
    "INV_ORG_CODE",
    "OPERATING_UNIT_NAME",
    "OU_CODE",
    "DIVISION",
    "COUNTRY",
    "COUNTRY_CODE",
    "STORE",
    "STORE_NAME",
    "CUSTOMER_NAME",
    "BILL_TO_LOCATION",
    "SHIP_TO_LOCATION",
]

DIVISION_LABELS = {
    "KWT":   "Koweït",
    "DAW7A": "Doha (Qatar)",
    "KSA":   "KSA / Arabie Saoudite",
    "SPG":   "Singapore",
    "LUX":   "Luxembourg",
    "GLOBAL":"Toutes divisions",
}


def detect_division_from_value(value: str) -> Optional[str]:
    """
    Détecte la division depuis une valeur de cellule.
    Priorité : code exact → préfixe → mot-clé texte.
    """
    if not value:
        return None
    v = str(value).strip().upper()

    # 1. Code exact
    if v in OU_CODE_MAP:
        return OU_CODE_MAP[v]

    # 2. Préfixe (les 3 premiers caractères)
    prefix = v[:3]
    if prefix in OU_CODE_MAP:
        return OU_CODE_MAP[prefix]

    # 3. Le code contient un mot-clé connu
    for code, div in OU_CODE_MAP.items():
        if code in v:
            return div

    # 4. Recherche par mots-clés texte (pour les adresses/noms)
    for keyword, div in TEXT_KEYWORDS:
        if keyword in v:
            return div

    return None


def detect_division_columns(df: pd.DataFrame) -> List[str]:
    """
    Retourne la liste des colonnes du DataFrame utilisables pour la détection.
    Normalise les noms de colonnes pour la comparaison.
    """
    df_cols_upper = {col.upper().replace(" ","_").replace("-","_"): col
                     for col in df.columns}
    found = []
    for candidate in DETECTION_COLUMNS:
        if candidate in df_cols_upper:
            found.append(df_cols_upper[candidate])
    return found


def detect_row_division(row: pd.Series, det_cols: List[str]) -> str:
    """
    Détecte la division d'une ligne en cherchant dans les colonnes de détection.
    Retourne la division détectée ou "GLOBAL" si inconnue.
    """
    for col in det_cols:
        val = str(row.get(col, "") or "").strip()
        if val:
            div = detect_division_from_value(val)
            if div:
                return div
    return "GLOBAL"


def split_dataframe_by_division(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Divise un DataFrame en sous-DataFrames par division.
    Retourne un dict : {"KWT": df_kwt, "KSA": df_ksa, ...}

    Si une seule division est détectée → dict avec une seule clé.
    Si aucune division détectée → {"GLOBAL": df_complet}
    """
    det_cols = detect_division_columns(df)

    if not det_cols:
        log.info("Aucune colonne de détection trouvée → division GLOBAL")
        return {"GLOBAL": df}

    log.info("Colonnes de détection utilisées : %s", det_cols)

    # Ajoute une colonne temporaire _DIVISION
    df = df.copy()
    df["_DIVISION"] = df.apply(
        lambda row: detect_row_division(row, det_cols), axis=1
    )

    divisions_found = df["_DIVISION"].unique().tolist()
    log.info("Divisions détectées dans le fichier : %s", divisions_found)

    result = {}
    for div in divisions_found:
        subset = df[df["_DIVISION"] == div].drop(columns=["_DIVISION"]).reset_index(drop=True)
        result[div] = subset
        log.info("Division %s : %d lignes", div, len(subset))

    return result


def get_division_summary(df: pd.DataFrame) -> Dict[str, int]:
    """
    Retourne un résumé {division: nb_lignes} sans splitter le DataFrame.
    Utilise des opérations vectorisées pandas pour la détection.
    """
    det_cols = detect_division_columns(df)
    if not det_cols:
        return {"GLOBAL": len(df)}

    if len(df) == 0:
        return {"GLOBAL": 0}

    df_subset = df[det_cols] if det_cols else df.select_dtypes(include=["object"]).iloc[:, :0]
    first_col = det_cols[0]
    vals = df_subset[first_col].fillna("").astype(str).str.strip().str.upper()

    mapped = vals.map(detect_division_from_value)
    counts: Dict[str, int] = mapped.value_counts(dropna=False).to_dict()
    if not counts:
        return {"GLOBAL": len(df)}
    return {k if isinstance(k, str) else "GLOBAL": int(v) for k, v in counts.items()}


def division_label(div: str) -> str:
    """Retourne le nom complet d'une division."""
    return DIVISION_LABELS.get(div, div)


def _extract_division_from_label(label: str) -> str:
    """Extrait la division depuis un label d'analyse (alias pour compatibilité)."""
    if not label:
        return ""
    up = str(label).upper().replace("-", " ").replace("_", " ")
    for keyword, div in TEXT_KEYWORDS:
        if keyword in up:
            return div
    return ""