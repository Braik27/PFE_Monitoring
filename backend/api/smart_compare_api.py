"""
api/smart_compare_api.py — Analyse intelligente automatique (Cegid vs Oracle)

POST /api/smart/preview     → parse les 2 fichiers, détecte colonnes, propose mapping
POST /api/smart/run         → exécute la comparaison avec le mapping validé
POST /api/smart/learn       → enregistre un mapping corrigé par l'utilisateur
GET  /api/smart/mappings    → liste les mappings appris par flux
"""
from __future__ import annotations
import os
import json
import tempfile
import hashlib
import logging
from datetime import datetime
from flask import Blueprint, jsonify, request, session
from api.auth import require_auth
from storage import get_storage
import pandas as pd
import re

log = logging.getLogger("smart_compare_api")
smart_bp = Blueprint("smart", __name__)
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/tmp/flux_uploads")


# ─────────────────────────────────────────────────────────────────────
# UTILITAIRES DE LECTURE AVEC DÉTECTION RAPIDE DE SÉPARATEUR
# ─────────────────────────────────────────────────────────────────────

def _detect_separator(file_path: str) -> str:
    """Détecte rapidement le séparateur du CSV sans parser le fichier entier (10KB max)."""
    try:
        with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as f:
            sample = f.read(10240)
            if not sample:
                return ";"
            counts = {
                ";": sample.count(";"),
                ",": sample.count(","),
                "\t": sample.count("\t"),
                "|": sample.count("|")
            }
            best_sep = max(counts, key=counts.get)
            return best_sep if counts[best_sep] > 0 else ";"
    except Exception:
        return ";"


def _read_file(file_storage, max_rows: int = 5000) -> pd.DataFrame:
    fname = file_storage.filename.lower()
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    suffix = os.path.splitext(fname)[1] or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, dir=UPLOAD_FOLDER, suffix=suffix) as f:
        file_storage.save(f.name)
        tmp_path = f.name
    try:
        if suffix in (".xlsx", ".xls"):
            df = pd.read_excel(tmp_path, nrows=max_rows, dtype=str)
        else:
            sep = _detect_separator(tmp_path)
            bad_lines = []
            def _handle_bad(line):
                bad_lines.append(line)
                return None
            df = pd.read_csv(tmp_path, sep=sep, nrows=max_rows, dtype=str,
                             encoding="utf-8-sig", engine='python', on_bad_lines=_handle_bad)
        df.fillna("", inplace=True)
        df.columns = [str(c).strip() for c in df.columns]
        if bad_lines:
            log.warning("[smart] %d lignes mal formées ignorées dans %s", len(bad_lines), tmp_path)
        return df
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _read_file_from_path(file_path: str, max_rows: int = 200_000) -> pd.DataFrame:
    suffix = os.path.splitext(file_path.lower())[1]
    bad_lines = []
    def _handle_bad(line):
        bad_lines.append(line)
        return None
    try:
        if suffix in (".xlsx", ".xls"):
            df = pd.read_excel(file_path, nrows=max_rows, dtype=str)
        else:
            sep = _detect_separator(file_path)
            df = pd.read_csv(file_path, sep=sep, nrows=max_rows, dtype=str,
                             encoding="utf-8-sig", engine='python', on_bad_lines=_handle_bad)
        df.fillna("", inplace=True)
        df.columns = [str(c).strip() for c in df.columns]
        if bad_lines:
            log.warning("[smart] %d lignes mal formées ignorées dans %s", len(bad_lines), file_path)
        return df
    except Exception:
        import traceback
        raise RuntimeError(f"Erreur lecture fichier {file_path}: {traceback.format_exc()}")


# ─────────────────────────────────────────────────────────────────────
# ANALYSE DE COLONNES
# ─────────────────────────────────────────────────────────────────────

