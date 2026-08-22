"""
ai/vector_store.py
Base vectorielle locale FAISS + sentence-transformers.

100% gratuit, 100% local, pas de GPU requis.
Tourne sur Azure App Service B1/B2 (CPU uniquement).

RÔLE :
  - Convertir chaque anomalie détectée en vecteur (embedding)
  - Stocker ces vecteurs dans un index FAISS sur disque
  - Retrouver les anomalies similaires à une question utilisateur
  - Fournir ce contexte à l'assistant (assistant_api.py) et au conseiller (agent_advisor.py)

UTILISATION :
  1. Après chaque comparaison → appeler store_anomalies(...)
  2. Dans l'assistant         → appeler retrieve_context(question)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CHEMINS DE STOCKAGE
# Les fichiers sont dans instance/ comme le reste de la base
# ─────────────────────────────────────────────────────────────
VECTOR_DIR  = Path(os.environ.get("VECTOR_DIR", "instance/vectors"))
INDEX_PATH  = VECTOR_DIR / "anomalies.index"
META_PATH   = VECTOR_DIR / "anomalies_meta.json"

# Dimension du modèle all-MiniLM-L6-v2 = 384
EMBED_DIM  = 384
# Seuil de similarité minimum (0.0 à 1.0)
MIN_SCORE   = 0.30

# Limite d'anomalies indexées par analyse (évite les minutes de traitement
# sur les flux volumineux comme CustomerBalance avec 30k+ anomalies).
MAX_INDEX = 1000
# Taille de batch pour l'encode (évite les pics mémoire).
BATCH_SIZE = 256

# ─────────────────────────────────────────────────────────────
# CHARGEMENT PARESSEUX — le modèle se charge une seule fois
# (au premier appel, pas au démarrage de Flask)
# ─────────────────────────────────────────────────────────────
_model = None
_faiss = None  # module faiss chargé dynamiquement


def _get_model():
    """Charge le modèle sentence-transformers une seule fois en mémoire."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            log.info("[VectorStore] Chargement du modèle all-MiniLM-L6-v2...")
            _model = SentenceTransformer("all-MiniLM-L6-v2")
            log.info("[VectorStore] Modèle chargé.")
        except Exception as e:
            log.error("[VectorStore] Impossible de charger le modèle : %s", e)
            raise
    return _model


def _get_faiss():
    """Importe faiss une seule fois."""
    global _faiss
    if _faiss is None:
        import faiss
        _faiss = faiss
    return _faiss


# ─────────────────────────────────────────────────────────────
# GESTION DE L'INDEX FAISS
# ─────────────────────────────────────────────────────────────

def _ensure_dir():
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)


def _load_index():
    """
    Charge l'index FAISS et les métadonnées depuis le disque.
    Crée un index vide si les fichiers n'existent pas encore.
    """
    faiss = _get_faiss()
    _ensure_dir()

    if INDEX_PATH.exists() and META_PATH.exists():
        try:
            index = faiss.read_index(str(INDEX_PATH))
            meta  = json.loads(META_PATH.read_text(encoding="utf-8"))
            return index, meta
        except Exception as e:
            log.warning("[VectorStore] Index corrompu, recréation : %s", e)

    # Index cosinus = Inner Product sur vecteurs normalisés
    index = faiss.IndexFlatIP(EMBED_DIM)
    meta  = []
    return index, meta


def _save_index(index, meta: list):
    """Persiste l'index et les métadonnées sur le disque."""
    faiss = _get_faiss()
    _ensure_dir()
    faiss.write_index(index, str(INDEX_PATH))
    META_PATH.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ─────────────────────────────────────────────────────────────
# API PUBLIQUE
# ─────────────────────────────────────────────────────────────

