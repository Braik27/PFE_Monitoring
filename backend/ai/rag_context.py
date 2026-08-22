from __future__ import annotations
import json
from datetime import datetime, timedelta
from typing import Optional


def build_rag_context(
    type_ecart_dominant: str,
    flux_id: str,
    n_critiques: int,
    max_cases: int = 3,
    days_back: int = 90,
) -> str:
    try:
        from storage import get_storage
        db = get_storage()
        cases = _search_similar_cases(db, type_ecart_dominant, flux_id, max_cases, days_back)
        if not cases:
            return ""
        return _format_rag_context(cases)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[RAG] Erreur recherche contexte : {e}")
        return ""


def store_resolved_case(
    alert_token: str,
    action_taken: str,
    resolution_hours: Optional[float],
    feedback_score: int,
    feedback_comment: str = "",
) -> None:
    try:
        from storage import get_storage
        db = get_storage()
        alert = db.get_alert_by_token(alert_token)
        if not alert:
            return
        _ensure_feedback_table(db)
        with db._conn() as conn:
            conn.execute("""
                INSERT INTO ia_feedbacks
                  (alert_token, flux_id, flux_name, n_critiques, n_warnings,
                   anomalies_json, action_taken, resolution_hours,
                   feedback_score, feedback_comment, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                alert_token,
                alert.get("flux_id", ""),
                alert.get("flux_name", ""),
                alert.get("n_critiques", 0),
                alert.get("n_warnings", 0),
                alert.get("anomalies_json", "[]"),
                action_taken,
                resolution_hours,
                max(1, min(5, feedback_score)),
                feedback_comment,
                datetime.now().isoformat(),
            ))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[RAG] Erreur stockage feedback : {e}")


def _search_similar_cases(db, type_ecart_dominant, flux_id, max_cases, days_back) -> list[dict]:
    _ensure_feedback_table(db)
    since = (datetime.now() - timedelta(days=days_back)).isoformat()

    try:
        with db._conn() as conn:
            rows = conn.execute("""
                SELECT *
                FROM ia_feedbacks
                WHERE created_at >= ?
                  AND feedback_score >= 3
                ORDER BY
                  CASE WHEN flux_id = ? THEN 0 ELSE 1 END,
                  feedback_score DESC,
                  created_at DESC
                LIMIT 20
            """, (since, flux_id)).fetchall()
    except Exception:
        return []

    if not rows:
        return []

    candidates = []
    for row in rows:
        d = dict(row)
        score = _similarity_score(d, type_ecart_dominant, flux_id)
        if score > 0:
            d["_similarity"] = score
            candidates.append(d)

    candidates.sort(key=lambda x: -x["_similarity"])
    return candidates[:max_cases]


def _similarity_score(case: dict, type_ecart_dominant: str, flux_id: str) -> float:
    score = 0.0
    try:
        anomalies = json.loads(case.get("anomalies_json", "[]"))
        types_historiques = {a.get("type_ecart", "") or a.get("error_type", "") for a in anomalies}
        if type_ecart_dominant in types_historiques:
            score += 3.0
        elif any(t.startswith(type_ecart_dominant[:5]) for t in types_historiques):
            score += 1.0
    except Exception:
        pass
    if case.get("flux_id") == flux_id:
        score += 2.0
    score += (case.get("feedback_score", 3) - 3) * 0.5
    return score


def _format_rag_context(cases: list[dict]) -> str:
    if not cases:
        return ""

    lines = [f"\n\nCas similaires résolus par l'équipe ({len(cases)} cas validés) :"]
    for i, case in enumerate(cases, 1):
        resolution = case.get("resolution_hours")
        resolution_str = f"résolu en {resolution:.1f}h" if resolution else "résolu"
        score = case.get("feedback_score", "?")
        action = case.get("action_taken", "").strip() or "Action manuelle"
        flux = case.get("flux_name", case.get("flux_id", "?"))
        n_crit = case.get("n_critiques", 0)

        lines.append(
            f"\nCas {i} — Flux '{flux}', {n_crit} critiques :\n"
            f"  Action validée : \"{action}\"\n"
            f"  {resolution_str} · Score expert : {score}/5"
        )

    lines.append(
        "\nS'inspirer de ces cas pour formuler des actions concrètes et précises."
    )
    return "\n".join(lines)


def _ensure_feedback_table(db) -> None:
    with db._conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ia_feedbacks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_token      TEXT NOT NULL,
                flux_id          TEXT NOT NULL DEFAULT '',
                flux_name        TEXT NOT NULL DEFAULT '',
                n_critiques      INTEGER DEFAULT 0,
                n_warnings       INTEGER DEFAULT 0,
                anomalies_json   TEXT DEFAULT '[]',
                action_taken     TEXT NOT NULL DEFAULT '',
                resolution_hours REAL,
                feedback_score   INTEGER NOT NULL DEFAULT 3,
                feedback_comment TEXT DEFAULT '',
                created_at       TEXT NOT NULL
            )
        """)