"""
engine/generic_reader.py — Version v3 ROBUSTE

Stratégie :
1. Lit TOUT le fichier d'abord (toutes les colonnes)
2. Détecte automatiquement l'en-tête (ligne avec le plus de vrais noms)
3. Mappe les colonnes du flux sur les colonnes réelles du fichier
4. Si une colonne obligatoire manque → erreur claire avec les vraies colonnes
5. Si une colonne optionnelle manque → colonne vide (pas d'erreur)
6. N_CEGID / N_ORACLE = vraie taille du fichier (jamais 0 sauf fichier vide)
"""
from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import Tuple, List, Optional

import pandas as pd

from engine.flux_loader import FluxConfig

log = logging.getLogger(__name__)


class GenericReader:

    def __init__(self, config: FluxConfig):
        self.config = config

    def read(self, filepath: str, source: str = "?") -> Tuple[pd.DataFrame, dict]:
        path = Path(filepath)
        log.info("[%s] Lecture %s — flux: %s", source, path.name, self.config.flux_id)

        # ── 1. Lecture brute SANS filtrage de colonnes ─────────────
        raw, bad_lines = self._read_raw(path)
        if raw.empty:
            raise ValueError(f"[{source}] Le fichier '{path.name}' est vide.")

        # ── 2. Détection de la ligne d'en-tête ─────────────────────
        header_idx = self._find_header(raw)

        # Applique l'en-tête trouvé
        if header_idx > 0:
            raw.columns = raw.iloc[header_idx]
            raw = raw.iloc[header_idx + 1:].reset_index(drop=True)
        elif header_idx == 0:
            raw.columns = raw.iloc[0]
            raw = raw.iloc[1:].reset_index(drop=True)

        # ── 3. Normalise les noms de colonnes ──────────────────────
        raw.columns = [self._norm(str(c)) for c in raw.columns]
        raw = raw.loc[:, ~raw.columns.duplicated()]

        # Supprime lignes entièrement vides
        raw = raw.dropna(how="all").reset_index(drop=True)

        log.info("[%s] Fichier lu : %d lignes, colonnes disponibles : %s",
                 source, len(raw), list(raw.columns))

        # ── 4. Extraction des colonnes du flux ─────────────────────
        df = self._extract_columns(raw, source, path.name)

        # ── 5. Numéro de ligne réel ────────────────────────────────
        # +2 : ligne 1 = en-tête, lignes numérotées à partir de 2
        df["__LINE__"]    = range(header_idx + 2, header_idx + 2 + len(df))
        df["__SOURCE__"]  = source
        df["__RAW_COLS__"] = str(list(raw.columns))  # pour debug

        log.info("[%s] %d lignes extraites pour le flux %s",
                 source, len(df), self.config.flux_id)

        bad_info = {
            "count": len(bad_lines),
            "samples": bad_lines[:5],
            "source": source,
            "filename": path.name,
        }
        return df, bad_info

    # ─────────────────────────────────────────────────────────────────
    # Lecture brute
    # ─────────────────────────────────────────────────────────────────

    def _read_raw(self, path: Path) -> Tuple[pd.DataFrame, list]:
        """Lit TOUT le fichier, toutes colonnes, tout en string."""
        suffix = path.suffix.lower()
        try:
            if suffix in (".xlsx", ".xls"):
                df = pd.read_excel(path, header=None, dtype=str)
                # Remplace les NaN par chaîne vide
                return df.fillna(""), []
            # CSV : teste plusieurs encodages et séparateurs
            for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
                for sep in (",", ";", "\t", "|"):
                    bad_lines = []
                    def _handle_bad(line):
                        bad_lines.append(line)
                        return None
                    try:
                        df = pd.read_csv(
                            path, header=None, dtype=str,
                            sep=sep, encoding=enc, engine='python',
                            on_bad_lines=_handle_bad,
                            keep_default_na=False
                        )
                        if len(df.columns) > 1:
                            return df.fillna(""), bad_lines
                    except Exception:
                        continue
            # Dernier recours : 1 seule colonne (fichier mal formaté)
            return pd.read_csv(
                path, header=None, dtype=str, encoding="latin-1",
                keep_default_na=False
            ).fillna(""), []
        except Exception as e:
            raise IOError(f"Impossible de lire '{path.name}': {e}")

    # ─────────────────────────────────────────────────────────────────
    # Détection de l'en-tête
    # ─────────────────────────────────────────────────────────────────

    def _find_header(self, df: pd.DataFrame) -> int:
        """
        Cherche la ligne qui ressemble le plus à un en-tête.
        Stratégie :
          - Score = nombre de cellules non-numériques + non-vides dans la ligne
          - Priorité aux 10 premières lignes
          - Si une ligne contient au moins 1 colonne du flux → bonus
        """
        target = {self._norm(c.name) for c in self.config.columns}
        best_idx, best_score = 0, -1

        for i in range(min(20, len(df))):
            row = df.iloc[i]
            vals = [str(v).strip() for v in row.values if str(v).strip()]
            if not vals:
                continue

            # Score de base : nombre de valeurs texte non numériques
            text_score = sum(
                1 for v in vals
                if v and not re.match(r"^-?[\d\s.,]+$", v)
            )

            # Bonus si des colonnes du flux sont présentes
            row_normed = {self._norm(v) for v in vals}
            flux_match = len(target & row_normed)
            total_score = text_score + flux_match * 10

            if total_score > best_score:
                best_score = total_score
                best_idx = i

            # Arrêt rapide si score parfait
            if flux_match >= len(target) // 2 + 1:
                return i

        return best_idx

    # ─────────────────────────────────────────────────────────────────
    # Extraction des colonnes
    # ─────────────────────────────────────────────────────────────────

    def _extract_columns(self, df: pd.DataFrame, source: str, filename: str) -> pd.DataFrame:
        """
        Mappe les colonnes du flux sur les colonnes réelles du fichier.
        Insensible à la casse, aux espaces, aux underscores.
        """
        result   = pd.DataFrame(index=df.index)
        available = list(df.columns)
        missing_required = []

        for col_cfg in self.config.columns:
            target = self._norm(col_cfg.name)
            match  = self._find_column(available, target)

            if match:
                result[col_cfg.name] = (
                    df[match].fillna("").astype(str).str.strip()
                )
                log.debug("[%s] Colonne '%s' → '%s'", source, col_cfg.name, match)
            elif col_cfg.required:
                missing_required.append(col_cfg.name)
            else:
                # Colonne optionnelle absente → colonne vide
                result[col_cfg.name] = ""
                log.debug("[%s] Colonne optionnelle '%s' absente → vide", source, col_cfg.name)

        if missing_required:
            # Reformate les colonnes disponibles pour affichage clair
            cols_display = [c for c in available if c and c != "COL"]
            raise ValueError(
                f"[{source}] Colonne(s) obligatoire(s) introuvable(s) "
                f"dans '{filename}': {missing_required}\n"
                f"Colonnes disponibles: {cols_display}"
            )

        return result

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _norm(name: str) -> str:
        """Normalise un nom : UPPERCASE, espaces/tirets/points → _, caractères spéciaux supprimés."""
        s = str(name).strip().upper()
        s = re.sub(r"[\s\-\./\\]+", "_", s)
        s = re.sub(r"[^\w]", "", s)
        return s or "COL"

    def _find_column(self, columns: List[str], target: str) -> Optional[str]:
        """
        Cherche une colonne avec flexibilité maximale :
          1. Correspondance exacte après normalisation
          2. Correspondance après suppression de tous les underscores/espaces
          3. Correspondance partielle (l'un contient l'autre)
        """
        tc = re.sub(r"[_\s]", "", target.upper())

        # 1. Exact (normalisé)
        for col in columns:
            if self._norm(col) == target:
                return col

        # 2. Sans underscores/espaces
        for col in columns:
            if re.sub(r"[_\s]", "", str(col).upper()) == tc:
                return col

        # 3. Partiel (prudent : seulement si le target est suffisamment long)
        if len(tc) >= 4:
            for col in columns:
                cc = re.sub(r"[_\s]", "", str(col).upper())
                if tc in cc or cc in tc:
                    return col

        return None


def read_pair(
    config: FluxConfig,
    cegid_path: str,
    oracle_path: str,
) -> Tuple[Tuple[pd.DataFrame, dict], Tuple[pd.DataFrame, dict]]:
    reader = GenericReader(config)
    return (
        reader.read(cegid_path,  source="CEGID"),
        reader.read(oracle_path, source="ORACLE"),
    )