_KEY_KEYWORDS = [
    "id", "ref", "code", "num", "numero", "number", "key", "pk",
    "invoice", "facture", "order", "commande", "bon", "ticket",
    "article", "item", "product", "produit", "sku", "ean", "upc",
    "client", "customer", "vendor", "fournisseur", "compte", "account",
    "header", "ligne", "line", "index", "sequence",
]
_VALUE_KEYWORDS = [
    "montant", "amount", "total", "prix", "price", "qty", "quantite",
    "quantity", "solde", "balance", "debit", "credit", "ttc", "ht",
    "tva", "tax", "net", "gross", "brut",
]
_DATE_KEYWORDS = [
    "date", "time", "jour", "day", "mois", "month", "annee", "year",
    "created", "updated", "timestamp", "dt", "periode", "period",
]


def _analyze_columns(df: pd.DataFrame) -> list:
    result = []
    n = len(df)
    for col in df.columns:
        col_lower = col.lower().replace(" ", "_").replace("-", "_")
        nb_unique = df[col].nunique()
        nb_null   = (df[col] == "").sum()
        taux_uni  = round(nb_unique / n * 100, 1) if n > 0 else 0
        taux_null = round(nb_null  / n * 100, 1) if n > 0 else 0

        sample = df[col].replace("", pd.NA).dropna().head(50)
        est_numeric = False
        est_date    = False
        if len(sample) > 0:
            def _to_num(v):
                try:
                    float(str(v).replace(",", ".").replace(" ", ""))
                    return True
                except Exception:
                    return False
            est_numeric = sample.apply(_to_num).mean() > 0.85
            if any(kw in col_lower for kw in _DATE_KEYWORDS):
                est_date = True

        type_detecte = "date" if est_date else ("decimal" if est_numeric else "text")

        score_cle = 0
        if any(kw in col_lower for kw in _KEY_KEYWORDS):
            score_cle += 30
        if taux_uni >= 90:
            score_cle += 40
        elif taux_uni >= 70:
            score_cle += 20
        if not est_numeric:
            score_cle += 20
        if taux_null > 10:
            score_cle -= 20
        if est_numeric and any(kw in col_lower for kw in _VALUE_KEYWORDS):
            score_cle -= 30

        if any(kw in col_lower for kw in _DATE_KEYWORDS):
            role = "date"
        elif any(kw in col_lower for kw in _VALUE_KEYWORDS):
            role = "valeur"
        elif score_cle >= 50:
            role = "cle"
        else:
            role = "donnee"

        result.append({
            "nom":          col,
            "type_detecte": type_detecte,
            "taux_unicite": taux_uni,
            "taux_nulls":   taux_null,
            "nb_uniques":   int(nb_unique),
            "nb_lignes":    n,
            "score_cle":    max(0, min(100, score_cle)),
            "role":         role,
            "sample":       sample.head(3).tolist(),
        })
    return result


# ─────────────────────────────────────────────────────────────────────
# MAPPING AUTOMATIQUE
# ─────────────────────────────────────────────────────────────────────

def _normalize_col_name(name: str) -> str:
    n = name.lower()
    n = re.sub(r"[^a-z0-9]", "_", n)
    n = re.sub(r"_+", "_", n).strip("_")
    for prefix in ["col_", "fld_", "f_", "c_", "d_"]:
        if n.startswith(prefix):
            n = n[len(prefix):]
    return n


_SYNONYMES = [
    {"id", "identifiant", "identifier", "pk", "key"},
    {"ref", "reference", "numero", "number", "num", "no"},
    {"article", "item", "produit", "product", "sku", "code_article", "art"},
    {"client", "customer", "cust", "cli", "acheteur"},
    {"montant", "amount", "total", "valeur", "value", "prix", "price"},
    {"quantite", "quantity", "qty", "qte", "nb", "nombre"},
    {"date", "dt", "jour", "day"},
    {"facture", "invoice", "inv", "fact"},
    {"commande", "order", "cmd", "ord"},
    {"fournisseur", "vendor", "supplier", "fourn"},
    {"header", "entete", "head"},
    {"ligne", "line", "detail", "row"},
]


