"""
api/smart_compare_async.py — Version asynchrone de smart_compare_api.py

Nouvelles routes :
  POST /api/smart/run-async    → démarre l'analyse en arrière-plan, retourne job_id
  GET  /api/smart/jobs/<id>    → statut et progression du job
  GET  /api/smart/jobs/<id>/result → résultat final (disponible quand DONE)

Le WebSocket /ws/alerts diffuse automatiquement les mises à jour de progression.
Le frontend n'a qu'à écouter les messages WS de type "job_progress".
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import shutil
from flask import Blueprint, jsonify, request, session

from api.auth import require_auth
from core.job_manager import get_job_manager, JobStatus

log = logging.getLogger("smart_async")

smart_async_bp = Blueprint("smart_async", __name__)
UPLOAD_FOLDER  = os.environ.get("UPLOAD_FOLDER", "/tmp/flux_uploads")
SMART_COMPARE_MAX_ROWS = int(os.environ.get("SMART_COMPARE_MAX_ROWS", "100000"))


def _format_size(path: str) -> str:
    try:
        size = os.path.getsize(path)
    except OSError:
        return "taille inconnue"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
        size /= 1024
    return "taille inconnue"


# ─────────────────────────────────────────────────────────────────────
# ROUTE PRINCIPALE : lancer une analyse async
# ─────────────────────────────────────────────────────────────────────

@smart_async_bp.post("/api/smart/run-async")
@require_auth
def smart_run_async():
    """
    Lance la comparaison CSV en arrière-plan.

    Body multipart/form-data :
      - cegid  : fichier CSV Cegid
      - oracle : fichier CSV Oracle
      - config : JSON string (même format que /api/smart/run)

    Retourne immédiatement :
      { "ok": true, "job_id": "abc123", "message": "Analyse lancée..." }
    """
    f_cegid  = request.files.get("cegid")
    f_oracle = request.files.get("oracle")
    if not f_cegid or not f_oracle:
        return jsonify({"error": "Les fichiers cegid et oracle sont requis"}), 400

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

    # ── Sauvegarder les fichiers sur disque (le thread en aura besoin) ──
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    suffix_c = os.path.splitext(f_cegid.filename.lower())[1] or ".csv"
    suffix_o = os.path.splitext(f_oracle.filename.lower())[1] or ".csv"

    tmp_c = tempfile.NamedTemporaryFile(
        delete=False, dir=UPLOAD_FOLDER,
        suffix=suffix_c, prefix="cegid_"
    )
    tmp_o = tempfile.NamedTemporaryFile(
        delete=False, dir=UPLOAD_FOLDER,
        suffix=suffix_o, prefix="oracle_"
    )
    f_cegid.save(tmp_c.name)
    f_oracle.save(tmp_o.name)
    tmp_c.close()
    tmp_o.close()

    username = session.get("user", {}).get("username", "unknown")

    meta = {
        "username":     username,
        "flux_key":     flux_key,
        "file_cegid":   f_cegid.filename,
        "file_oracle":  f_oracle.filename,
        "n_map_cols":   len(mapping),
    }

    jm     = get_job_manager()
    job_id = jm.submit(
        job_type="smart_compare",
        fn=_run_comparison_task,
        fn_args=(tmp_c.name, tmp_o.name, config, username),
        meta=meta,
    )

    log.info("[async] Job %s soumis par %s — %s vs %s",
             job_id, username, f_cegid.filename, f_oracle.filename)

    return jsonify({
        "ok":      True,
        "job_id":  job_id,
        "message": "Analyse lancée en arrière-plan. Suivez la progression via WebSocket.",
        "ws_event": "job_progress",
    })


# ─────────────────────────────────────────────────────────────────────
# ROUTE STATUT : progression du job
# ─────────────────────────────────────────────────────────────────────

@smart_async_bp.get("/api/smart/jobs/<job_id>")
@require_auth
def smart_job_status(job_id: str):
    """
    Retourne l'état courant d'un job.

    Réponse :
    {
      "job_id": "...",
      "status": "RUNNING" | "DONE" | "ERROR" | "PENDING",
      "progress": 70,
      "step_label": "Enrichissement IA...",
      "error": null | "message d'erreur",
      "duration_seconds": null | 12.3
    }
    """
    jm  = get_job_manager()
    job = jm.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable ou expiré"}), 404
    return jsonify(job.to_dict())


# ─────────────────────────────────────────────────────────────────────
# ROUTE RÉSULTAT : récupérer le résultat final
# ─────────────────────────────────────────────────────────────────────

@smart_async_bp.get("/api/smart/jobs/<job_id>/result")
@require_auth
def smart_job_result(job_id: str):
    """
    Retourne le résultat complet quand le job est DONE.
    Supprime le job de la mémoire après récupération.

    Si le job n'est pas encore terminé → 202 Accepted
    Si le job a échoué → 500 avec l'erreur
    """
    jm  = get_job_manager()
    job = jm.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable ou expiré"}), 404

    if job.status == JobStatus.PENDING:
        return jsonify({"status": "PENDING", "message": "Job en attente..."}), 202

    if job.status == JobStatus.RUNNING:
        return jsonify({
            "status":     "RUNNING",
            "progress":   job.progress,
            "step_label": job.step_label,
            "message":    "Analyse en cours...",
        }), 202

    if job.status == JobStatus.ERROR:
        return jsonify({
            "status": "ERROR",
            "error":  job.error,
        }), 500

    # DONE — on retourne le résultat et on libère la mémoire
    result = jm.get_result_and_cleanup(job_id)
    if not result:
        return jsonify({"error": "Résultat introuvable"}), 404

    return jsonify({"ok": True, "status": "DONE", **result})


# ─────────────────────────────────────────────────────────────────────
# TÂCHE LOURDE — s'exécute dans le thread worker
# ─────────────────────────────────────────────────────────────────────

def _run_comparison_task(
    update_progress,
    path_cegid:  str,
    path_oracle: str,
    config:      dict,
    username:    str,
) -> dict:
    """
    Logique complète de comparaison exécutée en arrière-plan.
    update_progress(step_key) est appelé à chaque étape clé.
    Les fichiers temporaires sont supprimés à la fin.
    """
    import time
    import pandas as pd
    from api.smart_compare_api import (
        _read_file_from_path,
        _analyze_columns,
        _run_comparison,
        _save_learned_mapping,
    )

    t_start = time.time()
    mapping       = config.get("mapping", [])
    key_cols_pair = config.get("key_cols", [])
    flux_key      = config.get("flux_key", "")
    key_cols_cegid  = [k["cegid_col"]  for k in key_cols_pair]
    key_cols_oracle = [k["oracle_col"] for k in key_cols_pair]

    try:
        # ── Étape 1 : Lecture ─────────────────────────────────────────
        size_c = _format_size(path_cegid)
        size_o = _format_size(path_oracle)
        update_progress("reading", f"Lecture des fichiers CSV ({size_c} + {size_o}, max {SMART_COMPARE_MAX_ROWS:,} lignes/fichier)...")
        t_read_start = time.time()
        df_c = _read_file_from_path(path_cegid,  max_rows=SMART_COMPARE_MAX_ROWS)
        update_progress("reading", f"Cegid lu : {len(df_c):,} lignes — lecture Oracle...")
        df_o = _read_file_from_path(path_oracle, max_rows=SMART_COMPARE_MAX_ROWS)
        update_progress("reading", f"Fichiers lus : {len(df_c):,} + {len(df_o):,} lignes")
        t_read = time.time() - t_read_start
        log.info(f"[async] Reading files took {t_read:.2f}s")

        # ── Étape 2 : Nettoyage ───────────────────────────────────────
        update_progress("cleaning")
        t_clean_start = time.time()
        df_c = df_c.fillna("").astype(str)
        df_c.columns = [str(c).strip() for c in df_c.columns]
        df_o = df_o.fillna("").astype(str)
        df_o.columns = [str(c).strip() for c in df_o.columns]
        t_clean = time.time() - t_clean_start
        log.info(f"[async] Cleaning took {t_clean:.2f}s")

        # ── Étape 3 : Enrichissement du mapping ───────────────────────
        update_progress("comparing", "Analyse des colonnes et préparation du mapping...")
        cols_c_map = {c["nom"]: c for c in _analyze_columns(df_c)}
        for m in mapping:
            m["cegid_role"] = cols_c_map.get(m.get("cegid_col", ""), {}).get("role", "donnee")

        # ── Étape 4 : Comparaison ─────────────────────────────────────
        update_progress("comparing", f"Comparaison de {len(df_c):,} lignes Cegid vs {len(df_o):,} lignes Oracle...")
        t_comp_start = time.time()
        result = _run_comparison(df_c, df_o, key_cols_cegid, key_cols_oracle, mapping)
        t_comp = time.time() - t_comp_start
        log.info(f"[async] Comparison took {t_comp:.2f}s")

        # ── Étape 5 : Enrichissement IA ───────────────────────────────
        update_progress("ia")
        t_ia_start = time.time()
        ia_result = _try_ia_enrichment(result)
        t_ia = time.time() - t_ia_start
        log.info(f"[async] IA enrichment took {t_ia:.2f}s")
        result["ia_conseil"] = ia_result

        # ── Étape 6 : Sauvegarde mapping appris ───────────────────────
        update_progress("saving")
        if flux_key:
            final_mapping = {
                m["cegid_col"]: m["oracle_col"]
                for m in mapping if m.get("oracle_col")
            }
            _save_learned_mapping(flux_key, final_mapping, username)

        t_total = time.time() - t_start
        log.info(f"[async] Total task time: {t_total:.2f}s (read={t_read:.2f}s, "
                 f"clean={t_clean:.2f}s, comp={t_comp:.2f}s, ia={t_ia:.2f}s)")
        
        update_progress("done")
        return result

    finally:
        # Toujours nettoyer les fichiers temporaires
        for path in (path_cegid, path_oracle):
            try:
                os.unlink(path)
            except Exception:
                pass


def _try_ia_enrichment(result: dict) -> dict:
    """Enrichissement IA des anomalies — non bloquant, retourne {} si Ollama indispo."""
    try:
        from ai.agent_advisor import analyser_rapport
        ecarts = [
            {
                "type_ecart":   a.get("type", "inconnu"),
                "article_id":   a.get("key_str", ""),
                "valeur_cegid": a.get("val_cegid"),
                "valeur_oracle": a.get("val_oracle"),
            }
            for a in result.get("anomalies", [])[:50]  # max 50 pour perf
        ]
        if not ecarts:
            return {}
        rapport = analyser_rapport(ecarts, flux_id="smart_compare")
        return {
            "resume":         rapport.get("resume", ""),
            "action_globale": rapport.get("action_globale", ""),
            "nb_critique":    rapport.get("nb_critique", 0),
            "nb_warning":     rapport.get("nb_warning", 0),
        }
    except Exception as e:
        log.debug("[async] IA enrichment ignoré: %s", e)
        return {}
