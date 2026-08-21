# Diagnostic approfondi du pipeline SLA

**Date :** 13 août 2026  
**Contexte :** monitoring de cohérence CEGID vs Oracle — le SLA n'a de sens que s'il est branché sur la détection réelle des écarts.  
**Méthode :** audit statique, références fichier/fonction/ligne.

---

## 2.1 Compréhension du pipeline actuel

### Cycle de vie complet d'une alerte

```
Écart détecté          Création alerte         Notification           Prise en charge
(comparaison CSV)  →   (token, status NEW)  →  (email + WebSocket) →  (ACKNOWLEDGED)
        │                      │                      │                      │
        │                      │                      │                      ▼
        │                      │                      │              Traitement (IN_PROGRESS)
        │                      │                      │                      │
        │                      │                      │                      ▼
        │                      │                      │              Résolution (RESOLVED)
        │                      │                      │                      │
        │                      │                      │                      ✗ CLOSED (jamais atteint)
        │                      │                      │
        └─ FICHIER_MANQUANT ───┴─ POST /alerts/manual (sans email auto)
```

### Détail par étape

#### Étape 1 — Détection de l'écart (CEGID vs Oracle)

| Attribut | Détail |
|----------|--------|
| **Automatisation** | Oui (watcher + upload UI) pour comparaison ; oui (schedule 1 min) pour fichier manquant |
| **Timestamp** | Horodatage écarts : `comparator.py` L34 ; analyses : `analyses.created_at` |
| **SLA défini ?** | Non à cette étape |
| **Références** | `watcher.py` L266–273 → `POST /api/flux/comparer` ; `flux_api.py` L165–167 → `comparer_flux()` ; `analysis.py` L170 → `run_analysis()` (chemin alternatif non utilisé par UI) |

**Lien métier :** c'est la seule source légitime d'alertes « données » ; le SLA de traitement ne démarre qu'après cette étape.

---

#### Étape 2 — Création de l'alerte

| Attribut | Détail |
|----------|--------|
| **Automatisation** | Oui — thread async `email_alert._send()` |
| **Timestamp** | `alerts.created_at` DEFAULT CURRENT_TIMESTAMP (`sql/create_tables.sql`, L121 ; `storage/local.py`, L591–598) |
| **SLA défini ?** | Non à la création — champs `sla_deadline`, `sla_hours`, `remaining_pct` restent NULL jusqu'à un job SLA (inactif) |
| **Statut initial** | `'NEW'` (`storage/local.py`, L595) |
| **Seuil déclenchement** | Alerte DB si `total_anomalies > 0` ; email si `ALERT_EMAIL_ENABLED=true` ET `total_critiques >= ALERT_MIN_CRITIQUES` (défaut 1) — `email_alert.py`, L22–26, L185–217 |
| **Références** | `core/email_alert.py` L169–214 ; manuel : `alerts_api.py` L342–400 |