def _col_similarity(a: str, b: str) -> float:
    na = _normalize_col_name(a)
    nb = _normalize_col_name(b)
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.85
    toks_a = set(re.split(r"_+", na))
    toks_b = set(re.split(r"_+", nb))
    common = toks_a & toks_b
    if common:
        jaccard = len(common) / len(toks_a | toks_b)
        if jaccard >= 0.5:
            return 0.75
    for groupe in _SYNONYMES:
        if any(kw in na for kw in groupe) and any(kw in nb for kw in groupe):
            return 0.7
    prefix_len = 0
    for ca, cb in zip(na, nb):
        if ca == cb:
            prefix_len += 1
        else:
            break
    if prefix_len >= 4:
        return 0.5 + prefix_len * 0.02
    return 0.0


def _generate_mapping(cols_cegid: list, cols_oracle: list, learned: dict = None) -> list:
    mapping = []
    used_oracle = set()

    if learned:
        for cegid_col, oracle_col in learned.items():
            oracle_match = next((c for c in cols_oracle if c["nom"] == oracle_col), None)
            cegid_match  = next((c for c in cols_cegid  if c["nom"] == cegid_col),  None)
            if cegid_match and oracle_match:
                mapping.append({
                    "cegid_col":   cegid_col,
                    "oracle_col":  oracle_col,
                    "score":       1.0,
                    "confiance":   100,
                    "source":      "appris",
                    "cegid_role":  cegid_match["role"],
                    "oracle_role": oracle_match["role"],
                })
                used_oracle.add(oracle_col)

    already_mapped = {m["cegid_col"] for m in mapping}
    for c_col in cols_cegid:
        if c_col["nom"] in already_mapped:
            continue
        best_score  = 0.0
        best_oracle = None
        for o_col in cols_oracle:
            if o_col["nom"] in used_oracle:
                continue
            score = _col_similarity(c_col["nom"], o_col["nom"])
            if score > best_score:
                best_score  = score
                best_oracle = o_col

        if best_oracle and best_score > 0.3:
            mapping.append({
                "cegid_col":   c_col["nom"],
                "oracle_col":  best_oracle["nom"],
                "score":       round(best_score, 2),
                "confiance":   int(best_score * 100),
                "source":      "auto",
                "cegid_role":  c_col["role"],
                "oracle_role": best_oracle["role"],
            })
            used_oracle.add(best_oracle["nom"])
        else:
            mapping.append({
                "cegid_col":   c_col["nom"],
                "oracle_col":  None,
                "score":       0.0,
                "confiance":   0,
                "source":      "none",
                "cegid_role":  c_col["role"],
                "oracle_role": None,
            })

    mapping.sort(key=lambda x: (-x["score"], x["cegid_col"]))
    return mapping


def _suggest_key_columns(cols_cegid: list, cols_oracle: list, mapping: list) -> list:
    suggestions = []
    cols_cegid_map = {c["nom"]: c for c in cols_cegid}
    for m in mapping:
        if not m["oracle_col"]:
            continue
        cegid_meta = cols_cegid_map.get(m["cegid_col"], {})
        score_global = (
            cegid_meta.get("score_cle", 0) * 0.5
            + m["confiance"] * 0.3
            + cegid_meta.get("taux_unicite", 0) * 0.2
        )
        suggestions.append({
            "cegid_col":    m["cegid_col"],
            "oracle_col":   m["oracle_col"],
            "score_global": round(score_global, 1),
            "score_cle":    cegid_meta.get("score_cle", 0),
            "taux_unicite": cegid_meta.get("taux_unicite", 0),
            "confiance":    m["confiance"],
        })
    suggestions.sort(key=lambda x: -x["score_global"])
    return suggestions[:5]


# ─────────────────────────────────────────────────────────────────────
# COMPARAISON VECTORISÉE
# ─────────────────────────────────────────────────────────────────────

