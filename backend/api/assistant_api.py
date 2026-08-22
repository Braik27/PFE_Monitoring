"""
api/assistant_api.py — Assistant IA persistant pour Flux Monitor
Page dédiée /assistant — LLM : Centralized LLM with NVIDIA NIM.
"""

from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime
from flask import Blueprint, jsonify, request, session, make_response, send_from_directory, Response

from api.auth import require_auth
from storage import get_storage
from ai.llm_client import call_llm, strip_llm_error_prefix

log = logging.getLogger("assistant")
assistant_bp = Blueprint("assistant", __name__)

SYSTEM_PROMPT = """Tu es un assistant intelligent intégré dans Flux Monitor, une plateforme de monitoring et réconciliation de flux ERP (Cegid ↔ Oracle) pour TimSoft.

Tu aides les utilisateurs à :
1. CONSULTER : flux configurés, alertes actives, statistiques, historique des analyses
2. AGIR : accuser réception d'une alerte (ACKNOWLEDGED), passer en cours (IN_PROGRESS), résoudre (RESOLVED)
3. COMPRENDRE : expliquer les écarts détectés, les types d'anomalies, les recommandations

Règles importantes :
- Réponds TOUJOURS en français
- Sois précis, professionnel, concis
- Utilise les données temps réel fournies dans le contexte
- Si tu vois des patterns récurrents dans l'historique de l'utilisateur, adapte tes explications
- Pour les actions sur alertes, confirme l'exécution clairement

Format de réponse :
- Utilise des emojis pertinents pour la lisibilité
- Structure avec des listes quand tu présentes plusieurs éléments
- Mets les chiffres importants en évidence avec **gras**
"""

TOPIC_PATTERNS = {
    "alertes":  ["alerte", "alert", "anomalie", "critique", "warning"],
    "flux":     ["flux", "cegid", "oracle", "configuration"],
    "stats":    ["stat", "bilan", "résumé", "total", "combien"],
    "analyses": ["analyse", "comparaison", "rapport", "historique"],
    "actions":  ["prendre en charge", "résoudre", "en cours", "ack"],
}


def _create_conversation(user_id: str, title: str = "Nouvelle conversation") -> int:
    return get_storage().create_conversation(user_id, title)


def _save_message(conv_id: int, role: str, content: str, context_keys: list = None):
    get_storage().save_message(conv_id, role, content, context_keys)


def _get_conversation_messages(conv_id: int, limit: int = 40) -> list:
    return get_storage().get_conversation_messages(conv_id, limit)


def _get_conversation_summary(conv_id: int) -> str:
    return get_storage().get_conversation_summary(conv_id)


def _update_conversation_summary(conv_id: int, summary: str):
    get_storage().update_conversation_summary(conv_id, summary)


def _list_conversations(user_id: str, limit: int = 20) -> list:
    return get_storage().list_conversations(user_id, limit)


def _delete_conversation(conv_id: int, user_id: str):
    get_storage().delete_conversation(conv_id, user_id)


def _track_user_patterns(user_id: str, message: str):
    msg_lower = message.lower()
    for pattern, keywords in TOPIC_PATTERNS.items():
        if any(k in msg_lower for k in keywords):
            try:
                get_storage().save_user_pattern(user_id, pattern)
            except Exception:
                pass


def _get_user_profile(user_id: str) -> str:
    try:
        rows = get_storage().get_user_patterns(user_id)
        if not rows:
            return ""
        top = [f"{r['pattern']} ({r['count']} fois)" for r in rows]
        return f"Profil utilisateur — sujets fréquents : {', '.join(top)}. Adapte tes réponses en conséquence."
    except Exception:
        return ""


def _get_cross_conversation_context(user_id: str, current_message: str) -> str:
    msg_lower = current_message.lower()
    relevant_topics = [p for p, kws in TOPIC_PATTERNS.items() if any(k in msg_lower for k in kws)]
    if not relevant_topics:
        return ""

    try:
        rows = get_storage().list_conversations(user_id, limit=10)
        if not rows:
            return ""

        snippets = []
        for r in rows:
            summary = r.get("summary", "")
            if summary and any(t in summary.lower() for t in relevant_topics):
                date = r.get("updated_at", "")[:10] if r.get("updated_at") else ""
                snippets.append(f"• [{date}] {r.get('title')} : {summary[:200]}")

        if snippets:
            return "Contexte historique pertinent (conversations précédentes) :\n" + "\n".join(snippets[:3])
    except Exception:
        pass
    return ""


