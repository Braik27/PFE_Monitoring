"""
engine/generic_cleaner.py — Version améliorée v2

Améliorations :
  - Normalisation de date ultra-robuste : supporte TOUS les formats
    Cegid : 2026-02-18T00:00:00  →  2026-02-18
    Oracle: 01-DEC-18, 18-DEC-2026, 01/12/2026  →  2026-12-01
  - Texte insensible à la casse (uppercase systématique avant comparaison)
  - Nombres : gère 1.830,00 / 1,830.00 / 1 830,00
"""
from __future__ import annotations
import re
import logging
from datetime import datetime
from typing import Any

import pandas as pd

from engine.flux_loader import FluxConfig

log = logging.getLogger(__name__)

# ── Mois en plusieurs langues ──────────────────────────────────────
_MONTH_MAP = {
    # Anglais
    "JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06",
    "JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12",
    # Français
    "JANV":"01","FÉVR":"02","FEVR":"02","MARS":"03","AVRI":"04","AVR":"04",
    "JUIN":"06","JUIL":"07","AOUT":"08","AOÛT":"08",
    "SEPT":"09","OCTO":"10","NOVE":"11","DECE":"12",
    # Abréviations 3 lettres FR
    "JAN":"01","FÉV":"02","FEV":"02","MAR":"03","AVR":"04","MAI":"05",
    "JUI":"06","JUL":"07","AOÛ":"08","AOU":"08",
    "SEP":"09","OCT":"10","NOV":"11","DÉC":"12","DEC":"12",
}

# ── Formats strptime à essayer dans l'ordre ────────────────────────
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",   # 2026-02-17T00:00:00  (Cegid ISO)
    "%Y-%m-%dT%H:%M",      # 2026-02-17T00:00
    "%Y-%m-%d %H:%M:%S",   # 2026-02-17 00:00:00
    "%Y-%m-%d",            # 2026-02-17
    "%d/%m/%Y",            # 17/02/2026
    "%d/%m/%y",            # 17/02/26
    "%m/%d/%Y",            # 02/17/2026
    "%d-%m-%Y",            # 17-02-2026
    "%d-%m-%y",            # 17-02-26
    "%d.%m.%Y",            # 17.02.2026
    "%d.%m.%y",            # 17.02.26
    "%Y%m%d",              # 20260217
    "%d %b %Y",            # 17 Feb 2026
    "%d %B %Y",            # 17 February 2026
    "%d-%b-%Y",            # 17-Feb-2026  (Oracle complet)
    "%d-%b-%y",            # 17-Feb-26    (Oracle court)  ← FORMAT ORACLE
    "%b %d, %Y",           # Feb 17, 2026
    "%d %b, %Y",           # 17 Feb, 2026
]


