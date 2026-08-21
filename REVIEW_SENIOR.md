# Revue senior — Plateforme de monitoring CEGID vs Oracle

**Date :** 13 août 2026  
**Périmètre audité :** code présent dans le dépôt `Full/` (backend en sous-module Git, frontend React, watcher Python, schéma SQL).  
**Méthode :** lecture statique du code source uniquement — aucune modification.

---

## 1. Architecture générale

### 1.1 Vue d'ensemble

Le projet **Flux Monitor** est une application web de réconciliation de données entre **CEGID** (système A) et **Oracle** (système B). Il n'existe **aucune connexion directe** aux bases CEGID ou Oracle dans le code audité : toute la comparaison repose sur des **fichiers CSV** (ou Excel) importés manuellement ou déposés automatiquement dans un dossier surveillé.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SOURCES DE DONNÉES (hors scope app)                  │
│   CEGID POS/ERP ──export CSV──►  cegid.csv                                   │
│   Oracle ERP    ──export CSV──►  oracle.csv                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                    │                              │
                    ▼                              ▼
     ┌──────────────────────────┐    ┌──────────────────────────┐
     │  Import manuel (UI)      │    │  watcher/watcher.py      │
     │  frontend → POST files   │    │  Watchdog + schedule     │
     └────────────┬─────────────┘    └────────────┬─────────────┘
                  │                                │
                  ▼                                ▼
     ┌────────────────────────────────────────────────────────────┐
     │              Backend Flask (backend/app.py)                 │
     │  Port 8000 — proxy Vite en dev (frontend/vite.config.ts)   │
     ├────────────────────────────────────────────────────────────┤
     │  /api/flux/comparer  → engine/comparator.py (comparer_flux)│
     │  /api/analyze        → engine/pipeline.py (GenericComparator)│
     │  /api/alerts/*       → api/alerts_api.py                   │
     │  APScheduler         → core/scheduler.py (SLA 4h)          │
     └────────────┬───────────────────────────────────────────────┘
                  │
                  ▼
     ┌────────────────────────────────────────────────────────────┐
     │  Stockage : SQLite / MySQL / Azure SQL (storage/)          │
     │  Tables : analyses, alerts, alert_tracking, ecarts, jobs…    │
     └────────────┬───────────────────────────────────────────────┘
                  │
                  ▼
     ┌────────────────────────────────────────────────────────────┐
     │  Frontend React (frontend/) — Dashboard, Alertes, Analyse  │
     │  WebSocket /ws/alerts — notifications temps réel             │
     └────────────────────────────────────────────────────────────┘
```

### 1.2 Points d'entrée des données

| Source | Mécanisme | Fichier / route | Déclenchement |
|--------|-----------|-----------------|---------------|
| **CEGID + Oracle (manuel)** | Upload multipart `cegid` + `oracle` | `POST /api/flux/comparer` (`backend/api/flux_api.py`, L446–526) | Page Analyser via `useAsyncJob.ts` (L86) |
| **CEGID + Oracle (automatique)** | Dépôt fichiers dans `watcher/watch_folder/{FLUX_ID}/` | `watcher/watcher.py` → `trigger_comparison()` (L93–149) → même route `/api/flux/comparer` | Watchdog dès que `cegid.csv` + `oracle.csv` coexistent |
| **Fichier manquant** | Alerte critique sans comparaison | `POST /api/alerts/manual` (`alerts_api.py`, L342–400) | Job planifié watcher `check_missing_files_job()` (L276–319), toutes les minutes |
| **Analyse « pipeline moderne »** | Upload vers `/api/analyze` | `backend/api/analysis.py` (L104–220) → `run_analysis()` | **Non utilisé par le frontend actuel** (aucune référence à `/api/analyze` dans `frontend/`) |
| **Smart Compare** | Mapping colonnes ad hoc | `POST /api/smart/*` | Route `/smart` redirigée vers `/` dans `App.tsx` (L64) — fonctionnalité marginalisée |
| **Config flux** | Fichiers JSON statiques | `backend/registry/*.json` (ex. `sales.json`) | Chargés par `FluxLoader` (`engine/flux_loader.py`) |

**Constat structurant :** le chemin opérationnel principal (UI Analyser + watcher automatique) utilise **`comparer_flux`** (`engine/comparator.py`), tandis qu'un second moteur **`GenericComparator`** (`engine/generic_comparator.py` via `engine/pipeline.py`) existe mais n'est branché que sur `/api/analyze`, endpoint **non consommé par le frontend**.

### 1.3 Chaîne ingestion → comparaison → alerte

1. **Lecture CSV** : pandas, séparateur auto, encodage UTF-8-sig (`flux_api.py`, L61–68).
2. **Pré-traitement optionnel** : `engine/preprocessor.py` selon config registry (ex. filtre `PrefiR` pour CUSTOMERBALANCE).
3. **Clés de rapprochement** : registry `key_columns` → sinon détection heuristique → fallback première colonne (`flux_api.py`, L86–159).
4. **Comparaison** : merge outer sur clés + rang d'occurrence (`comparator.py`, L107–124) ; comparaison valeurs avec tolérance numérique et règles NaN/0 (`comparator.py`, L160–201).
5. **Enrichissement sévérité** : `ai/agent_advisor.py` → `analyser_rapport()` assigne CRITIQUE/WARNING par type d'écart.
6. **Persistance** : `save_analysis()` + écarts dans table `ecarts` (`comparator.py`, L253–258).
7. **Alerte** : si anomalies > 0, `send_alert_async()` (`core/email_alert.py`, L169–214) insère dans `alerts` et envoie email si `ALERT_EMAIL_ENABLED=true` et seuil critiques atteint.

---

## 2. Qualité du code

### 2.1 Duplication et incohérences

| Problème | Preuve |
|----------|--------|
| **Deux moteurs de comparaison coexistent** | `comparator.py` vs `generic_comparator.py` + `pipeline.py` |
| **Deux chemins d'analyse divergents** | UI/watcher → `/api/flux/comparer` ; `/api/analyze` → pipeline (non utilisé UI) |
| **Deux systèmes SLA** | `core/scheduler.py` (actif, 4h fixe) vs `core/sla_monitor.py` (inactif, dynamique) |
| **Machine d'états partiellement appliquée** | `transition_alert()` utilisé seulement dans `POST /resolve` ; `PATCH /status` bypass direct (`alerts_api.py`, L443–461 vs L669–705) |
| **Nommage statuts incohérent** | `ESCALATED` assigné (`alerts_api.py`, L771) mais absent de `VALID_STATUSES` (L103) et de l'UI `STATUS_LABELS` (`Alerts.tsx`, L8–15) |
| **Champs API incohérents** | Frontend envoie `comment` à `/resolve` ; backend lit `solution` (`alerts_api.py`, L685) |
| **Comptage critiques gonflé (pipeline)** | `AnalysisResult.total_critiques` additionne `n_critiques + n_missing_oracle + n_missing_cegid` (`pipeline.py`, L48–50) alors que les manquants sont déjà dans `n_critiques` (`generic_comparator.py`, L87–100) |
| **Sévérité registry ignorée sur chemin principal** | `sales.json` définit `INVOICE_AMOUNT` severity `"ERROR"` (L93–96) — `GenericComparator` compterait `n_critiques` sur `"CRITIQUE"` uniquement (`generic_comparator.py`, L87–88), mais ce registry n'est **pas** utilisé par `comparer_flux` |

### 2.2 Gestion des erreurs

| Scénario | Comportement constaté | Référence |
|----------|----------------------|-----------|
| **Fichier CSV malformé** | `on_bad_lines='skip'` — lignes ignorées silencieusement | `flux_api.py`, L62–67 |
| **Erreur lecture (pipeline)** | Anomalie `ERREUR_LECTURE` CRITIQUE ajoutée au résultat | `pipeline.py`, L143–157 |
| **Échec comparaison async** | Job marqué `ERROR`, fichiers temp supprimés | `flux_api.py`, L319–330 |
| **Oracle indisponible** | Non géré — pas de connecteur DB ; seul cas « fichier manquant » via watcher | `watcher.py`, `should_alert()` |
| **SMTP non configuré** | Log warning, alerte en base quand même | `email_alert.py`, L217–221 ; `scheduler.py`, L43–45 |
| **Bootstrap DB en échec** | App démarre en mode dégradé | `app.py`, L335–338 |
| **Heure limite flux mal configurée** | Pas d'alerte (évite faux positifs) | `watcher.py`, L66–68 |

**Lacune :** aucune stratégie de retry ou dead-letter pour les jobs async en échec ; l'analyste doit relancer manuellement.

### 2.3 Sécurité

| Aspect | État | Référence |
|--------|------|-----------|
| **Authentification** | Session Flask cookie + `@require_auth` | `api/auth.py`, L19–25 |
| **Rôles** | `admin`, `analyst`, `consultant`, `team_leader`, `viewer` (partiel) | `alert_state_machine.py`, L37–44 ; `App.tsx`, L46–51 |
| **Mot de passe admin par défaut** | `admin123` si non configuré | `app.py`, L299–303 |
| **Compte technique watcher** | Mot de passe par défaut `watcher_pass_123` | `app.py`, L305–309 ; `watcher.py`, L47 |
| **SECRET_KEY dev** | Fallback `"dev-secret-change-me"` | `app.py`, L17 |
| **Secrets** | Variables `.env` (SMTP, DB, Azure) — fichier `.env` présent localement, non versionné | `config.py` |
| **Injection SQL** | Requêtes paramétrées dans `storage/local.py` | ex. L591–598 |
| **Suppression alerte** | Tout utilisateur authentifié peut supprimer (`DELETE /api/alerts/<token>`) — pas de garde rôle | `alerts_api.py`, L464–473 |
| **Open redirect partiel** | `next_url` validé (pas de netloc externe) | `auth.py`, L81–83 |

---

## 3. Fiabilité du processus de comparaison

### 3.1 Comment la comparaison est faite aujourd'hui (chemin réel)

**Chemin production (UI + watcher) — `comparer_flux` :**

- **Clé de rapprochement** : colonnes `key_columns` du registry JSON, ou détection auto (`flux_api.py`, L86–129).
- **Gestion doublons** : comptage occurrences par clé des deux côtés ; écart de volume signalé uniquement si présent des deux côtés avec nombres différents (`comparator.py`, L78–105).
- **Appariement ligne à ligne** : `_occ_rank` via `cumcount()` pour éviter produit cartésien (`comparator.py`, L107–124).
- **Tolérance** : comparaison numérique stricte (`==`) sauf NaN/0 équivalents ; pas de tolérance configurable par colonne sur ce moteur (`comparator.py`, L189–199).
- **Fréquence** : à la demande (upload) ou événementielle (watcher) ; job missing-files toutes les **1 minute** (`watcher.py`, L333).
- **Données manquantes** : `absent_oracle` / `absent_cegid` via merge outer (`comparator.py`, L126–150).

**Chemin alternatif (non utilisé par UI) — `GenericComparator` :**

- Règles par flux dans registry : tolérance par colonne (ex. 0.01 sur `INVOICE_AMOUNT`, `sales.json` L93–96).
- Normalisation dates/nombres via `GenericCleaner`.
- Concordance = `(total - manquants) / max(n_cegid, n_oracle)` (`generic_comparator.py`, L103–108).

### 3.2 Trous fonctionnels — écarts réels sans alerte

| Cas | Mécanisme | Fichier / fonction |
|-----|-----------|-------------------|
| **Warnings seuls (0 critique)** | Email non envoyé si `ALERT_MIN_CRITIQUES` ≥ 1 (défaut) ; alerte **non créée** si `total_anomalies == 0` | `email_alert.py`, L22–26, L185–186 |
| **Seuil registry `alert_threshold` ignoré** | `FluxConfig.min_critiques()` existe (`flux_loader.py`, L56) mais jamais appelé dans `email_alert.py` | `flux_loader.py` L56 ; `email_alert.py` L25 |
| **Alerte fichier manquant sans email** | `create_manual_alert()` sauvegarde en base mais n'appelle pas `send_alert_async` | `alerts_api.py`, L342–400 |
| **Lignes CSV corrompues ignorées** | `on_bad_lines='skip'` peut masquer des écarts | `flux_api.py`, L62–67 |
| **Écarts montants classés WARNING (chemin async)** | Types `ECART_*` non listés dans REGLES → fallback `severite_base: warning` (`agent_advisor.py`, L142–150) ; un écart financier peut ne pas compter comme critique |
| **Comparaison sans clés valides** | Fallback première colonne commune — rapprochement potentiellement incorrect sans alerte de config | `flux_api.py`, L127–129 |
| **Anomalies tronquées à 200 dans l'alerte** | Seules 200 anomalies sérialisées | `email_alert.py`, L174 |
| **Analyse OK côté concordance mais warnings** | Si `nb_critique == 0`, pas d'email ; alerte DB créée seulement si `total_anomalies > 0` incluant warnings — email bloqué par `_should_send` | `email_alert.py` |

### 3.3 Faux positifs potentiels

| Cas | Mécanisme | Fichier / fonction |
|-----|-----------|-------------------|
| **HEADER_ID tronqué (Oracle)** | Détecté et classé WARNING avec message explicite — pas un faux positif mais bruit récurrent | `generic_comparator.py`, L319–338 |
| **Vide == 0** | Règle métier : chaînes vides et `"0"` équivalentes (`generic_comparator.py`, L26–33, L288–289) — peut masquer un vrai écart si 0 est une valeur métier |
| **Clé auto-détectée incorrecte** | Heuristique `_ID/_NUM/_CODE` (`flux_api.py`, L104–121) peut produire des rapprochements erronés → écarts fictifs |
| **Double comptage critiques (pipeline)** | Inflation du nombre de critiques → alertes plus agressives | `pipeline.py`, L48–50 |
| **Concordance calculée différemment** | Async : `1 - n_critiques/n_base` (`flux_api.py`, L219–220) vs GenericComparator : basée sur manquants uniquement (`generic_comparator.py`, L103–108) — métriques incohérentes entre chemins |

---

## 4. Constat général

### 4.1 Le projet remplit-il son objectif métier ?

**Partiellement.** L'intention — comparer CEGID et Oracle, détecter les écarts, alerter et traiter sous SLA — est **architecturée** mais l'implémentation présente des **fractures importantes** :

1. **Comparaison** : fonctionnelle sur fichiers CSV avec logique de merge solide (doublons, rang d'occurrence), mais **deux moteurs divergents** dont seul l'ancien est branché sur les parcours utilisateur et automatiques.
2. **Alertes** : création et notification email opérationnelles pour les analyses avec anomalies critiques ; lacunes sur fichiers manquants (pas d'email), warnings seuls, seuils par flux non respectés.
3. **SLA** : promesse UI « 4h » (`Alerts.tsx`, L44–78) cohérente avec le scheduler actif, mais pipeline incomplet (pas de clôture, pas de métriques conformité, escalade manuelle, statut Ignoré mal intégré). Voir `SLA_DIAGNOSTIC.md`.
4. **Traçabilité** : table `alert_history` créée (`sql/create_tables.sql`, L137–146 ; `storage/local.py`, L947+) mais **jamais alimentée** par les transitions courantes.

### 4.2 Écarts intention métier ↔ implémentation

| Intention métier | Réalité code |
|------------------|--------------|
| Comparaison fiable CEGID/Oracle avec règles par flux | Registry JSON riche mais **non utilisé** par le moteur du chemin principal |
| SLA différencié par criticité / type d'écart | SLA **unique 4h** ; code dynamique (`sla_monitor.py`) **non démarré** (`app.py`, L331–332 appelle seulement `start_scheduler`) |
| Escalade automatique si SLA dépassé | Email de dépassement à `ALERT_EMAIL_TO` ; **pas d'escalade de statut** ni réassignation auto |
| Tableau de bord conformité SLA | **Absent** — compte à rebours uniquement sur détail alerte (`Alerts.tsx`, L54–91) |
| Monitoring continu aligné sur SLA | Watcher vérifie fichiers **toutes les minutes** ; comparaison **événementielle** ; pas de garantie de détection sous 4h si fichiers arrivent tard |
| Ignorer une alerte = sortie propre du SLA | Statut `IGNORED` existe mais **inclus dans le scan SLA** (`scheduler.py`, L103 — statut non exclu) |

### 4.3 Structure du dépôt

- **Backend** : sous-répertoire Git indépendant (`backend/.git`), référencé comme submodule dans le dépôt parent (commit `5597ee1`) mais sans `.gitmodules` complet côté parent.
- **Frontend** : React 18 + Vite + TypeScript.
- **Watcher** : service Python autonome (`watcher/watcher.py`).
- **CI** : `azure-pipelines.yml` — build frontend + image Docker `backend/Dockerfile`.

---

## 5. Synthèse des priorités (hors SLA — détail dans SLA_DIAGNOSTIC.md)

| Priorité | Sujet |
|----------|-------|
| **P0** | Unifier les moteurs de comparaison sur le chemin UI/watcher |
| **P0** | Corriger le bug `/resolve` (`comment` vs `solution`) |
| **P1** | Brancher `alert_threshold` du registry sur la création d'alertes |
| **P1** | Envoyer email pour alertes `FICHIER_MANQUANT` |
| **P1** | Appliquer la machine d'états à toutes les transitions |
| **P2** | Supprimer credentials par défaut en production |
| **P2** | Alimenter `alert_history` systématiquement |

---

*Rapport produit par audit statique du code. Toute affirmation est vérifiable dans les fichiers cités.*