**Cas fichier manquant :** alerte créée avec `n_critiques=1`, anomalies `FICHIER_MANQUANT`, **sans envoi email** (`alerts_api.py` — pas d'appel à `send_alert_async`).

---

#### Étape 3 — Notification / escalade

| Attribut | Détail |
|----------|--------|
| **Email initial** | Automatique (si config SMTP + seuil) — liens `/alert/{token}/ack` et `/ignore` (`email_alert.py`, L47–53, L80–86) |
| **WebSocket** | `broadcast_new_alert()` (`app.py`, L70–85) — toast frontend (`useAlertsWebSocket.ts`, L55–68) |
| **Escalade** | **Manuelle** — `POST /api/alerts/<token>/escalate` (`alerts_api.py`, L739–794) |
| **Timestamp escalade** | Entrée `alert_tracking` avec action `ESCALATED_TO:{email}` (`alerts_api.py`, L772–777) |
| **SLA à cette étape** | Aucun recalcul ; statut passe à `ESCALATED` (hors `VALID_STATUSES` L103) |
| **Escalade auto si SLA dépassé** | **Non** — seul un email « SLA DÉPASSÉ » est envoyé (`scheduler.py`, L34–89) |

---

#### Étape 4 — Prise en charge

| Attribut | Détail |
|----------|--------|
| **Automatisation** | Manuelle (UI ou lien email) |
| **Routes** | `PATCH /api/alerts/<token>/status` avec `ACKNOWLEDGED` ; `/alert/<token>/ack` (`app.py`, L231–243 ; `alerts_api.py`, L443–461) |
| **Timestamp** | `alert_tracking.created_at` (`storage/local.py`, L702–708) |
| **Machine d'états** | **Non appliquée** sur PATCH status — mise à jour directe BDD |
| **SLA** | Aucun délai intermédiaire « prise en charge sous X min » ; le SLA global 4h continue depuis `created_at` |

---

#### Étape 5 — Traitement (en cours)

| Attribut | Détail |
|----------|--------|
| **Automatisation** | Manuelle — statut `IN_PROGRESS` via PATCH ou email |
| **Timestamp** | `alert_tracking` |
| **Relance si stagnation** | **Aucune** — pas de rappel si ACKNOWLEDGED/IN_PROGRESS sans progrès |
| **SLA** | Toujours mesuré depuis `created_at` (frontend `SlaPanel`, L57–58) |

---

#### Étape 6 — Ignorer (statut récent)

| Attribut | Détail |
|----------|--------|
| **Automatisation** | Manuelle — bouton « Ignorer » (`Alerts.tsx`, L292–296) ou lien email |
| **Statut** | `IGNORED` dans `VALID_STATUSES` (`alerts_api.py`, L103) |
| **Notification** | Email à l'escalader si action `ESCALATED_TO:` trouvée dans tracking (`alerts_api.py`, L246–303) |
| **Impact SLA** | **Problématique** — `check_sla_breaches()` ne filtre que `NEW`, `ACKNOWLEDGED`, `IN_PROGRESS`, `PENDING` (`scheduler.py`, L103) : **`IGNORED` n'est pas exclu explicitement mais n'est pas dans la liste surveillée** → pas de breach email, mais l'alerte reste « ouverte » dans les listes et **n'est pas comptée comme conforme** |
| **Machine d'états** | Transition `→ IGNORED` autorisée depuis NEW/ACK/IN_PROGRESS/ESCALATED (`alert_state_machine.py`, L26–33) mais **non enforced** via PATCH |

---

#### Étape 7 — Résolution

| Attribut | Détail |
|----------|--------|
| **Automatisation** | Manuelle — `POST /api/alerts/<token>/resolve` |
| **Validation** | `transition_alert()` + commentaire obligatoire (`alert_state_machine.py`, L100–103) |
| **Bug connu** | Backend lit `data.get("solution")` (`alerts_api.py`, L685) ; frontend envoie `{ comment: ... }` (`Alerts.tsx`, L189) → **résolution via `/resolve` échoue (422)** ; fallback PATCH status fonctionne sans machine d'états |
| **Timestamp** | `alert_tracking` action `RESOLVED` |
| **Vérification auto** | `POST /verify` avec fichiers corrigés — résout si 0 écart (`alerts_api.py`, L598–666) |

---

#### Étape 8 — Clôture

| Attribut | Détail |
|----------|--------|
| **Statut `CLOSED`** | Défini dans machine d'états : `RESOLVED → CLOSED` (`alert_state_machine.py`, L31) |
| **Automatisation** | **Aucune** — aucun job ne passe RESOLVED → CLOSED |
| **Timestamp** | N/A |

---

#### Étape 9 — Suivi SLA (backend)

**Système A — ACTIF (`core/scheduler.py`)**

| Attribut | Valeur |
|----------|--------|
| Démarrage | `app.py`, L331–332 : `start_scheduler()` |
| Fréquence | Toutes les **5 minutes** (L135) |
| Règle | Alertes `NEW`/`ACKNOWLEDGED`/`IN_PROGRESS`/`PENDING`, non `sla_breached`, créées il y a **> 4 heures** (L103–107) |
| Actions | `flag_sla_breached=1`, tracking `SLA_BREACHED`, email (`L110–122`) |
| Champs BDD | Met à jour `sla_breached` uniquement via `flag_sla_breached()` — **pas** `sla_deadline` |

**Système B — INACTIF (`core/sla_monitor.py`)**

| Attribut | Valeur |
|----------|--------|
| Démarrage | `init_sla_scheduler()` — **jamais appelé** dans `app.py` |
| Règle | SLA dynamique : base 8–24h selon `flux_type`, modulateurs sévérité/backlog/escalade (`alert_state_machine.py`, L144–210) |
| Actions | `update_sla_fields()`, events WebSocket `alert.sla.breach` / `alert.sla.warn` (L47–67) |
| Cohérence UI | Frontend hardcode **4h** (`Alerts.tsx`, L44) — **incohérent** avec SLA dynamique 8–24h |

---

## 2.2 Incohérences vérifiées

### Le SLA dépend-il du type d'écart et de la criticité ?

**Non, en production.**

- Scheduler actif : **4 heures fixes** pour tous (`scheduler.py`, L107).
- Code dynamique non activé : `BASE_HOURS` par `flux_type` (`comptabilite`, `tresorerie`, `paie`) — **`flux_type` n'est jamais renseigné** sur les alertes (`save_alert()` n'inclut pas ce champ, `storage/local.py`, L585–600).
- Registry flux : `alert_threshold.min_critiques` par flux (`sales.json`, L98–101) — **non utilisé** pour le SLA ni pour le délai.
- Email annonce « SLA 4h » uniforme (`email_alert.py`, L60).

