"""
ai/agent_advisor.py
Conseiller intelligent pour les écarts détectés entre Cegid et Oracle.

- Règles métier statiques (fonctionnement hors-ligne garanti)
- Enrichissement IA via NVIDIA NIM (meta/llama-3.3-70b-instruct)

CHANGEMENT v2 :
  - Remplacement d'Ollama par NVIDIA NIM (déjà configuré dans .env)
  - Ajout du RAG vectoriel FAISS (ai/vector_store.py) en priorité
  - Le RAG SQLite (rag_context.py) est conservé en fallback
"""

from datetime import datetime
import os
import json
import urllib.request
import urllib.error
import logging

log = logging.getLogger(__name__)

try:
    from core.monitoring import record_ia_call, track_event
    _MONITORING = True
except ImportError:
    _MONITORING = False


# ─────────────────────────────────────────────────────────────────────────────
# RÈGLES MÉTIER — couvre les types réels du moteur (generic_comparator.py)
# ET les anciens types pour rétrocompatibilité
# ─────────────────────────────────────────────────────────────────────────────

REGLES = {
    # ── Anciens types (rétrocompatibilité) ───────────────────────────────────
    "prix_different": {
        "titre": "Prix différent entre Cegid et Oracle",
        "cause": "Mise à jour tarifaire appliquée dans un système mais pas encore synchronisée dans l'autre.",
        "action": "Vérifier la date de dernière synchronisation tarifaire Oracle. Si < 24h, attendre. Sinon corriger manuellement.",
        "severite_base": "critique", "seuil_warning": 5.0,
    },
    "quantite_differente": {
        "titre": "Quantité différente entre Cegid et Oracle",
        "cause": "Mouvement de stock enregistré dans un système mais pas encore répliqué.",
        "action": "Vérifier les mouvements récents sur cet article dans les deux systèmes. Contrôler les BL ou réceptions en attente.",
        "severite_base": "warning", "seuil_warning": 10.0,
    },
    "absent_oracle": {
        "titre": "Article présent dans Cegid, absent dans Oracle",
        "cause": "Article récemment créé dans Cegid pas encore répliqué vers Oracle.",
        "action": "Si < 48h depuis la création, attendre la prochaine synchro. Sinon vérifier la règle de réplication et les logs.",
        "severite_base": "warning", "seuil_warning": None,
    },
    "absent_cegid": {
        "titre": "Article présent dans Oracle, absent dans Cegid",
        "cause": "Article archivé dans Cegid ou créé directement dans Oracle hors processus standard.",
        "action": "Vérifier si l'article est archivé dans Cegid. Si oui, mettre à jour Oracle. Sinon, article créé hors processus.",
        "severite_base": "critique", "seuil_warning": None,
    },
    "doublon": {
        "titre": "Ligne en doublon détectée",
        "cause": "Une ligne apparaît plusieurs fois dans le même export — erreur d'extraction ou double enregistrement.",
        "action": "Vérifier les lignes dupliquées dans le fichier source et corriger le processus d'extraction.",
        "severite_base": "warning", "seuil_warning": None,
    },
    "montant_different": {
        "titre": "Montant financier différent",
        "cause": "Différence de calcul (taxes, remises, arrondis) ou paramètre comptable non synchronisé.",
        "action": "Vérifier les paramètres de calcul (TVA, remises) dans les deux systèmes. Contrôler les règles comptables récemment modifiées.",
        "severite_base": "critique", "seuil_warning": 1.0,
    },
    "format_invalide": {
        "titre": "Format de données invalide",
        "cause": "Une colonne contient des valeurs dans un format inattendu. Le schéma du fichier a peut-être changé.",
        "action": "Vérifier que le format d'export n'a pas été modifié. Contrôler si une migration du système source a eu lieu.",
        "severite_base": "warning", "seuil_warning": None,
    },
    "valeur_differente": {
        "titre": "Valeur différente entre les deux systèmes",
        "cause": "Différence de données non synchronisée entre Cegid et Oracle.",
        "action": "Comparer les valeurs dans les deux systèmes et identifier la source de référence pour corriger.",
        "severite_base": "warning", "seuil_warning": None,
    },

    # ── Types réels du moteur (generic_comparator.py) ────────────────────────
    "MANQUANT_ORACLE": {
        "titre": "Article présent dans Cegid, introuvable dans Oracle",
        "cause": "L'article existe dans Cegid mais n'a pas été répliqué dans Oracle. Peut être dû à un article récemment créé, une règle de synchro manquante ou un import Oracle en échec.",
        "action": "Vérifier dans Oracle si l'article doit être créé manuellement ou si le job de synchronisation a échoué. Consulter les logs d'intégration Oracle pour cet ITEM_CODE.",
        "severite_base": "critique", "seuil_warning": None,
    },
    "MANQUANT_CEGID": {
        "titre": "Article présent dans Oracle, introuvable dans Cegid",
        "cause": "L'article existe dans Oracle mais est absent de Cegid — article archivé, supprimé, ou créé directement dans Oracle hors du processus standard.",
        "action": "Vérifier si l'article a été archivé dans Cegid. Si oui, mettre à jour Oracle. Sinon, contrôler si l'article a été créé hors processus.",
        "severite_base": "critique", "seuil_warning": None,
    },
    "ECART_CATEGORY": {
        "titre": "Catégorie article différente entre Cegid et Oracle",
        "cause": "Le code catégorie de l'article diffère entre les deux systèmes. Désalignement probable du référentiel catégories lors d'une migration ou mise à jour.",
        "action": "Comparer le référentiel des catégories Cegid vs Oracle. Mettre à jour la table de correspondance et relancer la synchronisation des articles concernés.",
        "severite_base": "warning", "seuil_warning": None,
    },
    "ECART_BRAND": {
        "titre": "Code marque différent entre Cegid et Oracle",
        "cause": "Le code BRAND de l'article ne correspond pas entre les deux systèmes. Référentiel marques non synchronisé.",
        "action": "Vérifier la table de mapping des marques (Cegid → Oracle). Mettre à jour le référentiel Oracle pour aligner les codes marques, puis re-synchroniser.",
        "severite_base": "warning", "seuil_warning": None,
    },
    "ECART_DESCRIPTION": {
        "titre": "Description article différente entre Cegid et Oracle",
        "cause": "La description de l'article présente une différence (souvent un espace ou caractère supplémentaire). Peut causer des problèmes de recherche et d'affichage.",
        "action": "Identifier la source de référence (Cegid ou Oracle) et normaliser la description dans l'autre système. Vérifier si la différence est fonctionnelle ou cosmétique (ex: double espace).",
        "severite_base": "warning", "seuil_warning": None,
    },
    "ECART_UNIT_PRICE": {
        "titre": "Prix unitaire différent entre Cegid et Oracle",
        "cause": "Mise à jour tarifaire appliquée dans un système mais pas encore propagée dans l'autre.",
        "action": "Vérifier la date de dernière synchro tarifaire. Si < 24h, attendre. Sinon, corriger le prix dans le système en retard.",
        "severite_base": "critique", "seuil_warning": 5.0,
    },
    "VALEUR_NULLE": {
        "titre": "Valeur nulle ou manquante détectée",
        "cause": "Un champ obligatoire est vide ou null dans l'un des systèmes.",
        "action": "Identifier le champ concerné et compléter la donnée manquante. Vérifier le processus d'import pour éviter les champs vides à la source.",
        "severite_base": "warning", "seuil_warning": None,
    },
    "ERREUR_LECTURE": {
        "titre": "Erreur de lecture du fichier",
        "cause": "Le fichier source est mal formé, encodage incorrect ou colonnes manquantes.",
        "action": "Vérifier l'encodage du fichier (UTF-8 recommandé) et s'assurer que toutes les colonnes attendues sont présentes. Régénérer l'export depuis le système source.",
        "severite_base": "critique", "seuil_warning": None,
    },
}


