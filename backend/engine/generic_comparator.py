"""
engine/generic_comparator.py — Version v4 (Azure-optimized)

Optimisation mémoire critique pour Azure App Service B1 (1.75 GB RAM) :
  - AVANT : iterrows() → crée un objet pandas.Series par ligne → ~4KB/ligne
            63998 + 31999 lignes = ~384MB rien que pour l'itération → OOM
  - APRÈS : itertuples() → named tuples légers → ~0.4KB/ligne → ~38MB total
            + _build_index stocke des dicts simples au lieu de Series

Règle métier :
  VIDE (Cegid) == 0 / "0" / "0.0" / "0,0" (Oracle) → PAS D'ERREUR
"""
from __future__ import annotations
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import pandas as pd

from engine.flux_loader import FluxConfig, ComparisonRule

log = logging.getLogger(__name__)

# Valeurs considérées comme "vide / rien"
_EMPTY_VALUES = {"", "nan", "none", "null", "na", "n/a", "-", "--", "0", "0.0",
                 "0,0", "0.00", "0,00", ".0", ",0"}


def _is_empty(v: str) -> bool:
    if not v:
        return True
    return v.strip().lower() in _EMPTY_VALUES


def _norm_val(v: str) -> str:
    return str(v).strip().upper()


def _normalize_text(v: str) -> str:
    if not v or str(v).strip().lower() in _EMPTY_VALUES:
        return ""
    v = str(v).strip().upper()
    v = re.sub(r"[^A-Z0-9\s]", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


@dataclass
class Anomaly:
    error_type:  str
    severity:    str
    key_values:  Dict[str, str] = field(default_factory=dict)
    column:      Optional[str]  = None
    val_cegid:   Optional[str]  = None
    val_oracle:  Optional[str]  = None
    delta:       Optional[float] = None
    line_cegid:  Optional[int]  = None
    line_oracle: Optional[int]  = None
    explication: str = ""
    action:      str = ""

    def to_dict(self) -> dict:
        return {
            "error_type":  self.error_type,
            "severity":    self.severity,
            "key_values":  self.key_values,
            "key_str":     " | ".join(f"{k}={v}" for k, v in self.key_values.items()),
            "column":      self.column,
            "val_cegid":   self.val_cegid,
            "val_oracle":  self.val_oracle,
            "delta":       self.delta,
            "line_cegid":  self.line_cegid,
            "line_oracle": self.line_oracle,
            "explication": self.explication,
            "action":      self.action,
        }


@dataclass
class PairResult:
    flux_id:      str
    label:        str
    file_cegid:   str
    file_oracle:  str
    n_cegid:      int = 0
    n_oracle:     int = 0
    n_col_cegid:  int = 0
    n_col_oracle: int = 0
    n_matched:    int = 0
    n_integrated: int = 0   # Nb lignes Integrated (CBLC1I)
    n_rejected:   int = 0   # Nb lignes Rejected   (OPEC1R)
    anomalies:    List[Anomaly] = field(default_factory=list)

    @property
    def n_critiques(self) -> int:
        return sum(1 for a in self.anomalies if a.severity == "CRITIQUE")

    @property
    def n_warnings(self) -> int:
        return sum(1 for a in self.anomalies if a.severity == "WARNING")

    @property
    def n_missing_oracle(self) -> int:
        return sum(1 for a in self.anomalies if a.error_type == "MANQUANT_ORACLE")

    @property
    def n_missing_cegid(self) -> int:
        return sum(1 for a in self.anomalies if a.error_type == "MANQUANT_CEGID")

    @property
    def concordance(self) -> float:
        total = max(self.n_cegid, self.n_oracle)
        if total == 0:
            return 100.0
        bad = self.n_missing_oracle + self.n_missing_cegid + self.n_critiques + self.n_warnings
        return round(max(0.0, (total - bad) / total * 100), 1)

    @property
    def top_error_columns(self) -> List[Dict]:
        counts: Dict[str, int] = defaultdict(int)
        for a in self.anomalies:
            if a.column:
                counts[a.column] += 1
        return [
            {"column": col, "n_errors": n}
            for col, n in sorted(counts.items(), key=lambda x: -x[1])
        ]

    def to_dict(self) -> dict:
        return {
            "flux_id":           self.flux_id,
            "label":             self.label,
            "file_cegid":        self.file_cegid,
            "file_oracle":       self.file_oracle,
            "n_cegid":           self.n_cegid,
            "n_oracle":          self.n_oracle,
            "n_col_cegid":       self.n_col_cegid,
            "n_col_oracle":      self.n_col_oracle,
            "n_matched":         self.n_matched,
            "n_critiques":       self.n_critiques,
            "n_warnings":        self.n_warnings,
            "n_missing_oracle":  self.n_missing_oracle,
            "n_missing_cegid":   self.n_missing_cegid,
            "concordance":       self.concordance,
            "top_error_columns": self.top_error_columns,
            "anomalies":         [a.to_dict() for a in self.anomalies],
            "n_integrated":      self.n_integrated,
            "n_rejected":        self.n_rejected,
        }


class GenericComparator:

    def __init__(self, config: FluxConfig):
        self.config = config

    def compare(self, df_cegid: pd.DataFrame, df_oracle: pd.DataFrame,
                label: str = "", file_cegid: str = "cegid",
                file_oracle: str = "oracle") -> PairResult:

        result = PairResult(
            flux_id=self.config.flux_id, label=label,
            file_cegid=file_cegid, file_oracle=file_oracle,
            n_cegid=len(df_cegid), n_oracle=len(df_oracle),
            n_col_cegid=len(df_cegid.columns),
            n_col_oracle=len(df_oracle.columns),
        )

        # ── Comptage Integrated / Rejected (flux CustomerBalance) ──────────
        # Utilise des opérations vectorisées pandas → aucune boucle → rapide
        try:
            prefix_col = next(
                (c for c in df_cegid.columns if c.strip().upper() == "PREFIR"),
                None
            )
            if prefix_col and not df_cegid.empty:
                upper = df_cegid[prefix_col].astype(str).str.strip().str.upper()
                result.n_integrated = int(upper.str.endswith("I").sum())
                result.n_rejected   = int(upper.str.endswith("R").sum())
        except Exception as exc:
            log.warning("Comptage Integrated/Rejected ignoré: %s", exc)

        # ── Construction des index (dicts Python, pas de Series) ───────────
        # _build_index retourne Dict[key, List[dict]] au lieu de List[Series]
        # → 10x moins de mémoire sur Azure B1
        cegid_index  = self._build_index(df_cegid)
        oracle_index = self._build_index(df_oracle)

        # ── Parcourt les lignes Cegid avec itertuples (pas iterrows) ───────
        # itertuples() retourne des namedtuples légers, pas des Series pandas
        col_names = list(df_cegid.columns)
        for tup in df_cegid.itertuples(index=False, name=None):
            row_c    = dict(zip(col_names, tup))
            key      = self._make_key_from_dict(row_c)
            key_dict = {k: str(row_c.get(k, "")) for k in self.config.key_columns}

            oracle_rows = oracle_index.get(key)
            if not oracle_rows:
                result.anomalies.append(Anomaly(
                    error_type="MANQUANT_ORACLE",
                    severity="CRITIQUE",
                    key_values=key_dict,
                    val_cegid=self._amount_from_dict(row_c),
                    line_cegid=self._line_from_dict(row_c),
                    explication=(
                        f"Ligne présente dans Cegid, introuvable dans Oracle. "
                        f"Clé : {key}"
                    ),
                    action="Vérifier que la transaction a été transmise à Oracle.",
                ))
                continue

            row_o = oracle_rows.pop(0)
            if not oracle_rows:
                del oracle_index[key]
            result.n_matched += 1

            for rule in self.config.comparison_rules:
                anom = self._compare_column_dicts(rule, row_c, row_o, key_dict)
                if anom:
                    result.anomalies.append(anom)

        # ── Lignes Oracle sans correspondance Cegid ────────────────────────
        oracle_col_names = list(df_oracle.columns)
        for rows in oracle_index.values():
            for row_o in rows:
                key_dict_o = {k: str(row_o.get(k, "")) for k in self.config.key_columns}
                key_o      = "||".join(
                    str(row_o.get(k, "")).strip().upper()
                    for k in self.config.key_columns
                )
                result.anomalies.append(Anomaly(
                    error_type="MANQUANT_CEGID",
                    severity="CRITIQUE",
                    key_values=key_dict_o,
                    val_oracle=self._amount_from_dict(row_o),
                    line_oracle=self._line_from_dict(row_o),
                    explication=(
                        f"Ligne présente dans Oracle, introuvable dans Cegid. "
                        f"Clé : {key_o}"
                    ),
                    action="Vérifier si la transaction existe dans Cegid.",
                ))

        log.info(
            "[%s] %d lignes Cegid, %d Oracle, %d matchées, "
            "%d anomalies (%d crit, %d warn)",
            self.config.flux_id, result.n_cegid, result.n_oracle,
            result.n_matched, len(result.anomalies),
            result.n_critiques, result.n_warnings,
        )
        return result

    # ── Index : stocke des dicts simples (pas des Series pandas) ──────────

    def _build_index(self, df: pd.DataFrame) -> Dict[str, list]:
        """
        Construit un index clé → [dict, dict, ...].
        Utilise itertuples() au lieu d'iterrows() → ~10x moins de mémoire.
        """
        idx: Dict[str, list] = defaultdict(list)
        col_names = list(df.columns)
        for tup in df.itertuples(index=False, name=None):
            row = dict(zip(col_names, tup))
            key = self._make_key_from_dict(row)
            idx[key].append(row)
        return dict(idx)

    def _make_key_from_dict(self, row: dict) -> str:
        return "||".join(
            str(row.get(k, "")).strip().upper()
            for k in self.config.key_columns
        )

    def _amount_from_dict(self, row: dict) -> Optional[str]:
        col = self.config.display.amount_column
        return str(row[col]) if col and col in row else None

    @staticmethod
    def _line_from_dict(row: dict) -> Optional[int]:
        v = row.get("__LINE__", 0)
        try:
            return int(v) if v else None
        except (ValueError, TypeError):
            return None

    # ── Comparaison de colonnes ────────────────────────────────────────────

    def _compare_column_dicts(self, rule: ComparisonRule,
                               row_c: dict, row_o: dict,
                               key_dict: dict) -> Optional[Anomaly]:
        col   = rule.column
        raw_c = str(row_c.get(col, "")).strip()
        raw_o = str(row_o.get(col, "")).strip()

        if _is_empty(raw_c) and _is_empty(raw_o):
            return None

        col_cfg    = self.config.get_column(col)
        is_numeric = col_cfg and col_cfg.type == "decimal"
        is_date    = col_cfg and col_cfg.type == "date"

        if is_numeric:
            return self._compare_numeric_dicts(rule, raw_c, raw_o,
                                                row_c, row_o, key_dict)

        if not raw_c or not raw_o:
            if _is_empty(raw_c) and _is_empty(raw_o):
                return None
            return Anomaly(
                error_type="VALEUR_NULLE",
                severity=rule.severity,
                key_values=key_dict, column=col,
                val_cegid=raw_c or "(vide)",
                val_oracle=raw_o or "(vide)",
                line_cegid=self._line_from_dict(row_c),
                line_oracle=self._line_from_dict(row_o),
                explication=(
                    f"Colonne '{col}' : "
                    f"{'Cegid vide' if not raw_c else 'Oracle vide'}. "
                    f"Cegid='{raw_c or '(vide)'}' | Oracle='{raw_o or '(vide)'}'"
                ),
                action=f"Vérifier la colonne '{col}' dans les deux systèmes.",
            )

        if _normalize_text(raw_c) != _normalize_text(raw_o):
            if self._is_truncated(raw_c, raw_o):
                return Anomaly(
                    error_type=f"ECART_{col}", severity="WARNING",
                    key_values=key_dict, column=col,
                    val_cegid=raw_c, val_oracle=raw_o,
                    line_cegid=self._line_from_dict(row_c),
                    line_oracle=self._line_from_dict(row_o),
                    explication=(
                        f"⚠️ HEADER_ID Oracle tronqué — "
                        f"Oracle a exporté '{raw_o}' (les derniers chiffres sont perdus). "
                        f"La valeur complète Cegid est '{raw_c}'. "
                        f"Cause : Oracle exporte ce champ en notation scientifique "
                        f"(ex: 2.63E+12) dans le CSV, ce qui perd les derniers chiffres."
                    ),
                    action=(
                        "Demander à l'équipe Oracle d'exporter HEADER_ID en format TEXTE "
                        "(dans SQL*Plus : TO_CHAR(HEADER_ID), ou dans Excel : "
                        "formater la colonne en 'Texte' avant d'ouvrir le CSV)."
                    ),
                )
            return Anomaly(
                error_type=f"ECART_{col}", severity=rule.severity,
                key_values=key_dict, column=col,
                val_cegid=raw_c, val_oracle=raw_o,
                line_cegid=self._line_from_dict(row_c),
                line_oracle=self._line_from_dict(row_o),
                explication=f"Colonne '{col}' différente : Cegid='{raw_c}' | Oracle='{raw_o}'",
                action=f"Vérifier '{col}' dans les deux systèmes.",
            )
        return None

    def _compare_numeric_dicts(self, rule: ComparisonRule,
                                raw_c: str, raw_o: str,
                                row_c: dict, row_o: dict,
                                key_dict: dict) -> Optional[Anomaly]:
        try:
            n_c = self._to_float(raw_c)
            n_o = self._to_float(raw_o)
        except ValueError:
            return None

        if abs(n_c - n_o) <= rule.tolerance:
            return None

        return Anomaly(
            error_type=f"ECART_{rule.column}", severity=rule.severity,
            key_values=key_dict, column=rule.column,
            val_cegid=raw_c or "(vide)", val_oracle=raw_o or "(vide)",
            delta=round(n_c - n_o, 4),
            line_cegid=self._line_from_dict(row_c),
            line_oracle=self._line_from_dict(row_o),
            explication=(
                f"Écart sur '{rule.column}' : "
                f"Cegid={n_c:,.4f} | Oracle={n_o:,.4f} | Δ={n_c-n_o:+,.4f}"
            ),
            action=f"Vérifier la valeur de '{rule.column}'.",
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _to_float(v: str) -> float:
        if not v or v.strip().lower() in _EMPTY_VALUES:
            return 0.0
        v2 = v.strip().replace(" ", "").replace("\u00a0", "")
        if "," in v2 and "." not in v2:
            v2 = v2.replace(",", ".")
        elif "," in v2 and "." in v2:
            if v2.rindex(",") > v2.rindex("."):
                v2 = v2.replace(".", "").replace(",", ".")
            else:
                v2 = v2.replace(",", "")
        return float(v2)

    @staticmethod
    def _is_truncated(full: str, trunc: str) -> bool:
        if not (full.isdigit() and trunc.isdigit()):
            return False
        if len(full) < 10 or len(trunc) < 10:
            return False
        if not trunc.endswith("000"):
            return False
        return full[:10] == trunc[:10]

    # ── Méthodes legacy (gardées pour compatibilité avec le reste du code) ─

    def _make_key(self, row) -> str:
        """Compatibilité — utilise _make_key_from_dict si possible."""
        if isinstance(row, dict):
            return self._make_key_from_dict(row)
        return "||".join(
            str(row.get(k, "")).strip().upper()
            for k in self.config.key_columns
        )

    def _key_dict(self, row) -> Dict[str, str]:
        return {k: str(row.get(k, "")) for k in self.config.key_columns}

    def _amount_str(self, row) -> Optional[str]:
        col = self.config.display.amount_column
        return str(row[col]) if col and col in row else None

    @staticmethod
    def _line(row) -> Optional[int]:
        v = row.get("__LINE__", 0)
        try:
            return int(v) if v else None
        except (ValueError, TypeError):
            return None

    def _build_index_legacy(self, df: pd.DataFrame) -> Dict[str, list]:
        """Index legacy avec iterrows — conservé pour référence uniquement."""
        idx: Dict[str, list] = defaultdict(list)
        for _, row in df.iterrows():
            idx[self._make_key(row)].append(row)
        return dict(idx)