**Conclusion :** un écart financier majeur et un warning HEADER_ID tronqué ont le **même SLA de 4h**.

---

### Que se passe-t-il si le SLA de prise en charge est dépassé ?

| Comportement | Détail |
|--------------|--------|
| Email | Oui — à `ALERT_EMAIL_TO` (`scheduler.py`, L40, L122) |
| Flag BDD | `sla_breached = 1` |
| Escalade auto | **Non** — statut reste inchangé (NEW/ACK/IN_PROGRESS) |
| Réassignation | **Non** |
| Notification WebSocket SLA | **Non** (réservé au monitor inactif) |
| Dashboard conformité | **Non** |

L'alerte **reste en attente indéfiniment** jusqu'à action manuelle — le dépassement SLA est signalé mais **ne change pas le workflow**.

---

### Impact du statut « Ignorée » sur le SLA

| Question | Réponse |
|----------|---------|
| Sort du calcul de breach actif ? | **Partiellement** — IGNORED n'est pas dans la liste L103, donc **pas d'email SLA_BREACHED** |
| Comptée dans stats conformité ? | **Aucune stat de conformité n'existe** |
| Fausse les métriques ? | Reste visible dans listes ; sidebar compte NEW/PENDING/ACK uniquement (`Sidebar.tsx`, L72–74) — IGNORED **non compté** dans badge |
| Machine d'états | IGNORED → ACK/IN_PROGRESS/ESCALATED autorisé (`alert_state_machine.py`, L33) |
| Audit | Pas d'écriture `alert_history` |

**Conclusion :** Ignorée est un **statut terminal de facto** sans exclusion propre du périmètre SLA ni métrique de conformité.

---

### Existe-t-il un tableau de bord de conformité SLA ?

**Non.**

| Composant | Contenu SLA |
|-----------|-------------|
| `Dashboard.tsx` | Concordance, critiques, warnings — **pas de SLA** |
| `Monitoring.tsx` | Perf HTTP, concordance, risque — **pas de SLA** |
| `Alerts.tsx` | Compte à rebours 4h **par alerte sélectionnée** (`SlaPanel`, L54–91) |
| `Reporting.tsx` | Concordance par flux/période — **pas de SLA** |
| API | Pas d'endpoint `/api/sla/*` ou métrique `% conformité` |

**Manque majeur confirmé.**

Champs BDD prévus mais sous-utilisés : `sla_deadline`, `sla_hours`, `remaining_pct`, `sla_breached` (`sql/create_tables.sql`, L117–120).

---

### Cohérence fréquence comparaison vs délais SLA

| Mécanisme | Fréquence |
|-----------|-----------|
| Watcher missing-files | **1 minute** (`watcher.py`, L333) |
| Comparaison auto | **Événementielle** (dépôt fichiers) |
| SLA annoncé | **4 heures** |
| Scheduler SLA check | **5 minutes** |

**Analyse :**

- Si les exports arrivent **une fois par jour** (fréquence affichée « Quotidienne à 08h00 », `sales.json`, L109) et que l'heure limite watcher est 18h–21h (`app.py`, L313–316 seed), la **détection** de l'écart peut avoir jusqu'à ~24h de latence — le SLA de **résolution 4h** ne démarre qu'**après** création de l'alerte, pas après l'heure métier attendue du flux.
- **Pas d'incohérence directe** entre check SLA (5 min) et SLA 4h.
- **Incohérence métier** : promettre résolution sous 4h alors que la comparaison n'est pas continue — acceptable si les fichiers arrivent à heure fixe, **impossible à tenir** si l'écart n'est détecté que le lendemain.