def _maybe_summarize_conversation(conv_id: int, user_id: str):
    try:
        conv = get_storage().get_conversation(conv_id, user_id)
        if not conv or conv.get("msg_count", 0) < 30:
            return

        messages = get_storage().get_conversation_messages(conv_id, limit=20)
        if not messages:
            return

        history_text  = "\n".join([f"{r['role'].upper()}: {r['content'][:300]}" for r in messages])
        summary_prompt = f"""Résume en 3-4 phrases les points clés de cette conversation entre un utilisateur et l'assistant Flux Monitor. Sois factuel et concis.

Conversation :
{history_text}

Résumé :"""

        summary = call_llm([
            {"role": "user", "content": summary_prompt}
        ], temperature=0.2, max_tokens=200)

        if summary and not summary.startswith("❌"):
            _update_conversation_summary(conv_id, summary)
            log.info("Conversation %d summarized (%d chars)", conv_id, len(summary))
    except Exception as e:
        log.warning("Summarization failed: %s", e)


def _get_context_data(user_message: str) -> dict:
    db = get_storage()
    ctx = {}
    msg_lower = user_message.lower()

    if any(w in msg_lower for w in ["alerte", "alert", "problème", "anomalie", "critique", "warning"]):
        try:
            alerts = db.list_alerts(limit=20)
            ctx["alertes_recentes"] = [
                {
                    "token":       a.get("token", "")[:12] + "…",
                    "flux_id":     a.get("flux_id"),
                    "statut":      a.get("status"),
                    "n_critiques": a.get("n_critiques", 0),
                    "cree_le":     a.get("created_at", "")[:16] if a.get("created_at") else "",
                }
                for a in (alerts or [])[:10]
            ]
            ctx["total_alertes"] = len(alerts or [])
        except Exception as e:
            ctx["alertes_error"] = str(e)

    if any(w in msg_lower for w in ["stat", "résumé", "bilan", "tableau", "dashboard", "total", "combien"]):
        try:
            analyses = db.list_analyses(limit=200)
            ctx["statistiques"] = {
                "total_analyses": len(analyses or []),
                "analyses_recentes": [
                    {
                        "id":      a.get("id"),
                        "flux_id": a.get("flux_id"),
                        "label":   a.get("label"),
                        "date":    a.get("created_at", "")[:16] if a.get("created_at") else "",
                    }
                    for a in (analyses or [])[:5]
                ],
            }
        except Exception as e:
            ctx["stats_error"] = str(e)

    if any(w in msg_lower for w in ["histori", "analyse", "comparaison", "rapport", "dernier"]):
        try:
            analyses = db.list_analyses(limit=10)
            ctx["historique_analyses"] = [
                {
                    "id":      a.get("id"),
                    "flux_id": a.get("flux_id"),
                    "label":   a.get("label"),
                    "date":    a.get("created_at", "")[:16] if a.get("created_at") else "",
                }
                for a in (analyses or [])
            ]
        except Exception as e:
            ctx["historique_error"] = str(e)

    if any(w in msg_lower for w in ["flux", "configuration", "configur", "disponible", "liste"]):
        try:
            from engine.flux_loader import FluxLoader
            configs = FluxLoader.list_all()
            ctx["flux_configures"] = [
                {"flux_id": c.flux_id, "nom": c.flux_name, "description": c.description, "actif": c.active}
                for c in configs
            ]
        except Exception as e:
            ctx["flux_error"] = str(e)

    # RAG vectoriel
    try:
        from ai.vector_store import retrieve_context
        vec_ctx = retrieve_context(question=user_message, top_k=5)
        if vec_ctx:
            ctx["rag_vectoriel"] = vec_ctx
    except Exception:
        pass

    return ctx


