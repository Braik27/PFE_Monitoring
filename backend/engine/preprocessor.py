"""
engine/preprocessor.py
Pré-traitement des DataFrames avant comparaison.
Applique les règles définies dans le registry (section "pre_processing").

Règles supportées :
  filter_column      : nom de la colonne de statut (ex: "PrefiR", "PREFIRE")
  filter_keep        : "endswith_I" | "endswith_R" | "equals:<valeur>"
  drop_after_filter  : bool — supprimer la colonne après filtrage (défaut: true)
  deduplicate_on     : nom de la colonne clé pour dédoublonner
  deduplicate_keep   : "first" | "last" (défaut: "first")
"""
import logging
import pandas as pd

log = logging.getLogger(__name__)


def apply_preprocessing(
    df: pd.DataFrame,
    rules: dict,
    side: str,
    flux_id: str,
) -> pd.DataFrame:
    """
    Applique les règles de pré-traitement sur un DataFrame.

    Args:
        df       : DataFrame Cegid ou Oracle brut
        rules    : dict issu de config.pre_processing["cegid"] ou ["oracle"]
        side     : "cegid" | "oracle"  (pour les logs uniquement)
        flux_id  : identifiant du flux (pour les logs)

    Returns:
        DataFrame nettoyé, prêt pour la comparaison
    """
    if not rules:
        return df

    df = df.copy()
    n_initial = len(df)

    # ── 1. Filtre sur colonne de statut (PrefiR / PREFIRE) ────────────────
    filter_col  = rules.get("filter_column")
    filter_keep = rules.get("filter_keep")

    if filter_col and filter_keep:
        # Recherche insensible à la casse
        col_match = next(
            (c for c in df.columns if c.upper() == filter_col.upper()), None
        )

        if col_match:
            series = df[col_match].astype(str)

            if filter_keep == "endswith_I":
                mask = series.str.endswith("I", na=False)
            elif filter_keep == "endswith_R":
                mask = series.str.endswith("R", na=False)
            elif filter_keep.startswith("equals:"):
                val  = filter_keep.split(":", 1)[1]
                mask = series == val
            else:
                log.warning(
                    "[PREPROC] flux=%s side=%s — filter_keep='%s' inconnu, filtre ignoré",
                    flux_id, side, filter_keep,
                )
                mask = pd.Series([True] * len(df), index=df.index)

            n_rejected = int((~mask).sum())
            n_kept     = int(mask.sum())
            df = df[mask].copy()

            log.info(
                "[PREPROC] flux=%s side=%s | colonne '%s' : %d conservées / %d rejetées (sur %d totales)",
                flux_id, side, col_match, n_kept, n_rejected, n_initial,
            )

            # Supprimer la colonne après filtrage (elle n'existe pas côté Oracle)
            if rules.get("drop_after_filter", True):
                df = df.drop(columns=[col_match])
                log.info(
                    "[PREPROC] flux=%s side=%s | colonne '%s' supprimée après filtrage",
                    flux_id, side, col_match,
                )
        else:
            log.warning(
                "[PREPROC] flux=%s side=%s | colonne '%s' introuvable — filtre ignoré. Colonnes: %s",
                flux_id, side, filter_col, df.columns.tolist(),
            )

    # ── 2. Dédoublonnage (ex: Oracle ITEMS — plusieurs INV_ORG par ITEM_CODE) ──
    dedup_col = rules.get("deduplicate_on")

    # Trier par numéro de ligne physique avant dédoublonnage pour garder
    # la première occurrence (la plus proche du haut du fichier).
    if "_LIGNE_FICHIER" in df.columns:
        df = df.sort_values("_LIGNE_FICHIER").reset_index(drop=True)

    # Colonnes à exclure du test de doublon (jamais comparables)
    _dedup_exclude = {"_LIGNE_FICHIER"}

    if dedup_col == "ALL_BUSINESS_COLUMNS":
        cols_tech = ["INV_ORG_CODE", "INV_ORG_NAME", "OPERATING_UNIT_CODE",
                     "OPERATING_UNIT_NAME", "AGENCY_CODE", "OU_TRN_NUMBER",
                     "SERIAL_GENERATED", "SERIAL_LENGTH", "SERIAL_TYPE",
                     "SUPPLIER_REF", "ITEM_IMAGE", "ERR_MSG",
                     "CREATED_BY", "CREATION_DATE", "MODIFIED_DATE", "MODIFIED_BY"]
        # Exclues du test de doublon UNIQUEMENT — les colonnes restent dans
        # le DataFrame (elles portent le code organisation utilisé par le
        # rapport détaillé par pays ; les supprimer ici rendait ce rapport
        # muet : toutes les lignes tombaient en 'autre').
        tech_upper = {x.upper() for x in cols_tech}
        dedup_subset = [
            c for c in df.columns
            if c not in _dedup_exclude and c.upper() not in tech_upper
        ]
        n_before = len(df)
        df = df.drop_duplicates(subset=dedup_subset, keep="first").copy()
        log.info(
            "[PREPROC] flux=%s side=%s | dedup toutes colonnes métier : %d → %d lignes (--%d doublons)",
            flux_id, side, n_before, len(df), n_before - len(df)
        )
    elif dedup_col:
        dedup_keep = rules.get("deduplicate_keep", "first")
        col_match = next(
            (c for c in df.columns if c.upper() == dedup_col.upper()), None
        )

        if col_match:
            n_before = len(df)
            dedup_subset = [c for c in [col_match] if c not in _dedup_exclude]
            if dedup_subset:
                df = df.drop_duplicates(subset=dedup_subset, keep=dedup_keep).copy()
            else:
                df = df.drop_duplicates(keep=dedup_keep).copy()
            n_after  = len(df)
            log.info(
                "[PREPROC] flux=%s side=%s | dédoublonnage sur '%s' : %d → %d lignes (%d doublons supprimés)",
                flux_id, side, col_match, n_before, n_after, n_before - n_after,
            )
        else:
            log.warning(
                "[PREPROC] flux=%s side=%s | colonne dédoublonnage '%s' introuvable — ignoré. Colonnes: %s",
                flux_id, side, dedup_col, df.columns.tolist(),
            )

    # ── Résumé final ─────────────────────────────────────────────────────────
    log.info(
        "[PREPROC] flux=%s side=%s | RÉSULTAT : %d → %d lignes",
        flux_id, side, n_initial, len(df),
    )
    return df
