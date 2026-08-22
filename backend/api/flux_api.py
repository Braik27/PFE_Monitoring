"""
api/flux_api.py
Routes Flask pour l'upload, la comparaison et l'affichage des écarts.
"""

import os
import uuid
import json
import threading
import pandas as pd
import tempfile
from datetime import datetime

from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename

from engine.comparator import comparer_flux
from engine.schema_detector import detecter_colonnes, comparer_schemas
from ai.agent_advisor import analyser_rapport
from engine.flux_loader import FluxLoader
from api.auth import require_auth, require_admin
import logging
log = logging.getLogger(__name__)

flux_bp = Blueprint("flux", __name__, url_prefix="/api/flux")

ALLOWED_EXTENSIONS = {"csv"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ─────────────────────────────────────────────
# WORKER — tourne dans un thread séparé
# ─────────────────────────────────────────────

def _run_analysis_worker(
    app,
    job_id: str,
    flux_id: str,
    path_cegid: str,
    path_oracle: str,
    cles: list,
    valeurs: list,
    db_path: str,
    analyst: str = "system",
):
    """
    Fonction qui tourne dans un thread séparé.
    Lit les CSV, lance le pipeline, sauvegarde le résultat en base.
    """
    with app.app_context():
        from storage import get_storage
        storage = get_storage()

        # 1. Marquer le job comme en cours
        storage.update_job_async(job_id, "RUNNING")

        try:
            # 2. Lire les CSV depuis les fichiers temporaires avec détection des lignes invalides
            bad_cegid = []
            bad_oracle = []

            def _make_bad_handler(bad_list):
                def _handle(line):
                    bad_list.append(line)
                    return None
                return _handle

            df_cegid = pd.read_csv(
                path_cegid, sep=None, engine="python", encoding="utf-8-sig",
                on_bad_lines=_make_bad_handler(bad_cegid),
                keep_default_na=False
            )
            df_oracle = pd.read_csv(
                path_oracle, sep=None, engine="python", encoding="utf-8-sig",
                on_bad_lines=_make_bad_handler(bad_oracle),
                keep_default_na=False
            )

            # Normaliser les colonnes en majuscules
            df_cegid.columns  = df_cegid.columns.str.upper().str.strip()
            df_oracle.columns = df_oracle.columns.str.upper().str.strip()

            # ── Ajouter _LIGNE_FICHIER (numéro de ligne dans le CSV d'origine) ──
            # La ligne 1 = en-tête, les données commencent à la ligne 2
            df_cegid["_LIGNE_FICHIER"]  = range(2, 2 + len(df_cegid))
            df_oracle["_LIGNE_FICHIER"] = range(2, 2 + len(df_oracle))

            # ── Capturer les longueurs BRUTES avant pré-traitement ──────────
            raw_counts = {
                "nb_lignes_cegid": len(df_cegid),
                "nb_lignes_oracle": len(df_oracle),
            }

            # ── Pré-traitement (filtre PrefiR, dédoublonnage, etc.) ─────────
            try:
                from engine.preprocessor import apply_preprocessing
                _config = FluxLoader.load(flux_id.upper())
                _pre    = getattr(_config, "pre_processing", None) or {}
                if _pre:
                    df_cegid  = apply_preprocessing(df_cegid,  _pre.get("cegid",  {}), "cegid",  flux_id)
                    df_oracle = apply_preprocessing(df_oracle, _pre.get("oracle", {}), "oracle", flux_id)
            except Exception as _e:
                log.warning("[PREPROC] Ignoré (non bloquant): %s", _e)
            # ─────────────────────────────────────────────────────────────────

            # ── Détection des colonnes ──────────────────────────────────────
            cols_communes = [c for c in df_cegid.columns if c in df_oracle.columns]

            # Niveau 1 — Lire depuis le registry
            if not cles:
                try:
                    config = FluxLoader.load(flux_id.upper())
                    registry_keys = [k.upper() for k in (config.key_columns or [])]
                    cles = [c for c in registry_keys if c in cols_communes]
                    if cles:
                        log.info("[WORKER] Clés depuis registry: %s", cles)
                except Exception:
                    pass  # flux pas encore dans le registry → niveau 2

            # Niveau 2 — Détection automatique (flux nouveau)
            if not cles:
                log.info("[WORKER] flux '%s' non trouvé dans registry — détection auto", flux_id)

                # Chercher colonnes avec pattern ID/NUM/CODE et peu de doublons
                candidates = []
                for col in cols_communes:
                    col_upper = col.upper()
                    # Pattern de nom
                    is_id_col = any(p in col_upper for p in
                                    ["_ID", "_NUM", "_CODE", "_KEY", "_REF", "NUMBER"])
                    if not is_id_col:
                        continue
                    # Vérifier unicité — ratio doublons < 10%
                    total = len(df_cegid)
                    n_unique = df_cegid[col].nunique()
                    if total > 0 and (n_unique / total) > 0.5:
                        candidates.append((col, n_unique / total))

                # Trier par unicité décroissante
                candidates.sort(key=lambda x: x[1], reverse=True)
                cles = [c[0] for c in candidates[:2]]  # max 2 clés

                if cles:
                    log.info("[WORKER] Clés auto-détectées: %s", cles)

            # Niveau 3 — Fallback ultime
            if not cles:
                cles = [cols_communes[0]] if cols_communes else []
                log.warning("[WORKER] Fallback première colonne: %s", cles)

            # ── Colonnes de valeurs ─────────────────────────────────────────
            if not valeurs:
                # Niveau 1 — comparison_rules du registry
                try:
                    config = FluxLoader.load(flux_id.upper())
                    rule_cols = [r.column.upper() for r in (config.comparison_rules or [])]
                    valeurs = [c for c in rule_cols if c in cols_communes and c not in cles]
                    if valeurs:
                        log.info("[WORKER] Valeurs depuis registry rules: %s", valeurs)
                except Exception:
                    pass

                # Niveau 2 — colonnes numériques communes (max 5)
                if not valeurs:
                    valeurs = [
                        c for c in cols_communes
                        if df_cegid[c].dtype in ['float64', 'int64']
                        and c not in cles
                    ][:5]

            # Sécurité finale
            cles    = [c for c in cles    if c in cols_communes]
            valeurs = [c for c in valeurs if c in cols_communes]

            if not cles:
                raise ValueError(
                    f"Impossible de trouver des colonnes clés pour le flux '{flux_id}'. "
                    f"Colonnes communes disponibles: {cols_communes[:10]}"
                )

            log.info("[WORKER] job=%s flux=%s → cles=%s valeurs=%s",
                     job_id, flux_id, cles, valeurs)

            # 4. Lancer le pipeline
            resultat = comparer_flux(
                df_cegid, df_oracle, flux_id, cles, valeurs, db_path,
                raw_counts=raw_counts,
            )

            # 4ter. Rapport détaillé "niveau consultant"
            merged = resultat.pop("_merged", None)
            detailed_report = []
            detailed_excel_path = None
            try:
                from engine.detailed_report import build_detail_report, export_detailed_excel
                _cr = []
                try:
                    _cfg = FluxLoader.load(flux_id.upper())
                    _cr = [{"column": r.column, "tolerance": r.tolerance, "severity": r.severity}
                           for r in (_cfg.comparison_rules or [])]
                except Exception:
                    pass
                detailed_report = build_detail_report(
                    merged, cles, valeurs, flux_id,
                    comparison_rules=_cr,
                )
                if detailed_report:
                    import os as _os
                    reports_dir = _os.path.join(
                        _os.path.dirname(_os.path.dirname(__file__)),
                        "reports",
                    )
                    detailed_excel_path = export_detailed_excel(
                        detailed_report, resultat["stats"], flux_id,
                        output_path=_os.path.join(
                            reports_dir,
                            f"rapport_detaille_{flux_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx",
                        ),
                    )
            except Exception as _e:
                log.warning("[DETAIL] Rapport détaillé ignoré (non bloquant): %s", _e)

            # 4ter-bis. Compteurs SEVERITE depuis le rapport détaillé
            # (source de vérité unique pour l'Excel ET le dashboard)
            _n_crit_from_detail = sum(1 for r in detailed_report if r.get("SEVERITE") == "CRITIQUE")
            _n_warn_from_detail = sum(1 for r in detailed_report if r.get("SEVERITE") == "WARNING")

            # 4bis. Anomalies lignes CSV invalides
            if bad_cegid:
                resultat["ecarts"].append({
                    "type_ecart": "LIGNES_CSV_INVALIDES",
                    "article_id": "",
                    "flux_id": flux_id,
                    "source": "cegid",
                    "colonne": "",
                    "valeur_cegid": str(len(bad_cegid)),
                    "valeur_oracle": "",
                    "details": (
                        f"CEGID : {len(bad_cegid)} ligne(s) mal formée(s) ignorée(s). "
                        f"Échantillon : {bad_cegid[:2]}"
                    ),
                    "timestamp": datetime.utcnow().isoformat(),
                })
            if bad_oracle:
                resultat["ecarts"].append({
                    "type_ecart": "LIGNES_CSV_INVALIDES",
                    "article_id": "",
                    "flux_id": flux_id,
                    "source": "oracle",
                    "colonne": "",
                    "valeur_cegid": "",
                    "valeur_oracle": str(len(bad_oracle)),
                    "details": (
                        f"ORACLE : {len(bad_oracle)} ligne(s) mal formée(s) ignorée(s). "
                        f"Échantillon : {bad_oracle[:2]}"
                    ),
                    "timestamp": datetime.utcnow().isoformat(),
                })

            rapport = analyser_rapport(
                resultat["ecarts"], flux_id, db_path
            )

            # Convertir les écarts du format async vers le format standard Excel
            def _normaliser_anomalie(e: dict) -> dict:
                conseil = e.get("conseil") or {}
                severite = conseil.get("severite", "warning").upper()
                return {
                    "error_type":  e.get("type_ecart", ""),
                    "severity":    "CRITIQUE" if severite == "CRITIQUE" else "WARNING",
                    "column":      e.get("colonne", ""),
                    "val_cegid":   str(e.get("valeur_cegid", "") or ""),
                    "val_oracle":  str(e.get("valeur_oracle", "") or ""),
                    "key_str":     e.get("article_id", ""),
                    "explication": conseil.get("titre", ""),
                    "action":      conseil.get("action", ""),
                    "cause":       conseil.get("cause", ""),
                    "type_ecart":    e.get("type_ecart", ""),
                    "article_id":    e.get("article_id", ""),
                    "valeur_cegid":  e.get("valeur_cegid"),
                    "valeur_oracle": e.get("valeur_oracle"),
                    "flux_id":       e.get("flux_id", ""),
                    "timestamp":     e.get("timestamp", ""),
                    "conseil":       conseil,
                }

            anomalies_normalisees = [_normaliser_anomalie(e) for e in rapport["ecarts_enrichis"]]

            # 5. Construire le résultat final
            # Concordance fiable : même formule que generic_comparator.py
            # (lignes sans anomalie critique ni manquante / total)
            n_cegid   = resultat["stats"].get("nb_lignes_cegid", 0)
            n_oracle  = resultat["stats"].get("nb_lignes_oracle", 0)
            n_base    = max(n_cegid, n_oracle, 1)
            n_missing = (resultat["stats"].get("nb_absents_oracle", 0)
                         + resultat["stats"].get("nb_absents_cegid", 0))
            _n_critiques = _n_crit_from_detail if detailed_report else rapport["nb_critique"]
            _n_warnings  = _n_warn_from_detail if detailed_report else rapport["nb_warning"]
            concordance = max(0.0, round((n_base - n_missing - _n_critiques - _n_warnings) / n_base * 100, 1))

            result_data = {
                "flux_id":        flux_id,
                "stats":          resultat["stats"],
                "schema_diff":    resultat["schema_diff"],
                "cles_utilisees": resultat["cles_utilisees"],
                "taux_conformite": rapport["taux_conformite"],
                "resume":         rapport["resume"],
                "action_globale": rapport["action_globale"],
                "nb_critique":    _n_critiques,
                "nb_warning":     _n_warnings,
                "concordance":    concordance,
                "ecarts":         anomalies_normalisees,
            }

            summary = {
                "flux_id":             flux_id,
                "flux_name":           f"Flux {flux_id}",
                "division":            "GLOBAL",
                "divisions_found":     ["GLOBAL"],
                "analyst":             analyst,
                "concordance_moyenne": result_data.get("concordance", 0.0),
                "total_critiques":     result_data.get("nb_critique", 0),
                "total_warnings":      result_data.get("nb_warning", 0),
                "total_anomalies":     result_data.get("nb_critique", 0),
                "n_pairs":             1,
                "pairs": [{
                    "flux_id":        flux_id,
                    "label":          f"Analyse async {flux_id}",
                    "n_cegid":        resultat["stats"].get("nb_lignes_cegid", 0),
                    "n_oracle":       resultat["stats"].get("nb_lignes_oracle", 0),
                    "n_col_cegid":    len(df_cegid.columns),
                    "n_col_oracle":   len(df_oracle.columns),
                    "n_matched":      resultat["stats"].get("nb_lignes_cegid", 0),
                    "n_critiques":    result_data.get("nb_critique", 0),
                    "n_warnings":     result_data.get("nb_warning", 0),
                    "n_missing_oracle": resultat["stats"].get("nb_absents_oracle", 0),
                    "n_missing_cegid":  resultat["stats"].get("nb_absents_cegid", 0),
                    "concordance":    result_data.get("concordance", 0.0),
                    "anomalies_total": len(anomalies_normalisees),
                    "anomalies":      anomalies_normalisees,  # liste complète normalisée
                    "top_error_columns": [],
                }],
                "error": "",
            }

            # 6. Upload dans Azure Blob (anomalies complètes)
            blob_path = None
            try:
                from storage.blob_upload import upload_report_to_blob
                blob_path = upload_report_to_blob(summary, flux_id=flux_id)
                log.info("[WORKER] Blob upload → %s", blob_path)
            except Exception as e:
                log.warning("[WORKER] Blob upload échoué (non bloquant): %s", e)

            # 7. Version allégée pour SQLite (sans anomalies)
            import copy
            local_summary = copy.deepcopy(summary)
            for pair in local_summary.get("pairs", []):
                anoms = pair.get("anomalies", [])
                pair["anomalies_total"]     = len(anoms)
                pair["anomalies_truncated"] = True
                pair["anomalies_in_db"]     = False
                pair["anomalies_in_blob"]   = bool(blob_path)
                if anoms and not pair.get("top_error_columns"):
                    col_counts: dict[str, int] = {}
                    for a in anoms:
                        col = a.get("column") if isinstance(a, dict) else getattr(a, "column", None)
                        if col:
                            col_counts[col] = col_counts.get(col, 0) + 1
                    pair["top_error_columns"] = [
                        {"column": c, "n_errors": n}
                        for c, n in sorted(col_counts.items(), key=lambda x: -x[1])
                    ]
                pair["anomalies"]           = []  # vider pour SQLite
            if blob_path:
                local_summary["blob_path"]       = blob_path
                local_summary["details_storage"] = "azure_blob"

            # 8. Sauvegarder en base
            analysis_id = storage.save_analysis(
                flux_id=flux_id,
                label=f"Analyse async {flux_id} — {job_id[:8]}",
                summary=local_summary,
            )
            result_data["analysis_id"] = analysis_id

            # 8bis. Stocker le chemin du rapport détaillé Excel
            if detailed_excel_path:
                try:
                    storage.update_summary(analysis_id, {
                        **local_summary,
                        "detailed_excel_path": detailed_excel_path,
                    })
                except Exception as _e:
                    log.warning("[WORKER] Sauvegarde detailed_excel_path échouée: %s", _e)

            log.info("[WORKER] Analyse sauvegardée id=%s blob=%s", analysis_id, blob_path)

            # 9. Envoyer alerte si seuils d'alert_threshold dépassés
            try:
                from core.email_alert import send_alert_async

                _n_crit = result_data.get("nb_critique", 0)
                _n_warn = result_data.get("nb_warning", 0)

                # Évaluer alert_threshold du flux (min_critiques / max_warnings)
                _alert_cfg = {}
                try:
                    _alert_cfg = FluxLoader.load(flux_id.upper()).alert_threshold or {}
                except Exception:
                    pass
                _min_crit = int(_alert_cfg.get("min_critiques", 1))
                _max_warn = int(_alert_cfg.get("max_warnings", 9999))

                _should_alert = (_n_crit >= _min_crit) or (_n_warn > _max_warn)
                if not _should_alert:
                    log.info(
                        "[WORKER] Alerte non déclenchée — seuils non atteints "
                        "(critiques=%d/%d, warnings=%d/%d)",
                        _n_crit, _min_crit, _n_warn, _max_warn,
                    )
                else:
                    # Construire un objet compatible avec email_alert._send()
                    class _Anomaly:
                        def __init__(self, d):
                            self.severity   = d.get("severity", "WARNING")
                            self.error_type = d.get("error_type", "")
                            self.key_values = {d.get("column", ""): d.get("key_str", "")}
                            self.val_cegid  = d.get("val_cegid", "")
                            self.val_oracle = d.get("val_oracle", "")
                            self.explication = d.get("explication", "")
                            self.action     = d.get("action", "")

                    class _PairStub:
                        def __init__(self, pair_dict):
                            self.anomalies = [_Anomaly(a) for a in pair_dict.get("anomalies", [])]
                            self.n_cegid = pair_dict.get("n_cegid", 0)
                            self.n_oracle = pair_dict.get("n_oracle", 0)
                            self.n_critiques = pair_dict.get("n_critiques", 0)
                            self.n_warnings = pair_dict.get("n_warnings", 0)
                            self.concordance = pair_dict.get("concordance", 0.0)

                    class _FakeResult:
                        pass

                    fake = _FakeResult()
                    fake.flux_id = flux_id
                    fake.flux_name = summary.get("flux_name", f"Flux {flux_id}")
                    fake.label = f"Analyse async {flux_id}"
                    fake.error = ""
                    fake.divisions_found = summary.get("divisions_found", ["GLOBAL"])
                    fake.total_critiques = _n_crit
                    fake.total_warnings  = _n_warn
                    fake.total_anomalies = _n_crit + _n_warn
                    fake.concordance_moyenne = float(summary.get("concordance_moyenne", 0))
                    fake.pairs = [_PairStub(p) for p in summary.get("pairs", [])]
                    fake.to_dict = lambda: summary

                    send_alert_async(fake, analysis_id=analysis_id)
            except Exception as e:
                log.warning("[WORKER] Alerte échouée (non bloquant): %s", e)

            # 10. Marquer le job comme terminé
            storage.update_job_async(job_id, "DONE", result=result_data)

        except Exception as e:
            import traceback
            log.error("[WORKER] Erreur job %s : %s", job_id, traceback.format_exc())
            storage.update_job_async(job_id, "ERROR", error=str(e))

        finally:
            # 7. Supprimer les fichiers temporaires
            for path in [path_cegid, path_oracle]:
                try:
                    os.remove(path)
                except Exception:
                    pass


# ─────────────────────────────────────────────
# ROUTES FLUX (liste, détail, CRUD)
# ─────────────────────────────────────────────

@flux_bp.route("", methods=["GET"])
@require_auth
def list_flux():
    """Liste tous les flux configurés."""
    try:
        configs = FluxLoader.list_all()
        return jsonify([{
            "flux_id":    c.flux_id,
            "flux_name":  c.flux_name,
            "description": c.description,
            "key_columns": c.key_columns,
            "n_columns":  len(c.columns),
            "active":     c.active,
            "color":      c.display.color,
            "icon":       c.display.icon,
            "direction":  c.direction,
            "frequency":  c.frequency,
        } for c in configs])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flux_bp.route("/<flux_id>", methods=["GET"])
@require_auth
def get_flux(flux_id):
    """Détail d'un flux."""
    try:
        config = FluxLoader.load(flux_id.upper())
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({
        "flux_id":    config.flux_id,
        "flux_name":  config.flux_name,
        "description": config.description,
        "key_columns": config.key_columns,
        "columns":    [{"name": c.name, "type": c.type} for c in config.columns],
        "active":     config.active,
        "color":      config.display.color,
        "icon":       config.display.icon,
    })


@flux_bp.route("", methods=["POST"])
@require_admin
def create_flux():
    """Créer un nouveau flux."""
    data = request.get_json(silent=True) or {}
    try:
        config = FluxLoader.save(data)
        return jsonify({"ok": True, "flux_id": config.flux_id}), 201
    except (ValueError, KeyError) as e:
        return jsonify({"error": str(e)}), 400


@flux_bp.route("/<flux_id>", methods=["PUT"])
@require_admin
def update_flux(flux_id):
    """Mettre à jour un flux."""
    data = request.get_json(silent=True) or {}
    data["flux_id"] = flux_id.upper()
    try:
        config = FluxLoader.save(data)
        return jsonify({"ok": True, "flux_id": config.flux_id})
    except (ValueError, KeyError) as e:
        return jsonify({"error": str(e)}), 400


@flux_bp.route("/<flux_id>", methods=["DELETE"])
@require_admin
def delete_flux(flux_id):
    """Supprimer un flux."""
    FluxLoader.delete(flux_id.upper())
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
# ANALYSE SCHÉMA
# ─────────────────────────────────────────────

@flux_bp.route("/analyser-schema", methods=["POST"])
@require_auth
def analyser_schema():
    """Upload un CSV et retourne les colonnes détectées."""
    if "fichier" not in request.files:
        return jsonify({"erreur": "Aucun fichier envoyé"}), 400

    f = request.files["fichier"]
    if not allowed_file(f.filename):
        return jsonify({"erreur": "Format invalide — CSV uniquement"}), 400

    try:
        df = pd.read_csv(f, sep=None, engine="python", encoding="utf-8-sig")
        analyse = detecter_colonnes(df)
        return jsonify({
            "nb_lignes":          len(df),
            "nb_colonnes":        len(df.columns),
            "colonnes":           analyse["toutes"],
            "cles_suggérées":     analyse["cles_candidates"],
            "valeurs_suggérées":  analyse["valeurs_candidates"],
            "dates_suggérées":    analyse["dates_candidates"],
        })
    except Exception as e:
        return jsonify({"erreur": f"Lecture CSV échouée : {str(e)}"}), 500


# ─────────────────────────────────────────────
# COMPARAISON ASYNC — endpoint principal
# ─────────────────────────────────────────────

@flux_bp.route("/comparer", methods=["POST"])
@require_auth
def comparer():
    """
    Lance une comparaison en arrière-plan.
    Retourne immédiatement un job_id pour suivre l'avancement.
    """
    if "cegid" not in request.files or "oracle" not in request.files:
        return jsonify({"erreur": "Deux fichiers requis : 'cegid' et 'oracle'"}), 400

    flux_id       = request.form.get("flux_id", "flux_inconnu")
    cles_param    = request.form.get("cles")
    valeurs_param = request.form.get("valeurs")
    analyst       = request.form.get("analyst", "unknown")
    division      = request.form.get("division", "GLOBAL")

    cles    = [c.strip() for c in cles_param.split(",")]   if cles_param   else None
    valeurs = [v.strip() for v in valeurs_param.split(",")] if valeurs_param else None

    try:
        tmp_dir = tempfile.mkdtemp()
        path_cegid  = os.path.join(tmp_dir, "file_cegid.csv")
        path_oracle = os.path.join(tmp_dir, "file_oracle.csv")
        request.files["cegid"].save(path_cegid)
        request.files["oracle"].save(path_oracle)
    except Exception as e:
        return jsonify({"erreur": f"Sauvegarde fichiers échouée : {str(e)}"}), 500

    job_id  = str(uuid.uuid4())
    db_path = current_app.config.get("LOCAL_DB_PATH", "instance/flux_monitor.db")

    from storage import get_storage
    get_storage().create_job_async(
        job_id=job_id, flux_id=flux_id, analyst=analyst,
        blob_cegid=path_cegid, blob_oracle=path_oracle,
    )

    from config import settings

    if settings.use_azure:
        # ── Upload Blob + message Queue (pour la future Azure Function) ──
        blob_path_cegid = blob_path_oracle = None
        try:
            from azure.storage.blob import BlobServiceClient
            conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
            blob_client = BlobServiceClient.from_connection_string(conn_str)
            container = blob_client.get_container_client("flux-uploads")

            blob_path_cegid  = f"input/{flux_id}/{job_id}_cegid.csv"
            blob_path_oracle = f"input/{flux_id}/{job_id}_oracle.csv"

            with open(path_cegid, "rb") as fc:
                container.upload_blob(blob_path_cegid, fc, overwrite=True)
            with open(path_oracle, "rb") as fo:
                container.upload_blob(blob_path_oracle, fo, overwrite=True)

            from core.queue_client import enqueue_comparison_job
            enqueue_comparison_job(
                job_id=job_id, flux_id=flux_id,
                blob_path_cegid=blob_path_cegid, blob_path_oracle=blob_path_oracle,
                division=division, analyst=analyst,
            )
            log.info("[QUEUE] Message envoyé pour job=%s", job_id)
        except Exception as e:
            log.warning("[QUEUE] Envoi message échoué (non bloquant): %s", e)
    else:
        # Restauration du traitement local asynchrone par thread
        app = current_app._get_current_object()
        thread = threading.Thread(
            target=_run_analysis_worker,
            args=(app, job_id, flux_id, path_cegid, path_oracle, cles, valeurs, db_path, analyst),
            daemon=True,
        )
        thread.start()
        log.info("[WORKER] Thread d'analyse locale démarré pour job=%s", job_id)

    return jsonify({
        "job_id":  job_id,
        "status":  "PENDING",
        "message": "Analyse lancée en arrière-plan",
    }), 202


# ─────────────────────────────────────────────
# SUIVI DU JOB — endpoint de polling
# ─────────────────────────────────────────────

@flux_bp.route("/jobs/<job_id>", methods=["GET"])
@require_auth
def get_job_status(job_id: str):
    """
    Le frontend appelle cet endpoint toutes les 3 secondes
    pour savoir si l'analyse est terminée.

    Retourne :
      { status: "PENDING" | "RUNNING" | "DONE" | "ERROR",
        result: {...},   ← présent seulement si DONE
        error:  "...",   ← présent seulement si ERROR
      }
    """
    from storage import get_storage
    job = get_storage().get_job_async(job_id)

    if not job:
        return jsonify({"erreur": "Job introuvable"}), 404

    response = {
        "job_id":   job_id,
        "flux_id":  job.get("flux_id"),
        "status":   job.get("status"),
        "analyst":  job.get("analyst"),
        "created_at": job.get("created_at"),
    }

    # Ajouter le résultat seulement si terminé
    if job.get("status") == "DONE" and job.get("result"):
        response["result"] = job["result"]

    # Ajouter l'erreur seulement si échec
    if job.get("status") == "ERROR":
        response["error"] = job.get("error", "Erreur inconnue")

    return jsonify(response)


# ─────────────────────────────────────────────
# HISTORIQUE & STATUTS ÉCARTS
# ─────────────────────────────────────────────

@flux_bp.route("/historique/<flux_id>", methods=["GET"])
@require_auth
def historique(flux_id: str):
    """Retourne les 100 derniers écarts pour un flux."""
    try:
        from storage import get_storage
        rows = get_storage().list_ecarts(flux_id, limit=100)
        return jsonify(rows)
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@flux_bp.route("/ecart/<int:ecart_id>/statut", methods=["PATCH"])
@require_auth
def update_statut(ecart_id: int):
    """Met à jour le statut d'un écart."""
    data   = request.get_json()
    statut = data.get("statut")
    statuts_valides = {"traite", "escalade", "ignore", "nouveau"}

    if statut not in statuts_valides:
        return jsonify({"erreur": f"Statut invalide. Valeurs : {statuts_valides}"}), 400

    try:
        from storage import get_storage
        get_storage().update_ecart_status(ecart_id, statut)
        return jsonify({"succes": True, "ecart_id": ecart_id, "nouveau_statut": statut})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500