class GenericCleaner:

    def __init__(self, config: FluxConfig):
        self.config = config

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for col_cfg in self.config.columns:
            col = col_cfg.name
            if col not in df.columns:
                continue
            rule = col_cfg.normalize
            try:
                df[col] = df[col].apply(lambda v: self._apply(v, rule))
            except Exception as e:
                log.warning("Erreur normalisation '%s' (règle '%s'): %s", col, rule, e)
        return df

    def _apply(self, value: Any, rule: str) -> str:
        v = str(value).strip() if value is not None else ""
        if v.upper() in ("NAN", "NONE", "NULL", "NAT", ""):
            return ""
        dispatch = {
            "strip_zeros":  self._norm_strip_zeros,
            "uppercase":    self._norm_uppercase,
            "parse_number": self._norm_parse_number,
            "iso_date":     self._norm_iso_date,
            "none":         lambda x: x,
        }
        return dispatch.get(rule, lambda x: x)(v)

    # ── RÈGLES ────────────────────────────────────────────────────

    @staticmethod
    def _norm_strip_zeros(v: str) -> str:
        """
        Normalise un identifiant numérique.

        Cas traités :
          '0003500000174'  → '3500000174'      (zéros initiaux)
          '2.63E+12'       → '2630000000000'   (notation scientifique point)
          '2,63E+12'       → '2630000000000'   (notation scientifique virgule)
          '2630000000535'  → '2630000000535'   (déjà correct — aucun changement)

        ATTENTION : La notation scientifique 2.63E+12 perd les derniers chiffres
        (535 → 000).  Ce n'est PAS une erreur du code mais une limitation du
        format CSV/Excel.  Le comparateur détecte ce cas séparément et génère
        un WARNING « valeur Oracle tronquée par la source ».
        """
        v = v.strip().upper().replace(" ", "")
        if not v or v in ("", "NAN", "NONE", "NULL"):
            return ""

        # Notation scientifique : 2.63E+12  ou  2,63E+12  ou  2.630000000535E+12
        sci = re.match(r"^(-?[0-9]+)[.,]([0-9]+)[Ee]([+\-]?[0-9]+)$", v)
        if sci:
            try:
                # Reconstruit le nombre entier avec toute la précision disponible
                integer_part = sci.group(1)
                decimal_part = sci.group(2)
                exponent     = int(sci.group(3))
                # Nombre entier = integer_part + decimal_part, décalé de l'exposant
                full_digits  = integer_part.lstrip("-") + decimal_part
                total_digits = len(integer_part.lstrip("-")) + exponent
                if total_digits >= len(full_digits):
                    # Complète avec des zéros si nécessaire
                    result = full_digits.ljust(total_digits, "0")
                else:
                    result = full_digits[:total_digits]
                if v.startswith("-"):
                    result = "-" + result
                return result.lstrip("0") or "0"
            except Exception:
                # Fallback float
                try:
                    return str(int(float(v.replace(",", "."))))
                except Exception:
                    pass

        # Numérique pur → supprime zéros initiaux
        if re.match(r"^\d+$", v):
            return v.lstrip("0") or "0"

        return v

    @staticmethod
    def _norm_uppercase(v: str) -> str:
        return v.strip().upper()

    @staticmethod
    def _norm_parse_number(v: str) -> str:
        if not v or v.upper() in ("NAN", "NONE", "NULL", ""):
            return "0.0"
        v = re.sub(r"[\s\u00a0€$£]", "", v)
        if v.count(",") == 1 and v.count(".") == 0:
            v = v.replace(",", ".")
        elif v.count(",") >= 1 and v.count(".") >= 1:
            if v.rindex(",") > v.rindex("."):
                v = v.replace(".", "").replace(",", ".")
            else:
                v = v.replace(",", "")
        elif v.count(",") > 1:
            v = v.replace(",", "")
        try:
            return str(round(float(v), 6))
        except ValueError:
            return "0.0"

    @classmethod
    def _norm_iso_date(cls, v: str) -> str:
        """
        Normalise n'importe quel format de date vers YYYY-MM-DD.

        Exemples :
          Cegid  : '2026-02-18T00:00:00'  →  '2026-02-18'
          Oracle : '01-DEC-18'            →  '2018-12-01'  (ou 2018 selon contexte)
                   '18-DEC-2026'          →  '2026-12-18'
                   '01/12/2026'           →  '2026-12-01'
        """
        if not v:
            return ""
        v_clean = v.strip()

        # Déjà ISO YYYY-MM-DD
        if re.match(r"^\d{4}-\d{2}-\d{2}$", v_clean):
            return v_clean
        # ISO avec heure : 2026-02-17T... ou 2026-02-17 ...
        m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ]", v_clean)
        if m:
            return m.group(1)

        # FORMAT ORACLE : JJ-MON-AA ou JJ-MON-AAAA (ex: 01-DEC-18, 18-DEC-2026)
        m = re.match(r"^(\d{1,2})[\/\-\.]([A-Za-zÀ-ÿ]{3,4})[\/\-\.](\d{2,4})$", v_clean)
        if m:
            day, mon_str, year = m.group(1), m.group(2).upper()[:4], m.group(3)
            mon_num = _MONTH_MAP.get(mon_str)
            if mon_num:
                # Année sur 2 chiffres → 4 chiffres
                if len(year) == 2:
                    yr = int(year)
                    year = str(2000 + yr) if yr <= 49 else str(1900 + yr)
                return f"{year}-{mon_num}-{day.zfill(2)}"

        # Essaie tous les formats strptime connus
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(v_clean, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue

        # Dernier recours : pandas
        try:
            return pd.to_datetime(v_clean, dayfirst=True).strftime("%Y-%m-%d")
        except Exception:
            pass

        log.debug("Date non reconnue : '%s'", v)
        return v_clean


def clean_pair(config, df_cegid, df_oracle):
    c = GenericCleaner(config)
    return c.clean(df_cegid), c.clean(df_oracle)