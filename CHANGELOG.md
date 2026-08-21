# CHANGELOG — Corrections SLA & Alertes + Bugs flux CEGID/Oracle

Implémentation des recommandations `REVIEW_SENIOR.md`, `SLA_DIAGNOSTIC.md` et corrections bugs identifiés sur données réelles.

## Phase A — Quick wins

### A.1 — Bug résolution alerte
- **Backend** `api/alerts_api.py` : `resolve_alert` lit `comment` (fallback `solution`) au lieu de `solution` seul.
- La transition passe par `transition_alert()`.

### A.2 — Exclusion IGNORED / RESOLVED / CLOSED du scan SLA
- **Backend** `core/scheduler.py` : constantes explicites `SLA_EXCLUDED_STATUSES` et `SLA_MONITORED_STATUSES`.

### A.3 — ESCALATED dans le scan SLA et l'UI
- **Backend** `api/alerts_api.py` : `VALID_STATUSES`, labels CSS.
- **Frontend** `Alerts.tsx` : statuts ESCALATED et CLOSED (labels, filtres, bordures).

### A.4 — Historique d'audit `alert_history`
- **Backend** `core/alert_state_machine.py`, `storage/local.py`, `storage/azure_backend.py` : écriture via `update_alert_status(audit_username=...)`.
- Routes alertes enrichies avec audit.

### A.5 — Email FICHIER_MANQUANT
- **Backend** `core/email_alert.py` : `send_missing_file_alert_async()`.
- **Backend** `api/alerts_api.py` : `create_manual_alert()` déclenche l'email.

### A.6 — SLA dynamique côté frontend (fallback 4h)
- **Frontend** `Alerts.tsx` : `SlaPanel` lit `sla_deadline`, `sla_hours`, `remaining_pct`, `sla_breached`.

---

## Phase B — Améliorations structurelles

### B.1 — SLA dynamique par criticité
- **Nouveau** `core/sla_policy.py` : grille P1_FILE (2h), P1_MASS (4h), P2 (4h), P3 (24h).
- **Backend** `core/sla_monitor.py` : scheduler unifié (surveillance 5 min).
- **Backend** `core/email_alert.py`, `api/alerts_api.py` : `sla_deadline` calculé à la création via `build_sla_meta()`.
- **Backend** `storage/*` : colonnes `flux_type`, `severity_class`, `detected_at`, `expected_hour`, `detection_latency_minutes`.
- **Backend** `app.py` : `init_sla_scheduler()` remplace `start_scheduler()` (ancien scheduler 4h conservé mais deprecated).

### B.2 — Escalade automatique SLA
- **Backend** `core/sla_monitor.py` :
  - Rappel email analyste si `remaining_pct < 20%`.
  - Breach → flag + auto `ESCALATED` si statut `NEW` + notification team_leader via `transition_alert()`.

### B.3 — Dashboard conformité SLA
- **Nouveau** `api/sla_api.py` : `GET /api/sla/metrics`.
- **Nouveau** `frontend/src/components/SlaCompliance/SlaCompliance.tsx` intégré au Dashboard.
- Métriques : conformité %, MTTR, breaches actifs, alertes ignorées, tendances 7j/30j.