def store_anomalies(
    analysis_result,          # objet AnalysisResult de engine/pipeline.py
    flux_id: str = "",
    flux_name: str = "",
    division: str = "",
) -> int:
    """
    Convertit toutes les anomalies d'un résultat d'analyse en vecteurs
    et les ajoute à l'index FAISS.

    Paramètres :
        analysis_result : AnalysisResult retourné par run_analysis()
        flux_id         : identifiant du flux (ex: "items", "sales")
        flux_name       : nom lisible (ex: "Articles Cegid/Oracle")
        division        : division concernée (ex: "KWT")

    Retourne le nombre d'anomalies indexées.
    """
    try:
        model = _get_model()
        index, meta = _load_index()
    except Exception as e:
        log.error("[VectorStore] store_anomalies — init échoué : %s", e)
        return 0

    texts   = []
    records = []
    now     = datetime.now().isoformat()
    total   = 0

    for pair in getattr(analysis_result, "pairs", []):
        pair_total = len(getattr(pair, "anomalies", []))
        for anomaly in getattr(pair, "anomalies", []):
            if total >= MAX_INDEX:
                log.info("[VectorStore] Limite MAX_INDEX=%d atteinte (%d anomalies dans l'analyse), "
                         "indexation tronquée pour éviter le timeout.", MAX_INDEX, pair_total)
                break
            text = (
                f"Flux {flux_name or flux_id} "
                f"division {division or 'GLOBAL'} "
                f"le {now[:10]} : "
                f"type={getattr(anomaly, 'error_type', '?')} "
                f"sev={getattr(anomaly, 'severity', '?')} "
                f"cle={getattr(anomaly, 'join_key', '?')} "
                f"col={getattr(anomaly, 'column', '?')} "
                f"cegid={getattr(anomaly, 'val_cegid', '?')} "
                f"oracle={getattr(anomaly, 'val_oracle', '?')} "
                f"exp={getattr(anomaly, 'explication', '')}"
            )
            texts.append(text)
            records.append({
                "text":       text,
                "flux_id":    flux_id,
                "flux_name":  flux_name,
                "division":   division or "GLOBAL",
                "date":       now[:10],
                "error_type": getattr(anomaly, "error_type", ""),
                "severity":   getattr(anomaly, "severity", ""),
                "join_key":   str(getattr(anomaly, "join_key", "") or ""),
                "column":     getattr(anomaly, "column", "") or "",
                "val_cegid":  str(getattr(anomaly, "val_cegid", "") or ""),
                "val_oracle": str(getattr(anomaly, "val_oracle", "") or ""),
                "explication": getattr(anomaly, "explication", "") or "",
            })
            total += 1
        if total >= MAX_INDEX:
            break

    if not texts:
        return 0

    try:
        import numpy as np
        vecs_list = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            vecs = model.encode(batch, normalize_embeddings=True).astype("float32")
            vecs_list.append(vecs)
        vecs = np.concatenate(vecs_list) if len(vecs_list) > 1 else vecs_list[0]

        index, meta = _load_index()
        index.add(vecs)
        meta.extend(records)

        def _persist():
            try:
                _save_index(index, meta)
            except Exception as e:
                log.error("[VectorStore] Erreur sauvegarde differee : %s", e)

        threading.Thread(target=_persist, daemon=True).start()
        log.info("[VectorStore] %d anomalies encodees (total DB: %d) — %d dans l'analyse, "
                 "sauvegarde en arriere-plan.", len(texts), index.ntotal, total)
        return len(texts)
    except Exception as e:
        log.error("[VectorStore] Erreur lors de l'indexation : %s", e)
        return 0


def retrieve_context(
    question: str,
    top_k: int = 6,
    flux_id: Optional[str] = None,
) -> str:
    """
    Recherche les anomalies historiques les plus proches de la question.

    Paramètres :
        question : question de l'utilisateur en langage naturel
        top_k    : nombre maximum de résultats à retourner
        flux_id  : si fourni, booste les résultats du même flux

    Retourne un bloc de texte prêt à injecter dans le prompt LLM.
    """
    try:
        model = _get_model()
        index, meta = _load_index()
    except Exception as e:
        log.warning("[VectorStore] retrieve_context — init échoué : %s", e)
        return ""

    if index.ntotal == 0:
        return ""

    try:
        import numpy as np
        qvec = model.encode([question], normalize_embeddings=True).astype("float32")
        k    = min(top_k * 2, index.ntotal)  # prend 2× pour permettre le re-ranking
        scores, ids = index.search(qvec, k)

        lines = []
        seen_keys = set()  # évite les doublons (même clé, même jour)

        for score, idx in zip(scores[0], ids[0]):
            if idx < 0 or score < MIN_SCORE:
                continue
            r   = meta[idx]
            key = f"{r['error_type']}_{r['join_key']}_{r['date']}"
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Booste légèrement les résultats du même flux
            effective_score = score + (0.1 if flux_id and r.get("flux_id") == flux_id else 0)

            lines.append((effective_score, r))
            if len(lines) >= top_k:
                break

        if not lines:
            return ""

        # Trie par score effectif décroissant
        lines.sort(key=lambda x: -x[0])

        parts = ["Anomalies similaires dans l'historique :"]
        for _, r in lines:
            severity_label = "⚠️ CRITIQUE" if r["severity"] == "CRITIQUE" else "⚡ WARNING"
            parts.append(
                f"• [{r['date']}] {r['flux_name'] or r['flux_id']} / {r['division']} — "
                f"{severity_label} {r['error_type']} | "
                f"clé={r['join_key']} col={r['column']} | "
                f"Cegid={r['val_cegid']} vs Oracle={r['val_oracle']}"
            )
            if r.get("explication"):
                parts.append(f"  → {r['explication'][:120]}")

        return "\n".join(parts)

    except Exception as e:
        log.warning("[VectorStore] Erreur lors de la recherche : %s", e)
        return ""


def get_stats() -> dict:
    """
    Retourne des statistiques sur l'index vectoriel.
    Utilisé par le dashboard admin et l'endpoint /api/assistant/status.
    """
    try:
        _, meta = _load_index()
        if not meta:
            return {"total": 0, "flux": {}, "severity": {}}

        flux_counts = {}
        severity_counts = {}
        for r in meta:
            fx = r.get("flux_id", "?")
            sv = r.get("severity", "?")
            flux_counts[fx]     = flux_counts.get(fx, 0) + 1
            severity_counts[sv] = severity_counts.get(sv, 0) + 1

        return {
            "total":    len(meta),
            "flux":     flux_counts,
            "severity": severity_counts,
            "oldest":   min(r["date"] for r in meta) if meta else None,
            "newest":   max(r["date"] for r in meta) if meta else None,
        }
    except Exception:
        return {"total": 0, "flux": {}, "severity": {}}


def clear_index():
    """Supprime l'index vectoriel (utile pour les tests ou la remise à zéro)."""
    try:
        if INDEX_PATH.exists():
            INDEX_PATH.unlink()
        if META_PATH.exists():
            META_PATH.unlink()
        log.info("[VectorStore] Index supprimé.")
    except Exception as e:
        log.warning("[VectorStore] Erreur lors de la suppression : %s", e)