---

## 2.3 Tableau comparatif

| Étape du processus | Comportement actuel constaté | Problème identifié | Recommandation |
|-------------------|------------------------------|-------------------|----------------|
| **Détection écart** | CSV via watcher ou upload ; 2 moteurs de comparaison | Moteur UI ≠ moteur registry riche ; lignes CSV skip silencieux | Unifier sur `GenericComparator` + registry ; alerter sur lignes ignorées |
| **Création alerte** | Token UUID, status NEW, anomalies JSON | Seuil par flux ignoré ; FICHIER_MANQUANT sans email | Utiliser `alert_threshold` ; email pour tous types critiques |
| **Calcul SLA initial** | Aucun à la création ; 4h implicite | `sla_deadline` jamais renseigné au create | Calculer deadline à la création selon criticité/type |
| **Notification** | Email + WebSocket | Email désactivé par défaut (`ALERT_EMAIL_ENABLED`) | Documenter/activer ; pré-alerte à 20% temps restant |
| **Prise en charge** | Manuelle, tracking partiel | Pas de SLA intermédiaire « ack sous 30 min » | Définir SLA multi-étapes |
| **Traitement** | IN_PROGRESS optionnel | Pas de relance si stagnation | Job rappel à J+2h sans progression |
| **Ignorer** | IGNORED + email escalader si applicable | Pas d'exclusion SLA/conformité ; pas d'audit history | Exclure IGNORED des métriques ; tracer dans `alert_history` |
| **Escalade** | Manuelle → ESCALATED + email cible | Pas auto sur breach ; ESCALATED hors VALID_STATUSES | Auto-escalade team_leader si SLA > 75% ; harmoniser statuts |
| **Dépassement SLA** | Email + flag `sla_breached` | Pas d'escalade workflow ; IGNORED/ESCALATED mal gérés | Escalade auto + dashboard rouge |
| **Résolution** | `/resolve` avec machine d'états | Bug champ `solution` vs `comment` | Corriger + PATCH via machine d'états |
| **Clôture** | Statut CLOSED jamais assigné | Alertes RESOLVED infinies | Job auto CLOSED après 24–48h |
| **Audit** | `alert_tracking` seulement | `alert_history` vide | Écrire à chaque transition |
| **Conformité SLA** | Aucune vue | Pas de KPI % respect | Dashboard SLA dédié |
| **SLA dynamique** | Code complet mais inactif | Double implémentation, confusion | Activer `sla_monitor` OU supprimer ; une seule source de vérité |
| **UI SLA** | Compte à rebours 4h hardcodé | Ignore champs BDD `sla_deadline` | Lire API/backend pour countdown réel |

---

## Recommandations priorisées

### A. Quick wins (corrections rapides)

1. **Corriger `/resolve`** — accepter `comment` ou mapper `comment` → `solution` (`alerts_api.py`, L685).
2. **Exclure `IGNORED` et `RESOLVED` du scan SLA** — éviter bruit et clarifier périmètre (`scheduler.py`, L103).
3. **Inclure `ESCALATED` dans le scan SLA** — aujourd'hui ignoré par la condition L103.
4. **Appeler `save_alert_history()`** dans `update_alert_status()` ou `transition_alert()`.
5. **Envoyer email pour alertes manuelles** FICHIER_MANQUANT (`alerts_api.py`, `create_manual_alert`).
6. **Frontend : lire `sla_deadline`/`remaining_pct`** depuis l'API si présents, sinon fallback 4h.

### B. Améliorations structurelles

1. **SLA multi-niveaux par criticité et type d'écart**

   | Type | Exemple | SLA résolution suggéré |
   |------|---------|------------------------|
   | P1 — Fichier absent | FICHIER_MANQUANT | 2h |
   | P1 — Manquant Oracle/Cegid massif | > 10% lignes | 4h |
   | P2 — Écart montant | ECART montant > seuil | 4h |
   | P3 — Warning format | HEADER_ID tronqué | 24h |

   Implémenter via activation de `init_sla_scheduler()` + enrichissement alerte à la création (`flux_type`, `severity_class`).

2. **Escalade automatique** — si `remaining_pct < 20%` : notification analyste ; si `breached` : statut ESCALATED + email team_leader (pas seulement `ALERT_EMAIL_TO`).