def _detect_action(user_message: str) -> dict | None:
    msg_lower = user_message.lower()
    if any(w in msg_lower for w in ["accuser", "acknowledg", "prendre en charge", "ack"]):
        return {"action": "ACKNOWLEDGED"}
    if any(w in msg_lower for w in ["en cours", "in progress", "traitement", "progress"]):
        return {"action": "IN_PROGRESS"}
    if any(w in msg_lower for w in ["résolu", "resolved", "clôturer", "fermer", "résoudre"]):
        return {"action": "RESOLVED"}
    return None


def _execute_alert_action(token: str, action: str, user: dict) -> dict:
    db = get_storage()
    try:
        alert = db.get_alert_by_token(token)
        if not alert:
            return {"success": False, "message": f"Alerte …{token[-8:]} introuvable."}
        db.update_alert_status(token=token, status=action, updated_by=user.get("username", "assistant"))
        labels = {"ACKNOWLEDGED": "✅ prise en charge", "IN_PROGRESS": "🔧 en cours", "RESOLVED": "✅ résolue"}
        return {
            "success": True,
            "message": f"Alerte **{alert.get('flux_id')}** marquée {labels.get(action, action)}.",
            "flux_id": alert.get("flux_id"),
            "action":  action,
        }
    except Exception as e:
        return {"success": False, "message": f"Erreur : {e}"}


def _call_nvidia_llm_raw(messages: list, max_tokens: int = 1024, temperature: float = 0.6) -> str:
    return call_llm(messages, temperature, max_tokens)


def _call_nvidia_llm(messages: list, context_data: dict, user_profile: str = "", cross_ctx: str = "") -> str:
    enriched = list(messages)

    system_content = SYSTEM_PROMPT
    if user_profile:
        system_content += f"\n\n{user_profile}"
    enriched[0] = {"role": "system", "content": system_content}

    if cross_ctx:
        enriched.insert(-1, {"role": "system", "content": cross_ctx})

    # RAG Vectoriel
    rag_ctx = context_data.pop("rag_vectoriel", "")
    if rag_ctx:
        enriched.insert(-1, {
            "role":    "system",
            "content": (
                "Anomalies historiques similaires trouvées dans la base vectorielle "
                "(utilise ces informations pour répondre avec précision) :\n"
                + rag_ctx
            ),
        })

    if context_data:
        ctx_json = json.dumps(context_data, ensure_ascii=False, indent=2)
        enriched.insert(-1, {
            "role":    "system",
            "content": f"Données temps réel du système :\n```json\n{ctx_json}\n```",
        })

    return strip_llm_error_prefix(call_llm(enriched, temperature=0.6, max_tokens=1024))


def _auto_title(first_message: str) -> str:
    clean = first_message.strip()[:120]
    for keyword, title in [
        ("alerte", "🚨 Gestion des alertes"),
        ("flux",   "📋 Consultation flux"),
        ("stat",   "📊 Statistiques"),
        ("analys", "🔍 Analyse ERP"),
        ("bilan",  "📈 Bilan"),
    ]:
        if keyword in clean.lower():
            return title + f" — {datetime.now().strftime('%d/%m %H:%M')}"
    return (clean[:45] + "…") if len(clean) > 45 else clean


# ── ROUTES FLASK ──────────────────────────────────────────────────────────