def _get_regle(type_ecart: str) -> dict:
    """Retourne la règle pour un type d'écart, avec fallback intelligent."""
    if type_ecart in REGLES:
        return REGLES[type_ecart]
    if type_ecart.startswith("ECART_"):
        col = type_ecart[6:]
        return {
            "titre":         f"Valeur '{col}' différente entre Cegid et Oracle",
            "cause":         f"La colonne {col} présente des valeurs différentes entre les deux systèmes. Désynchronisation du référentiel.",
            "action":        f"Comparer les valeurs de la colonne {col} dans les deux systèmes et identifier la source de référence pour corriger.",
            "severite_base": "warning",
            "seuil_warning": None,
        }
    return {
        "titre":         "Écart détecté",
        "cause":         "Type d'écart non catégorisé.",
        "action":        "Analyser les données des deux systèmes pour identifier l'origine de l'écart.",
        "severite_base": "warning",
        "seuil_warning": None,
    }


# ─────────────────────────────────────────────
# HISTORIQUE : compter les occurrences passées
# ─────────────────────────────────────────────

_historique_cache = {}

def _get_historique(article_id: str, flux_id: str, type_ecart: str, db_path: str = "") -> dict:
    cache_key = (flux_id, type_ecart)
    global _historique_cache
    if cache_key in _historique_cache:
        corrections = _historique_cache[cache_key]
    else:
        try:
            from storage import get_storage
            db = get_storage()
            corrections = db.get_similar_corrections(
                flux_id=flux_id,
                error_type=type_ecart,
                limit=20
            )
            _historique_cache[cache_key] = corrections
        except Exception:
            corrections = []

    if not corrections:
        return {
            "nb_ce_mois":          0,
            "premiere_occurrence": None,
            "est_nouveau":         True,
            "est_recurrent":       False,
        }

    debut_mois = datetime.now().replace(day=1, hour=0, minute=0, second=0).isoformat()
    nb_mois    = sum(1 for c in corrections if c.get("created_at", "") >= debut_mois)
    premiere   = min((c.get("created_at", "") for c in corrections), default=None)
    return {
        "nb_ce_mois":          nb_mois,
        "premiere_occurrence": premiere,
        "est_nouveau":         premiere is None,
        "est_recurrent":       nb_mois >= 3,
    }


