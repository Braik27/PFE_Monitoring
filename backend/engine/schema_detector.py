"""
schema_detector.py
Détection automatique des colonnes clés et de valeurs dans un CSV.
Permet à l'app de s'adapter à n'importe quel flux sans configuration manuelle.
"""

import pandas as pd
from typing import Tuple


def detecter_colonnes(df: pd.DataFrame) -> dict:
    """
    Analyse un DataFrame CSV et retourne les colonnes clés candidates
    et les colonnes de valeurs candidates.

    Logique :
    - Colonne CLÉ   : taux d'unicité > 70%, souvent de type string ou int
    - Colonne VALEUR: numérique (float/int), pas une clé
    - Colonne DATE  : contient 'date', 'time', 'jour', 'mois' dans le nom

    Retourne un dict avec :
      cles_candidates   : liste de colonnes probablement des identifiants
      valeurs_candidates: liste de colonnes probablement des montants/quantités
      dates_candidates  : liste de colonnes probablement des dates
      toutes            : liste complète avec métadonnées par colonne
    """
    cles = []
    valeurs = []
    dates = []
    toutes = []

    for col in df.columns:
        nb_lignes = len(df)
        nb_uniques = df[col].nunique()
        taux_unicite = nb_uniques / nb_lignes if nb_lignes > 0 else 0
        dtype = str(df[col].dtype)
        col_lower = col.lower()

        mots_date = ["date", "time", "jour", "mois", "annee", "année", "created", "updated", "timestamp"]
        est_date = any(m in col_lower for m in mots_date)

        est_cle = (
            taux_unicite > 0.7
            and not est_date
            and dtype in ("object", "int64", "int32")
        )

        est_valeur = (
            dtype in ("float64", "float32", "int64", "int32")
            and not est_cle
            and not est_date
        )

        meta = {
            "nom": col,
            "type": dtype,
            "taux_unicite": round(taux_unicite * 100, 1),
            "nb_uniques": nb_uniques,
            "nb_nulls": int(df[col].isna().sum()),
            "est_cle": est_cle,
            "est_valeur": est_valeur,
            "est_date": est_date,
        }
        toutes.append(meta)

        if est_cle:
            cles.append(col)
        if est_valeur:
            valeurs.append(col)
        if est_date:
            dates.append(col)

    return {
        "cles_candidates": cles,
        "valeurs_candidates": valeurs,
        "dates_candidates": dates,
        "toutes": toutes,
    }


def valider_schema(df: pd.DataFrame, colonnes_requises: list) -> Tuple[bool, list]:
    """
    Vérifie que le DataFrame contient bien toutes les colonnes requises.
    Retourne (True, []) si OK, (False, [colonnes manquantes]) sinon.
    """
    manquantes = [c for c in colonnes_requises if c not in df.columns]
    return len(manquantes) == 0, manquantes


def comparer_schemas(df1: pd.DataFrame, df2: pd.DataFrame) -> dict:
    """
    Compare les schémas de deux DataFrames (Cegid vs Oracle).
    Utile pour détecter si les colonnes sont différentes entre les deux exports.
    """
    cols1 = set(df1.columns)
    cols2 = set(df2.columns)

    return {
        "communes": sorted(cols1 & cols2),
        "seulement_cegid": sorted(cols1 - cols2),
        "seulement_oracle": sorted(cols2 - cols1),
        "schemas_identiques": cols1 == cols2,
    }