@assistant_bp.get("/assistant")
def assistant_page():
    response = make_response(send_from_directory("templates", "assistant.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"]        = "no-cache"
    response.headers["Expires"]       = "0"
    return response


@assistant_bp.post("/api/assistant/chat")
@require_auth
def chat():
    user         = session.get("user", {})
    user_id      = str(user.get("id") or user.get("username") or "anonymous")
    data         = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    conv_id      = data.get("conversation_id")

    if not user_message:
        return jsonify({"error": "Message vide"}), 400

    is_new_conv = conv_id is None
    if is_new_conv:
        title   = _auto_title(user_message)
        conv_id = _create_conversation(user_id, title)
    else:
        conv_id = int(conv_id)

    _save_message(conv_id, "user", user_message)
    _track_user_patterns(user_id, user_message)

    history      = _get_conversation_messages(conv_id, limit=20)
    conv_summary = _get_conversation_summary(conv_id)

    context_data = _get_context_data(user_message)
    user_profile = _get_user_profile(user_id)
    cross_ctx    = _get_cross_conversation_context(user_id, user_message)

    llm_messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if conv_summary:
        llm_messages.append({
            "role":    "system",
            "content": f"Résumé de la conversation (messages anciens compressés) : {conv_summary}",
        })

    for h in history[:-1]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            llm_messages.append({"role": h["role"], "content": h["content"]})

    llm_messages.append({"role": "user", "content": user_message})

    action_detected = _detect_action(user_message)
    action_result   = None
    token_match     = re.search(r"\b([a-f0-9]{32,})\b", user_message)

    if action_detected and token_match:
        token         = token_match.group(1)
        action_result = _execute_alert_action(token, action_detected["action"], user)
        if action_result["success"]:
            context_data["action_executee"] = action_result

    reply = _call_nvidia_llm(llm_messages, context_data, user_profile, cross_ctx)

    if action_result and action_result["success"]:
        reply = f"{action_result['message']}\n\n{reply}"

    _save_message(conv_id, "assistant", reply, list(context_data.keys()))
    _maybe_summarize_conversation(conv_id, user_id)

    return jsonify({
        "reply":           reply,
        "conversation_id": conv_id,
        "is_new_conv":     is_new_conv,
        "context_used":    list(context_data.keys()),
        "action":          action_result,
        "model":           "Cloud LLaMA (NVIDIA NIM)",
        "timestamp":       datetime.utcnow().isoformat(),
    })


@assistant_bp.post("/api/assistant/chat-stream")
@require_auth
def chat_stream():
    user         = session.get("user", {})
    user_id      = str(user.get("id") or user.get("username") or "anonymous")
    data         = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    conv_id      = data.get("conversation_id")

    if not user_message:
        return jsonify({"error": "Message vide"}), 400

    is_new_conv = conv_id is None
    if is_new_conv:
        title   = _auto_title(user_message)
        conv_id = _create_conversation(user_id, title)
    else:
        conv_id = int(conv_id)

    _save_message(conv_id, "user", user_message)
    _track_user_patterns(user_id, user_message)

    history      = _get_conversation_messages(conv_id, limit=20)
    conv_summary = _get_conversation_summary(conv_id)

    context_data = _get_context_data(user_message)
    user_profile = _get_user_profile(user_id)
    cross_ctx    = _get_cross_conversation_context(user_id, user_message)

    llm_messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if conv_summary:
        llm_messages.append({
            "role":    "system",
            "content": f"Résumé de la conversation (messages anciens compressés) : {conv_summary}",
        })

    for h in history[:-1]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            llm_messages.append({"role": h["role"], "content": h["content"]})

    llm_messages.append({"role": "user", "content": user_message})

    action_detected = _detect_action(user_message)
    action_result   = None
    token_match     = re.search(r"\b([a-f0-9]{32,})\b", user_message)

    action_prefix = ""
    if action_detected and token_match:
        token         = token_match.group(1)
        action_result = _execute_alert_action(token, action_detected["action"], user)
        if action_result["success"]:
            context_data["action_executee"] = action_result
            action_prefix = f"{action_result['message']}\n\n"

    enriched = list(llm_messages)
    system_content = SYSTEM_PROMPT
    if user_profile:
        system_content += f"\n\n{user_profile}"
    enriched[0] = {"role": "system", "content": system_content}

    if cross_ctx:
        enriched.insert(-1, {"role": "system", "content": cross_ctx})

    # RAG Vectoriel
    rag_ctx = context_data.pop("rag_vectoriel", "")
    if rag_ctx:
        enriched.insert(-1, {
            "role":    "system",
            "content": (
                "Anomalies historiques similaires trouvées dans la base vectorielle "
                "(utilise ces informations pour répondre avec précision) :\n"
                + rag_ctx
            ),
        })

    if context_data:
        ctx_json = json.dumps(context_data, ensure_ascii=False, indent=2)
        enriched.insert(-1, {
            "role":    "system",
            "content": f"Données temps réel du système :\n```json\n{ctx_json}\n```",
        })

    def generate_response():
        meta_payload = {
            "conversation_id": conv_id,
            "is_new_conv": is_new_conv,
            "context_used": list(context_data.keys()),
            "action": action_result,
            "timestamp": datetime.utcnow().isoformat()
        }
        yield f"data: {json.dumps(meta_payload)}\n\n"

        full_reply = ""
        if action_prefix:
            full_reply += action_prefix
            yield f"data: {json.dumps({'content': action_prefix})}\n\n"

        try:
            from ai.llm_client import call_llm_stream
            for text_chunk in call_llm_stream(enriched, temperature=0.6, max_tokens=1024):
                full_reply += text_chunk
                yield f"data: {json.dumps({'content': text_chunk})}\n\n"
        except Exception as e:
            log.error("Streaming error: %s", e)
            yield f"data: {json.dumps({'content': f'\\n❌ Erreur pendant la génération : {e}'})}\n\n"
        
        _save_message(conv_id, "assistant", full_reply, list(context_data.keys()))
        _maybe_summarize_conversation(conv_id, user_id)

    return Response(generate_response(), mimetype="text/event-stream")


@assistant_bp.get("/api/assistant/conversations")
@require_auth
def list_conversations():
    user    = session.get("user", {})
    user_id = str(user.get("id") or user.get("username") or "anonymous")
    convs   = _list_conversations(user_id, limit=30)
    return jsonify({"conversations": convs})


@assistant_bp.get("/api/assistant/conversations/<int:conv_id>")
@require_auth
def get_conversation(conv_id: int):
    user    = session.get("user", {})
    user_id = str(user.get("id") or user.get("username") or "anonymous")

    conv = get_storage().get_conversation(conv_id, user_id)
    if not conv:
        return jsonify({"error": "Conversation introuvable"}), 404

    messages = _get_conversation_messages(conv_id, limit=100)
    return jsonify({"conversation": conv, "messages": messages})


@assistant_bp.delete("/api/assistant/conversations/<int:conv_id>")
@require_auth
def delete_conversation(conv_id: int):
    user    = session.get("user", {})
    user_id = str(user.get("id") or user.get("username") or "anonymous")
    _delete_conversation(conv_id, user_id)
    return jsonify({"ok": True})


@assistant_bp.get("/api/assistant/status")
@require_auth
def assistant_status():
    status = {
        "model": "Cloud LLaMA (NVIDIA NIM)",
        "ok": bool(os.environ.get("NVIDIA_API_KEY", "").strip()),
    }
    if not status["ok"]:
        status["reason"] = "NVIDIA_API_KEY manquante"

    # RAG vector stats
    try:
        from ai.vector_store import get_stats
        status["rag_vectoriel"] = get_stats()
    except Exception:
        status["rag_vectoriel"] = {"total": 0}

    return jsonify(status)


@assistant_bp.get("/api/assistant/suggestions")
@require_auth
def get_suggestions():
    user    = session.get("user", {})
    user_id = str(user.get("id") or user.get("username") or "anonymous")

    base = [
        "📊 Montre-moi les statistiques globales",
        "🚨 Quelles sont les alertes critiques en cours ?",
        "📋 Liste tous les flux configurés",
        "📈 Résume les 5 dernières analyses",
        "🔍 Explique les types d'écarts détectés",
        "✅ Comment prendre en charge une alerte ?",
        "📅 Quel est le bilan de cette semaine ?",
    ]

    try:
        rows = get_storage().get_user_patterns(user_id, limit=2)
        top_patterns = [r["pattern"] for r in rows]

        personalized = {
            "alertes":  "🚨 Montre-moi toutes mes alertes non traitées",
            "flux":     "📋 Quels flux sont actifs en ce moment ?",
            "stats":    "📊 Donne-moi le bilan complet d'aujourd'hui",
            "analyses": "🔍 Quelle est la dernière analyse effectuée ?",
            "actions":  "✅ Y a-t-il des alertes à prendre en charge ?",
        }
        for p in top_patterns:
            if p in personalized and personalized[p] not in base:
                base.insert(0, personalized[p])
    except Exception:
        pass

    return jsonify({"suggestions": base[:7]})