# ─────────────────────────────────────────────
# CONSEIL STATIQUE PAR ÉCART (inchangé)
# ─────────────────────────────────────────────

def generer_conseil(
    type_ecart: str,
    article_id: str,
    flux_id: str,
    valeur_cegid=None,
    valeur_oracle=None,
    db_path: str = "instance/flux_monitor.db",
) -> dict:
    regle = _get_regle(type_ecart)

    delta_pct = None
    if valeur_cegid is not None and valeur_oracle is not None:
        try:
            v1 = float(str(valeur_cegid).replace(',', '').replace(' ', ''))
            v2 = float(str(valeur_oracle).replace(',', '').replace(' ', ''))
            if v2 != 0:
                delta_pct = round(abs((v1 - v2) / v2) * 100, 1)
        except (ValueError, TypeError):
            pass

    severite = regle["severite_base"]
    seuil    = regle.get("seuil_warning")
    if seuil and delta_pct is not None:
        severite = "critique" if delta_pct >= seuil else "warning"

    hist   = _get_historique(article_id, flux_id, type_ecart, db_path)
    cause  = regle["cause"]
    action = regle["action"]

    if hist["est_recurrent"]:
        cause  += f" Cet écart est récurrent ({hist['nb_ce_mois']} fois ce mois) — probablement un problème structurel."
        action  = "Escalader : cet écart dépasse le seuil de récurrence. " + action
        severite = "critique"

    if hist["est_nouveau"]:
        contexte = "Première occurrence détectée."
    elif hist["nb_ce_mois"] == 1:
        contexte = "Déjà vu 1 fois ce mois."
    else:
        contexte = f"Déjà vu {hist['nb_ce_mois']} fois ce mois."

    if delta_pct is not None:
        contexte += f" Écart : {delta_pct}%."

    badges = {"critique": "Critique", "warning": "Attention", "info": "Info"}

    return {
        "titre":               regle["titre"],
        "cause":               cause,
        "action":              action,
        "severite":            severite,
        "badge":               badges.get(severite, "Info"),
        "delta_pct":           delta_pct,
        "contexte":            contexte,
        "est_nouveau":         hist["est_nouveau"],
        "est_recurrent":       hist["est_recurrent"],
        "nb_occurrences_mois": hist["nb_ce_mois"],
    }