3. **Dashboard conformité SLA** — KPIs : `% alertes résolues dans les délais`, `MTTR`, `alertes en breach`, tendance 7/30 jours ; endpoint `GET /api/sla/metrics`.

4. **Clôture automatique** — job quotidien : `RESOLVED` depuis > 48h → `CLOSED`.

5. **Unifier machine d'états** — tout changement de statut via `transition_alert()` ; supprimer bypass PATCH direct.

6. **Brancher comparaison et SLA** — enregistrer `expected_hour` + `detected_at` sur alerte pour mesurer latence détection vs heure métier.

---

## Schéma cible du pipeline SLA idéal (CEGID vs Oracle)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 1. INGESTION (automatisée)                                                    │
│    Watcher détecte cegid.csv + oracle.csv  OR  alerte FICHIER_MANQUANT       │
│    Délai métier : fichiers attendus avant H limite (config expected_flux)       │
│    Responsable : système / exploitation                                       │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 2. COMPARAISON (automatisée, < 15 min après réception)                        │
│    GenericComparator + registry — écarts typés et sévérité P1/P2/P3          │
│    Responsable : moteur / watcher_agent                                       │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 3. CRÉATION ALERTE + SLA CALCULÉ (automatisée)                                │
│    status=NEW ; sla_deadline = f(type_écart, n_critiques, flux_id)              │
│    Email + WebSocket immédiat                                                   │
│    SLA P1: 2h | P2: 4h | P3: 24h                                               │
│    Responsable : système → analyste de permanence (ALERT_EMAIL_TO)            │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 4. PRISE EN CHARGE (manuelle)                                                 │
│    SLA ack : 30 min (P1) / 1h (P2) / 4h (P3)                                  │
│    Si dépassé → rappel email automatique                                       │
│    status → ACKNOWLEDGED                                                       │
│    Responsable : analyste                                                      │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 5. TRAITEMENT (manuel)                                                        │
│    status → IN_PROGRESS ; commentaires dans alert_history                     │
│    Si stagnation > 50% SLA → escalade auto team_leader                         │
│    Responsable : analyste / consultant métier                                  │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 6. DÉPASSEMENT SLA (automatisé)                                               │
│    sla_breached=1 ; email responsable N+1 ; dashboard rouge                   │
│    Escalade auto si non ACK après breach                                       │
│    Responsable : team_leader / admin                                           │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 7. RÉSOLUTION (manuelle, commentaire obligatoire)                             │
│    Option : POST /verify avec fichiers corrigés                                │
│    status → RESOLVED ; MTTR enregistré                                          │
│    Responsable : analyste ayant traité l'écart CEGID/Oracle                    │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 8. CLÔTURE (automatisée après 48h)                                            │
│    status → CLOSED ; exclu des métriques SLA actives                           │
│    Alimentation RAG / ia_feedbacks pour amélioration continue                  │
│    Responsable : système                                                       │
└──────────────────────────────────────────────────────────────────────────────┘

Branche parallèle : IGNORED (décision explicite, audit obligatoire)
  → Exclu du calcul conformité SLA
  → Notification à l'escalader si applicable
  → Réouverture possible vers ACKNOWLEDGED
```

---

## Confirmation du constat initial

**Le pipeline SLA actuel est incomplet et présente des incohérences logiques.** Preuves principales :

1. **Deux implémentations SLA** — une active (4h rigide), une riche mais **jamais démarrée** (`app.py` L331 vs `sla_monitor.py` L75–98).
2. **Promesse UI ≠ backend** — countdown 4h hardcodé (`Alerts.tsx`, L44) vs champs BDD jamais alimentés à la création.
3. **Pas de conséquence workflow au dépassement** — email seulement, pas d'escalade auto (`scheduler.py`).
4. **Cycle de vie incomplet** — CLOSED orphelin (`alert_state_machine.py`, L31).
5. **Pas de métrique conformité** — impossible de répondre à « % alertes traitées dans les délais ».
6. **Déconnexion partielle métier** — alertes fichier manquant sans notification email ; comparaison et SLA sur chemins code différents.

Nuances : les **fondations** existent (tables SLA, machine d'états, tracking, WebSocket, emails, statut IGNORED récent avec notification escalader). Le travail consiste surtout à **connecter, unifier et activer** ce qui est déjà écrit — pas à repartir de zéro.

---

*Document d'audit — aucune modification de code effectuée. Références vérifiables dans le dépôt.*