def _run_comparison(df_c: pd.DataFrame, df_o: pd.DataFrame,
                    key_cols_cegid: list, key_cols_oracle: list,
                    compare_cols: list) -> dict:
    import time
    t0 = time.time()
    log.info("[smart] Comparison start: %d rows Cegid vs %d rows Oracle", len(df_c), len(df_o))

    # 1. Renommer colonnes Oracle
    rename_map = {
        pair["oracle_col"]: pair["cegid_col"]
        for pair in compare_cols
        if pair.get("oracle_col") and pair.get("cegid_col")
    }
    df_o_r = df_o.rename(columns=rename_map).copy()

    # 2. Clé composite
    KEY_COL = "__key__"

    def _build_key(df: pd.DataFrame, cols: list) -> pd.Series:
        parts = []
        for c in cols:
            if c in df.columns:
                parts.append(df[c].astype(str).str.strip().str.upper())
            else:
                parts.append(pd.Series([""] * len(df), index=df.index))
        return parts[0] if len(parts) == 1 else parts[0].str.cat(parts[1:], sep="||")

    df_c  = df_c.copy()
    df_o_r = df_o_r.copy()
    df_c[KEY_COL]   = _build_key(df_c,   key_cols_cegid)
    df_o_r[KEY_COL] = _build_key(df_o_r, key_cols_cegid)

    # 3. Colonnes
    cols_to_compare = [
        p for p in compare_cols
        if p.get("compare", True)
        and p.get("cegid_col")
        and p["cegid_col"] in df_c.columns
        and p["cegid_col"] in df_o_r.columns
    ]
    compare_names = [p["cegid_col"] for p in cols_to_compare]

    # 4. Merge inner
    df_c_sub   = df_c[[KEY_COL]  + [c for c in compare_names if c in df_c.columns]].copy()
    df_o_sub   = df_o_r[[KEY_COL] + [c for c in compare_names if c in df_o_r.columns]].copy()

    df_o_sub = df_o_sub.drop_duplicates(subset=[KEY_COL], keep="first")
    merged = df_c_sub.merge(df_o_sub, on=KEY_COL, how="inner", suffixes=("_c", "_o"))
    n_matched = len(merged)

    # 5. Anomalies
    anomalies     = []
    diffs_par_col = {}

    for pair in cols_to_compare:
        col = pair["cegid_col"]
        col_c = f"{col}_c" if f"{col}_c" in merged.columns else col
        col_o = f"{col}_o" if f"{col}_o" in merged.columns else col
        if col_c not in merged.columns or col_o not in merged.columns:
            continue

        vc = merged[col_c].astype(str).str.strip()
        vo = merged[col_o].astype(str).str.strip()

        mask = vc != vo

        try:
            fc = pd.to_numeric(vc.str.replace(",", ".", regex=False), errors="coerce")
            fo = pd.to_numeric(vo.str.replace(",", ".", regex=False), errors="coerce")
            tol = pair.get("tolerance", 0.01)
            numeric_ok = (fc.notna() & fo.notna() & (abs(fc - fo) <= tol))
            mask = mask & ~numeric_ok
        except Exception:
            pass

        n_diff = int(mask.sum())
        if n_diff == 0:
            continue

        diffs_par_col[col] = n_diff
        severity = "CRITIQUE" if pair.get("cegid_role") in ("cle",) else "WARNING"

        diff_rows = merged[mask][[KEY_COL, col_c, col_o]].head(500)
        for _, row in diff_rows.iterrows():
            anomalies.append({
                "type":      f"ECART_{col}",
                "severity":  severity,
                "key":       row[KEY_COL],
                "key_str":   row[KEY_COL],
                "col":       col,
                "val_cegid": row[col_c],
                "val_oracle": row[col_o],
                "delta":     None,
                "message":   f"'{col}' : Cegid='{row[col_c]}' ≠ Oracle='{row[col_o]}'",
            })

    # 6. Manquants
    keys_c   = set(df_c[KEY_COL].unique())
    keys_o   = set(df_o_r[KEY_COL].unique())
    only_c   = keys_c - keys_o
    only_o   = keys_o - keys_c

    n_only_cegid  = len(only_c)
    n_only_oracle = len(only_o)

    remaining = max(0, 500 - len(anomalies))
    for key in list(only_c)[:remaining // 2 + 1]:
        anomalies.append({
            "type":      "MANQUANT_ORACLE",
            "severity":  "CRITIQUE",
            "key":       key,
            "key_str":   key,
            "col":       None, "val_cegid": None, "val_oracle": None, "delta": None,
            "message":   "Ligne présente dans Cegid, absente dans Oracle",
        })

    remaining = max(0, 500 - len(anomalies))
    for key in list(only_o)[:remaining]:
        anomalies.append({
            "type":      "MANQUANT_CEGID",
            "severity":  "CRITIQUE",
            "key":       key,
            "key_str":   key,
            "col":       None, "val_cegid": None, "val_oracle": None, "delta": None,
            "message":   "Ligne présente dans Oracle, absente dans Cegid",
        })

    n_value_diff = sum(diffs_par_col.values())
    n_anomalies_total = n_only_cegid + n_only_oracle + n_value_diff

    n_total = max(len(df_c), len(df_o))
    n_bad   = n_only_cegid + n_only_oracle
    concordance = max(0.0, round((n_total - n_bad) / n_total * 100, 1)) if n_total > 0 else 100.0

    top_cols = sorted(
        [{"col": c, "n": n} for c, n in diffs_par_col.items()],
        key=lambda x: -x["n"]
    )[:10]

    return {
        "n_cegid":        len(df_c),
        "n_oracle":       len(df_o),
        "n_matched":      n_matched,
        "n_only_cegid":   n_only_cegid,
        "n_only_oracle":  n_only_oracle,
        "n_value_diff":   n_value_diff,
        "n_anomalies":    n_anomalies_total,
        "n_critiques":    sum(1 for a in anomalies if a["severity"] == "CRITIQUE"),
        "n_warnings":     sum(1 for a in anomalies if a["severity"] == "WARNING"),
        "concordance":    concordance,
        "top_error_cols": top_cols,
        "anomalies":      anomalies[:500],
    }


# ─────────────────────────────────────────────────────────────────────
# MAPPING PERSISTENCY VIA BaseStorage
# ─────────────────────────────────────────────────────────────────────

def _load_learned_mapping(flux_key: str) -> dict:
    return get_storage().load_learned_mapping(flux_key)


def _save_learned_mapping(flux_key: str, mapping: dict, username: str):
    for cegid_col, oracle_col in mapping.items():
        get_storage().save_smart_mapping(flux_key, cegid_col, oracle_col, username)


def _flux_key(cols_cegid: list, cols_oracle: list) -> str:
    key = "|".join(sorted(cols_cegid)) + "###" + "|".join(sorted(cols_oracle))
    return hashlib.md5(key.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────
# ENRICHISSEMENT IA (Centralized Cloud LLM client)
# ─────────────────────────────────────────────────────────────────────

def _ia_enhance_mapping(cols_c, cols_o, mapping, sample_c, sample_o) -> dict:
    cols_c_names = [c["nom"] for c in cols_c[:8]]
    cols_o_names = [c["nom"] for c in cols_o[:8]]
    map_data     = {m["cegid_col"]: m["oracle_col"] for m in mapping[:8]}
    prompt = f"""Liste max 3 corrections pour ce mapping. JSON only.
Cegid: {cols_c_names}
Oracle: {cols_o_names}
Mapping: {map_data}
Chaque correction doit avoir exactement: cegid_col, oracle_col.
JSON: {{"corrections":[{{"cegid_col":"COL_CEGID","oracle_col":"COL_ORACLE"}}],"confiance_globale":85,"observations":"X"}}"""
    try:
        from ai.llm_client import call_llm, clean_and_parse_json
        reply = call_llm([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=300)
        result = clean_and_parse_json(reply)
        if result:
            result["ok"] = True
            return result
        return {"ok": False, "reason": "JSON non trouvé"}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────
# ROUTES FLASK
# ─────────────────────────────────────────────────────────────────────

def _normalize_ia_corrections(ia_result: dict, cols_c: list, cols_o: list) -> list[dict]:
    corrections = ia_result.get("corrections")
    if not isinstance(corrections, list):
        return []

    valid_c = {c["nom"] for c in cols_c}
    valid_o = {c["nom"] for c in cols_o}
    cegid_aliases = ("cegid_col", "cegid", "colonne_cegid", "source", "from")
    oracle_aliases = ("oracle_col", "oracle", "colonne_oracle", "target", "to")
    normalized = []

    for correction in corrections:
        if not isinstance(correction, dict):
            log.warning("Correction IA ignoree (format invalide): %r", correction)
            continue

        cegid_col = next((correction.get(k) for k in cegid_aliases if correction.get(k)), None)
        oracle_col = next((correction.get(k) for k in oracle_aliases if correction.get(k)), None)
        if not cegid_col or not oracle_col:
            log.warning("Correction IA ignoree (cles manquantes): %r", correction)
            continue

        cegid_col = str(cegid_col).strip()
        oracle_col = str(oracle_col).strip()
        if cegid_col not in valid_c or oracle_col not in valid_o:
            log.warning("Correction IA ignoree (colonnes inconnues): %r", correction)
            continue

        normalized.append({"cegid_col": cegid_col, "oracle_col": oracle_col})

    return normalized


@smart_bp.post("/api/smart/preview")
@require_auth
def smart_preview():
    f_cegid  = request.files.get("cegid")
    f_oracle = request.files.get("oracle")
    if not f_cegid or not f_oracle:
        return jsonify({"error": "Les fichiers cegid et oracle sont requis"}), 400
    try:
        df_c = _read_file(f_cegid)
        df_o = _read_file(f_oracle)
    except Exception as e:
        return jsonify({"error": f"Erreur de lecture : {e}"}), 400

    cols_c = _analyze_columns(df_c)
    cols_o = _analyze_columns(df_o)
    flux_key        = _flux_key([c["nom"] for c in cols_c], [c["nom"] for c in cols_o])
    learned         = _load_learned_mapping(flux_key)
    mapping         = _generate_mapping(cols_c, cols_o, learned)
    key_suggestions = _suggest_key_columns(cols_c, cols_o, mapping)
    sample_c        = df_c.head(3).to_dict(orient="records")
    sample_o        = df_o.head(3).to_dict(orient="records")

    ia_result = {}
    use_ia = request.form.get("use_ia", "true").lower() != "false"
    if use_ia:
        ia_result = _ia_enhance_mapping(cols_c, cols_o, mapping, sample_c, sample_o)
        if ia_result.get("ok") and ia_result.get("corrections"):
            normalized_corrections = _normalize_ia_corrections(ia_result, cols_c, cols_o)
            corrections_map = {c["cegid_col"]: c["oracle_col"] for c in normalized_corrections}
            ia_result["corrections"] = normalized_corrections
            for m in mapping:
                if m["cegid_col"] in corrections_map:
                    m["oracle_col"] = corrections_map[m["cegid_col"]]
                    m["source"]     = "ia"
                    m["confiance"]  = 95
                    m["score"]      = 0.95

    return jsonify({
        "ok":              True,
        "flux_key":        flux_key,
        "n_cegid":         len(df_c),
        "n_oracle":        len(df_o),
        "cols_cegid":      cols_c,
        "cols_oracle":     cols_o,
        "mapping":         mapping,
        "key_suggestions": key_suggestions,
        "sample_cegid":    sample_c,
        "sample_oracle":   sample_o,
        "ia":              ia_result,
        "has_learned":     bool(learned),
        "n_learned":       len(learned),
    })


@smart_bp.post("/api/smart/run")
@require_auth
def smart_run():
    f_cegid  = request.files.get("cegid")
    f_oracle = request.files.get("oracle")
    if not f_cegid or not f_oracle:
        return jsonify({"error": "Fichiers requis"}), 400
    try:
        config_json = request.form.get("config", "{}")
        config      = json.loads(config_json)
    except Exception:
        return jsonify({"error": "Config JSON invalide"}), 400

    mapping       = config.get("mapping", [])
    key_cols_pair = config.get("key_cols", [])
    flux_key      = config.get("flux_key", "")
    if not mapping or not key_cols_pair:
        return jsonify({"error": "mapping et key_cols sont requis"}), 400

    key_cols_cegid  = [k["cegid_col"]  for k in key_cols_pair]
    key_cols_oracle = [k["oracle_col"] for k in key_cols_pair]
    if not key_cols_cegid:
        return jsonify({"error": "Au moins une colonne clé est requise"}), 400

    try:
        df_c = _read_file(f_cegid,  max_rows=50000)
        df_o = _read_file(f_oracle, max_rows=50000)
    except Exception as e:
        return jsonify({"error": f"Erreur lecture : {e}"}), 400

    cols_c_map = {c["nom"]: c for c in _analyze_columns(df_c)}
    for m in mapping:
        m["cegid_role"] = cols_c_map.get(m.get("cegid_col", ""), {}).get("role", "donnee")

    try:
        result = _run_comparison(df_c, df_o, key_cols_cegid, key_cols_oracle, mapping)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

    if flux_key:
        final_mapping = {m["cegid_col"]: m["oracle_col"] for m in mapping if m.get("oracle_col")}
        username = session.get("user", {}).get("username", "system")
        _save_learned_mapping(flux_key, final_mapping, username)

    return jsonify({"ok": True, **result})


@smart_bp.post("/api/smart/learn")
@require_auth
def smart_learn():
    data     = request.get_json(silent=True) or {}
    flux_key = data.get("flux_key", "")
    mapping  = data.get("mapping", {})
    if not flux_key or not mapping:
        return jsonify({"error": "flux_key et mapping requis"}), 400
    username = session.get("user", {}).get("username", "system")
    _save_learned_mapping(flux_key, mapping, username)
    return jsonify({"ok": True, "saved": len(mapping)})


@smart_bp.post("/api/smart/analyze-anomalies")
@require_auth
def analyze_anomalies():
    data      = request.get_json(silent=True) or {}
    anomalies = data.get("anomalies", [])
    if not anomalies:
        return jsonify({"error": "anomalies requises"}), 400
    sample_anom = anomalies[:10]
    prompt = f"""Tu es un expert données. Analyse ces anomalies et donne:
- Cause probable
- Actions concrètes
Anomalies: {json.dumps(sample_anom, ensure_ascii=False)}
JSON: {{"causes":["X"], "actions":["A"]}}"""
    try:
        from ai.llm_client import call_llm, clean_and_parse_json
        reply = call_llm([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=500)
        result = clean_and_parse_json(reply)
        if result:
            result["ok"] = True
            return jsonify(result)
        return jsonify({"ok": True, "raw_response": reply[:500]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@smart_bp.get("/api/smart/mappings")
@require_auth
def smart_mappings():
    try:
        mappings = get_storage().list_smart_mappings()
        return jsonify({"ok": True, "mappings": mappings})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@smart_bp.get("/api/smart/test-ai")
@require_auth
def test_ai():
    try:
        from ai.llm_client import call_llm
        reply = call_llm([{"role": "user", "content": "Réponds 'OK' seulement"}], temperature=0.1, max_tokens=5)
        return jsonify({"ok": True, "reply": reply})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