# ─────────────────────────────────────────────
# ANALYSE GLOBALE (règles statiques — cache reset)
# ─────────────────────────────────────────────

def analyser_rapport(ecarts: list, flux_id: str, db_path: str = "instance/flux_monitor.db") -> dict:
    global _historique_cache
    _historique_cache = {}  # Clear cache before starting the batch analysis

    if not ecarts:
        return {
            "nb_total":        0,
            "nb_critique":     0,
            "nb_warning":      0,
            "taux_conformite": 100.0,
            "ecarts_enrichis": [],
            "resume":          "Aucun écart détecté. Les deux systèmes sont synchronisés.",
            "action_globale":  "Aucune action requise.",
        }

    ecarts_enrichis = []
    nb_critique = 0
    nb_warning  = 0

    for e in ecarts:
        conseil = generer_conseil(
            type_ecart=e.get("type_ecart", "inconnu"),
            article_id=str(e.get("article_id", "")),
            flux_id=flux_id,
            valeur_cegid=e.get("valeur_cegid"),
            valeur_oracle=e.get("valeur_oracle"),
            db_path=db_path,
        )
        e["conseil"] = conseil
        ecarts_enrichis.append(e)

        if conseil["severite"] == "critique":
            nb_critique += 1
        elif conseil["severite"] == "warning":
            nb_warning += 1

    nb_total = len(ecarts)
    taux     = max(0.0, round(100 - (nb_critique * 5 + nb_warning * 1), 1))

    if nb_critique > 0:
        resume         = f"{nb_critique} écart(s) critique(s) détecté(s) — intervention requise."
        action_globale = "Traiter en priorité les écarts critiques avant la prochaine synchronisation."
    elif nb_warning > 0:
        resume         = f"Aucun écart critique. {nb_warning} point(s) d'attention à surveiller."
        action_globale = "Surveiller les écarts warnings lors du prochain import."
    else:
        resume         = "Flux en bon état — quelques informations mineures."
        action_globale = "Aucune action urgente requise."

    return {
        "nb_total":        nb_total,
        "nb_critique":     nb_critique,
        "nb_warning":      nb_warning,
        "taux_conformite": taux,
        "ecarts_enrichis": ecarts_enrichis,
        "resume":          resume,
        "action_globale":  action_globale,
    }


# ─────────────────────────────────────────────────────────────
# ENRICHISSEMENT IA VIA NVIDIA NIM (conservé tel quel)
# + RAG vectoriel FAISS ajouté en priorité
# ─────────────────────────────────────────────────────────────

NVIDIA_API_KEY  = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL    = os.environ.get("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")

_IA_PROMPT = """Tu es consultant ERP spécialisé synchronisation Cegid/Oracle. Réponds en JSON ONLY.

Contexte: {n_crit} écarts critiques, {n_warn} warnings, concordance {concordance}%

Types d'écarts détectés:
{types_resume}

Principaux articles affectés (échantillon):
{ecarts_resume}{rag_context}

JSON ONLY (pas de texte avant ou après):
{{"analyse":"diagnostic en 1 phrase","actions":["action concrète 1","action concrète 2","action concrète 3"],"prevention":"mesure préventive en 1 phrase"}}"""


