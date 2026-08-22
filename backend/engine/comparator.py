"""
comparator.py
Moteur de comparaison générique Cegid vs Oracle.
S'adapte à n'importe quel flux — pas de colonnes codées en dur.
"""

import pandas as pd
import re
from datetime import datetime
from typing import Optional
from engine.schema_detector import detecter_colonnes, comparer_schemas
from storage import get_storage


def _normalize_text(v) -> str:
    if not v or pd.isna(v):
        return ""
    v = str(v).strip().upper()
    v = re.sub(r"[^A-Z0-9\s]", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


def comparer_flux(
    df_cegid: pd.DataFrame,
    df_oracle: pd.DataFrame,
    flux_id: str,
    cles: Optional[list] = None,
    valeurs: Optional[list] = None,
    db_path: str = "instance/flux_monitor.db",
    raw_counts: Optional[dict] = None,
) -> dict:
    """
    Compare deux DataFrames (Cegid vs Oracle) et retourne les écarts structurés.

    Si cles et valeurs ne sont pas fournis, les détecte automatiquement.

    Retourne un dict avec :
      ecarts        : liste d'écarts détectés (enrichis pour agent_advisor)
      stats         : statistiques de la comparaison
      schema_diff   : différences de schéma entre les deux fichiers
      cles_utilisees: colonnes clés effectivement utilisées
      nb_lignes     : nb de lignes Cegid / Oracle
    raw_counts: optionnel — dict avec les compteurs brutes (avant pré-traitement)
        pour nb_lignes_cegid / nb_lignes_oracle. Si fourni, utilise ces valeurs
        au lieu de len(df) qui est post-filtre/dédoublonnage.
    """
    timestamp = datetime.utcnow().isoformat()

    schema_diff = comparer_schemas(df_cegid, df_oracle)

    if cles is None:
        analyse = detecter_colonnes(df_cegid)
        cles = analyse["cles_candidates"]
        if not cles:
            cles = [df_cegid.columns[0]]

    if valeurs is None:
        analyse = detecter_colonnes(df_cegid)
        valeurs = analyse["valeurs_candidates"]

    cles = [c for c in cles if c in df_cegid.columns and c in df_oracle.columns]
    valeurs = [c for c in valeurs if c in df_cegid.columns and c in df_oracle.columns]

    # Charger les règles du registry pour les sévérités
    severities = {}
    try:
        from engine.flux_loader import FluxLoader
        config = FluxLoader.load(flux_id)
        for rule in config.comparison_rules:
            severities[rule.column.upper()] = rule.severity
    except Exception:
        pass

    ecarts = []

    # ── CORRECTIF FAUX DOUBLONS ────────────────────────────────────────────
    # Avant : df_cegid.duplicated(subset=cles) régardait Cegid tout seul et
    # signalait un "doublon" dès qu'une clé revenait >1 fois, sans jamais
    # vérifier si Oracle avait le même nombre d'occurrences. Une clé présente
    # 2 fois des deux côtés (cas normal : 2 lignes identiques sur la même
    # facture) était donc signalée à tort.
    #
    # Correctif : on compte les occurrences de chaque clé de chaque côté et
    # on ne garde comme "vrai" doublon que les clés dont le NOMBRE
    # d'occurrences diffère entre Cegid et Oracle.
    df_cegid = df_cegid.copy()
    df_oracle = df_oracle.copy()

    # ── CORRECTIF CLÉS NaN / TYPES MIXTES ──────────────────────────────────
    # pandas ne fait jamais correspondre NaN == NaN dans groupby()/merge().
    # Sans ça, une ligne avec une clé partiellement vide (ex: ligne d'entête
    # sans ITEM_CODE) ne peut jamais matcher entre les deux fichiers, même
    # si elle est identique des deux côtés. On remplace les NaN par une
    # valeur sentinelle, ET on force tout en texte (str) des deux côtés pour
    # éviter un mélange int64/object qui ferait planter le merge().
    if cles:
        for c in cles:
            df_cegid[c] = df_cegid[c].fillna("__NA__").astype(str).str.replace(r"\.0$", "", regex=True)
            df_oracle[c] = df_oracle[c].fillna("__NA__").astype(str).str.replace(r"\.0$", "", regex=True)

    if cles:
        cnt_cegid = df_cegid.groupby(cles).size().rename("_n_cegid")
        cnt_oracle = df_oracle.groupby(cles).size().rename("_n_oracle")
        cnt_compare = pd.concat([cnt_cegid, cnt_oracle], axis=1).fillna(0)
        # CORRECTIF DOUBLE-COMPTAGE : un "doublon" ne doit exister que si
        # l'article est présent des DEUX côtés (n>0 des deux côtés) mais en
        # nombre différent. Si un côté est à 0, ce n'est pas un doublon —
        # c'est déjà couvert par absent_oracle/absent_cegid via le merge()
        # plus bas ; sans ce filtre, chaque écart réel est compté deux fois.
        cnt_mismatch = cnt_compare[
            (cnt_compare["_n_cegid"] != cnt_compare["_n_oracle"]) &
            (cnt_compare["_n_cegid"] > 0) &
            (cnt_compare["_n_oracle"] > 0)
        ]

        for key_vals, row in cnt_mismatch.iterrows():
            key_vals = key_vals if isinstance(key_vals, tuple) else (key_vals,)
            n_cegid, n_oracle = int(row["_n_cegid"]), int(row["_n_oracle"])
            ecarts.append({
                "type_ecart": "doublon",
                "article_id": " | ".join(str(v) for v in key_vals),
                "flux_id": flux_id,
                "source": "cegid" if n_cegid > n_oracle else "oracle",
                "valeur_cegid": n_cegid,
                "valeur_oracle": n_oracle,
                "details": f"Écart de volume sur {cles} : {n_cegid} occurrence(s) Cegid vs {n_oracle} Oracle",
                "timestamp": timestamp,
            })

        # ── CORRECTIF PRODUIT CARTÉSIEN ────────────────────────────────────
        # merge() sur une clé dupliquée des deux côtés fait un produit
        # cartésien (2 lignes x 2 lignes = 4 lignes fusionnées). On ajoute un
        # rang d'occurrence à la clé de fusion pour forcer un appariement
        # ligne à ligne (1ère occurrence Cegid <-> 1ère occurrence Oracle, etc).
        df_cegid["_occ_rank"] = df_cegid.groupby(cles).cumcount()
        df_oracle["_occ_rank"] = df_oracle.groupby(cles).cumcount()
        cles_merge = cles + ["_occ_rank"]
    else:
        cles_merge = cles

    merged = df_cegid.merge(
        df_oracle,
        on=cles_merge,
        how="outer",
        suffixes=("_cegid", "_oracle"),
        indicator=True,
    )

    only_cegid = merged[merged["_merge"] == "left_only"]
    for _, row in only_cegid.iterrows():
        ecarts.append({
            "type_ecart": "absent_oracle",
            "article_id": _make_article_id(row, cles),
            "flux_id": flux_id,
            "source": "cegid",
            "valeur_cegid": None,
            "valeur_oracle": None,
            "details": "Présent dans Cegid, absent dans Oracle",
            "timestamp": timestamp,
        })

    only_oracle = merged[merged["_merge"] == "right_only"]
    for _, row in only_oracle.iterrows():
        ecarts.append({
            "type_ecart": "absent_cegid",
            "article_id": _make_article_id(row, cles),
            "flux_id": flux_id,
            "source": "oracle",
            "valeur_cegid": None,
            "valeur_oracle": None,
            "details": "Présent dans Oracle, absent dans Cegid",
            "timestamp": timestamp,
        })

    both = merged[merged["_merge"] == "both"]
    for col in valeurs:
        col_cegid = f"{col}_cegid" if f"{col}_cegid" in both.columns else col
        col_oracle = f"{col}_oracle" if f"{col}_oracle" in both.columns else col

        if col_cegid not in both.columns or col_oracle not in both.columns:
            continue

        def _sont_egaux(s_cegid, s_oracle):
            """Compare deux Series en gérant NaN=0 et formats de dates."""
            import pandas as pd
            import re

            # Règle 1 — NaN côté Cegid == 0 côté Oracle (et vice versa)
            nan_vs_zero = (
                (s_cegid.isna() & (s_oracle.fillna(-1) == 0)) |
                (s_oracle.isna() & (s_cegid.fillna(-1) == 0)) |
                (s_cegid.isna() & s_oracle.isna())
            )

            # Règle 2 — Normaliser les dates avant comparaison
            def _normalize_date(series):
                """Convertit les dates en format ISO YYYY-MM-DD."""
                try:
                    return pd.to_datetime(series, infer_datetime_format=True, errors='coerce').dt.strftime('%Y-%m-%d')
                except Exception:
                    return series.astype(str)

            col_name = s_cegid.name if hasattr(s_cegid, 'name') else ''
            is_date_col = any(p in str(col_name).upper() for p in ['DATE', 'TIME', 'DT_'])

            if is_date_col:
                norm_cegid  = _normalize_date(s_cegid)
                norm_oracle = _normalize_date(s_oracle)
                egaux = (norm_cegid == norm_oracle) | nan_vs_zero
            else:
                # Comparaison numérique tolérante pour les montants
                try:
                    num_cegid  = pd.to_numeric(s_cegid,  errors='coerce')
                    num_oracle = pd.to_numeric(s_oracle, errors='coerce')
                    # NaN vs 0 → égaux
                    num_nan_zero = (
                        (num_cegid.isna()  & (num_oracle.fillna(-999) == 0)) |
                        (num_oracle.isna() & (num_cegid.fillna(-999)  == 0))
                    )
                    egaux = (num_cegid == num_oracle) | nan_vs_zero | num_nan_zero

                    # Fallback texte quand les deux côtés sont non-numériques
                    both_nan = num_cegid.isna() & num_oracle.isna()
                    text_cegid  = s_cegid.astype(str).fillna("").apply(_normalize_text)
                    text_oracle = s_oracle.astype(str).fillna("").apply(_normalize_text)
                    text_egaux = (text_cegid == text_oracle)
                    egaux = egaux | text_egaux
                except Exception:
                    egaux = (s_cegid.astype(str) == s_oracle.astype(str)) | nan_vs_zero

            return egaux

        diffs = both[~_sont_egaux(both[col_cegid], both[col_oracle])]
        for _, row in diffs.iterrows():
            type_ecart = _detecter_type_valeur(col)
            severity = severities.get(col.upper(), "WARNING")
            ecarts.append({
                "type_ecart": type_ecart,
                "article_id": _make_article_id(row, cles),
                "flux_id": flux_id,
                "colonne": col,
                "valeur_cegid": row.get(col_cegid),
                "valeur_oracle": row.get(col_oracle),
                "details": f"{col} : Cegid={row.get(col_cegid)} / Oracle={row.get(col_oracle)}",
                "timestamp": timestamp,
                "severite": severity,
            })

    _sauvegarder_ecarts(ecarts)

    return {
        "ecarts": ecarts,
        "stats": {
            "nb_lignes_cegid": raw_counts.get("nb_lignes_cegid", len(df_cegid)) if raw_counts else len(df_cegid),
            "nb_lignes_oracle": raw_counts.get("nb_lignes_oracle", len(df_oracle)) if raw_counts else len(df_oracle),
            "nb_ecarts": len(ecarts),
            "nb_absents_oracle": len(only_cegid),
            "nb_absents_cegid": len(only_oracle),
            "nb_doublons": sum(1 for e in ecarts if e["type_ecart"] == "doublon"),
        },
        "schema_diff": schema_diff,
        "cles_utilisees": cles,
        "valeurs_comparees": valeurs,
        "timestamp": timestamp,
        "_merged": merged,
    }


def _make_article_id(row, cles: list) -> str:
    """Construit un identifiant lisible depuis les colonnes clés."""
    return " | ".join(str(row.get(c, "?")) for c in cles)


def _detecter_type_valeur(nom_colonne: str) -> str:
    """Devine le type d'écart depuis le nom de la colonne."""
    col = nom_colonne.lower()
    if any(m in col for m in ["prix", "price", "tarif", "montant_ht", "montant_ttc"]):
        return "prix_different"
    if any(m in col for m in ["montant", "amount", "total", "valeur"]):
        return "montant_different"
    if any(m in col for m in ["qte", "quantite", "quantité", "qty", "stock"]):
        return "quantite_differente"
    return "valeur_differente"


def _sauvegarder_ecarts(ecarts: list):
    """Sauvegarde les écarts via le moteur de stockage unifié."""
    try:
        get_storage().save_ecarts(ecarts)
    except Exception as ex:
        print(f"[comparator] Erreur sauvegarde écarts : {ex}")