### B.4 — Clôture automatique RESOLVED → CLOSED
- **Backend** `core/sla_monitor.py` : job cron quotidien (02:00 UTC), alertes RESOLVED > 48h → CLOSED via `transition_alert()`.
- **Backend** `api/alerts_api.py` : liste active exclut CLOSED (`archived=1` pour l'historique).

### B.5 — Unification machine d'états
- Routes refactorées pour passer par `transition_alert()` :
  - `PATCH /api/alerts/<token>/status`
  - `POST /api/alerts/<token>/track`
  - `execute_alert_action` (email ack/ignore)
  - `alert_page`, `escalate`, `verify` (résolution auto)

### B.6 — Latence de détection
- **Backend** `core/sla_policy.py` : `get_expected_hour_for_flux()`, `compute_detection_latency_minutes()`.
- Enregistrement `expected_hour` + `detection_latency_minutes` à la création d'alerte (depuis `expected_flux` ou registry).

---

## Phase C — Corrections bugs flux CEGID/Oracle (données réelles)

### C.1 — Flux Items : comparaison valeurs manquante
- **Constat** : le flux Items ne comparait que `ITEM_CODE` (clé). Un écart de `DESCRIPTION` ou `STATUS` n'était jamais détecté → faux « Aucun écart détecté ».
- **Correction** :
  - **Registry** `backend/registry/items.json` : ajout de `comparison_rules` pour `DESCRIPTION` (WARNING), `UNIT_PRICE` (CRITIQUE, tolérance 0.01), `STATUS` (CRITIQUE), `CATEGORY` (WARNING), `BRAND` (WARNING).
  - **Moteur** `backend/engine/comparator.py` : normalisation texte avant comparaison pour ne pas rater les écarts de valeurs texte.
  - **Moteur** `backend/engine/generic_comparator.py` : idem pour le chemin pipeline.
- **Test non-régression** : `test_P3_cegid.csv` vs `test_P3_oracle.csv` → 1 warning DESCRIPTION sur item 1304014100, 0 écart sur les 5 autres items.

### C.2 — Flux Customer Balance : clé de matching trop fragile
- **Constat** : la clé composite `CUSTOMER_SITE_NAME + CUSTOMER_SITE_NUMBER` générait ~2180 faux « manquants » car le nom client variait selon le formatage d'export (parenthèses supprimées côté CEGID).
- **Correction** :
  - **Registry** `backend/registry/customerbalance.json` : `key_columns` remplacé par `CUSTOMER_SITE_NUMBER` uniquement. `CUSTOMER_SITE_NAME` déplacé en `comparison_rules` (WARNING) avec normalisation texte.
  - **Moteurs** : ajout d'une normalisation texte (suppression caractères spéciaux, passage en majuscules, collapse des espaces) avant comparaison des valeurs texte.
- **Test non-régression** : avec les fichiers de test, le nombre de manquants passe de ~29 à 17 (12 lignes Oracle mal formées comptées comme absentes + 2 Cegid-only + 3 Oracle-only). Les 12 paires dont le nom diffère seulement par formatage sont maintenant matchées.

### C.3 — Bug transverse : lignes CSV perdues silencieusement
- **Constat** : `on_bad_lines='skip'` supprimait silencieusement les lignes mal formées (ex : `ULTIMATE TRDG & CONTG CO;-Salwa-SCD;5471530;0;0`). Confirmé sur les flux Items et Customer Balance.
- **Correction** :
  - Remplacement de `on_bad_lines='skip'` par un handler callable (`engine='python'`) qui collecte les lignes rejetées.
  - **GenericReader** (`backend/engine/generic_reader.py`) : retourne le décompte et un échantillon des lignes invalides.
  - **Pipeline** (`backend/engine/pipeline.py`) et **Flux API** (`backend/api/flux_api.py`) : création d'une anomalie `LIGNES_CSV_INVALIDES` (WARNING) avec comptage et échantillon, au lieu d'une suppression silencieuse.
  - **Smart Compare API** (`backend/api/smart_compare_api.py`) : même correctif pour cohérence.
- **Test non-régression** : `CustomerBalance_Oracle_1.csv` → anomalie `LIGNES_CSV_INVALIDES` avec 12 lignes détectées.

---

## Fichiers modifiés (résumé)

| Zone | Fichiers |
|------|----------|
| Registry | `backend/registry/items.json`, `backend/registry/customerbalance.json` |
| Backend moteur | `engine/comparator.py`, `engine/generic_comparator.py`, `engine/generic_reader.py`, `engine/pipeline.py` |
| Backend API | `api/flux_api.py`, `api/smart_compare_api.py` |
| Tests | `tests/fixtures/item_flux/*`, `tests/fixtures/customer_balance/*` |

## Vérifications recommandées

1. Flux Items (`/api/analyze` ou pipeline) avec `test_P3_*.csv` → 1 warning DESCRIPTION, 0 écart sur les autres items.
2. Flux Customer Balance avec `CustomerBalance_*.csv` → clé `CUSTOMER_SITE_NUMBER` uniquement, normalisation nom active, ~17 manquants (au lieu de ~29), anomalie `LIGNES_CSV_INVALIDES` pour les 12 lignes Oracle mal formées.
3. `/api/flux/comparer` (async) : même comportement, bad lines remontées dans le rapport.
4. `/api/smart/preview` et `/api/smart/run` : plus de suppression silencieuse de lignes CSV.
5. Comparaison CEGID/Oracle toujours fonctionnelle sur l'ensemble des flux.