def generer_conseil_ia(alert: dict, ecarts: list, rapport_statique: dict) -> dict | None:
    """
    Génère un conseil IA via le client LLM centralisé (NVIDIA NIM).
    Enrichi avec le RAG vectoriel FAISS (priorité) puis RAG SQLite (fallback).
    """
    n_crit      = rapport_statique.get("nb_critique", 0)
    n_warn      = rapport_statique.get("nb_warning",  0)
    concordance = alert.get("concordance", 0)
    flux_id     = alert.get("flux_id", "")

    types_count: dict = {}
    for e in ecarts:
        t = e.get("type_ecart", "inconnu")
        types_count[t] = types_count.get(t, 0) + 1
    types_resume = "\n".join(
        f"- {t}: {c} occurrence(s)" for t, c in sorted(types_count.items(), key=lambda x: -x[1])
    )

    type_dominant = max(types_count, key=types_count.get) if types_count else ""

    critiques     = [e for e in ecarts if e.get("conseil", {}).get("severite") == "critique"]
    sample        = (critiques + ecarts)[:8]
    ecarts_resume = "\n".join(
        f"- [{e.get('type_ecart','?')}] {e.get('article_id','?')}"
        for e in sample
    )
    if len(ecarts) > 8:
        ecarts_resume += f"\n... et {len(ecarts)-8} autres écarts"

    # ── RAG vectoriel FAISS (priorité) ───────────────────────
    rag_context = ""
    rag_used    = False

    try:
        from ai.vector_store import retrieve_context
        vec_ctx = retrieve_context(
            question=f"anomalie {type_dominant} flux {flux_id} {n_crit} critiques",
            top_k=5,
            flux_id=flux_id,
        )
        if vec_ctx:
            rag_context = f"\n\nContexte vectoriel (anomalies similaires historiques) :\n{vec_ctx}"
            rag_used    = True
            log.debug("[IA] RAG vectoriel utilisé : %d chars", len(vec_ctx))
    except Exception as e:
        log.debug("[IA] RAG vectoriel indisponible : %s", e)

    # ── RAG SQLite (fallback si vectoriel vide) ───────────────
    if not rag_context:
        try:
            from ai.rag_context import build_rag_context
            sql_ctx = build_rag_context(
                type_ecart_dominant=type_dominant,
                flux_id=flux_id,
                n_critiques=n_crit,
            )
            if sql_ctx:
                rag_context = sql_ctx
                rag_used    = True
        except Exception:
            pass

    prompt = _IA_PROMPT.format(
        ecarts_resume=ecarts_resume,
        types_resume=types_resume,
        n_crit=n_crit,
        n_warn=n_warn,
        concordance=concordance,
        rag_context=rag_context,
    )

    # ── Appel LLM via le client centralisé ─────────────────────
    try:
        from ai.llm_client import call_llm, clean_and_parse_json
        messages = [{"role": "user", "content": prompt}]
        text = call_llm(messages, temperature=0.1, max_tokens=600)
        
        if not text or text.startswith("❌"):
            return None

        result = clean_and_parse_json(text)
        if result:
            if "actions" in result:
                result["actions_prioritaires"] = result["actions"]
            if "prevention" not in result:
                result["prevention"] = ""
            result["confidence"] = 80
            result["rag_used"]   = rag_used
            
            if _MONITORING:
                record_ia_call(success=True)
                track_event("ia_call_success", {"n_critiques": n_crit, "n_warnings": n_warn})
            return result

        return None

    except Exception as e:
        if _MONITORING:
            record_ia_call(success=False)
            track_event("ia_call_error", {"error": str(e)[:100]})
        log.error("[IA] Erreur LLM : %s", e)
        return None