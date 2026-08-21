# PLAN DE MODIFICATION — Flux Monitor

## Contexte

Deux objectifs :
- **PARTIE A** : Automatisation 100% locale (Azurite, worker local, scheduler) — sans Azure
- **PARTIE B** : SLA à deux niveaux (workflow_status / sla_status), notifications, résolution manuelle

**Contrainte absolue** : Aucun fichier Azure ne sera modifié en écriture.

---

## PARTIE A — Automatisation locale

### A1. Abstraction Queue Backend

**Nouveaux fichiers :**

| Fichier | Description |
|---------|-------------|
| `core/queue_backends/__init__.py` | Package init + factory `get_queue_backend()` |
| `core/queue_backends/base.py` | ABC `QueueBackend` (enqueue, dequeue, peek, delete) |
| `core/queue_backends/local_backend.py` | `AzuriteQueueBackend` — utilise `azure-storage-queue` SDK pointé sur Azurite |
| `core/queue_backends/azure_backend.py` | `AzureQueueBackend` — importe et appelle `core/queue_client.py` SANS le modifier |
| `docker-compose.yml` | Azurite (port 10001/10002/10003) pour stockage local |

**Sélection** : `QUEUE_BACKEND=local|azure` (défaut `local`), lue dans `core/queue_backends/__init__.py`.

**Contrat de message** : dict JSON identique à celui de l'Azure Function existante :
```python
{
    "job_id": str,
    "flux_id": str,
    "blob_path_cegid": str,
    "blob_path_oracle": str,
    "division": str,
    "analyst": str,
    "status": "pending",
}
```

**Azurite docker-compose.yml** (nouveau) :
```yaml
version: "3.9"
services:
  azurite:
    image: mcr.microsoft.com/azure-storage/azurite:latest
    ports:
      - "10001:10001"   # Blob
      - "10002:10002"   # Queue
      - "10003:10003"   # Table
    volumes:
      - azurite-data:/data
volumes:
  azurite-data:
```

### A2. Local Worker

**Nouveau fichier :** `core/local_worker.py`

- Boucle de polling (intervalle configurable, défaut 5s) sur `QueueBackend`
- Déqueue un message, appelle `engine/pipeline.py` pour exécuter l'analyse
- Écrit le résultat via `storage/base.py` (pas via `storage/azure_backend.py`)
- Gère les erreurs (repousse le message en cas d'échec, avec compteur de retries)
- Activé via `QUEUE_BACKEND=local` (seul backend qui tourne en local)
- Thread daemon, démarré depuis `startup.py` ou `scheduler_worker.py`

**Ne touche PAS** à `job_manager.py` ni à `core/queue_client.py`.

### A3. Scheduler Worker

**Nouveau fichier :** `scheduler_worker.py` (racine du backend)

- Utilise APScheduler (déjà en dépendance)
- Job 1 : `monitor_sla_job` (toutes les 5 minutes)
- Job 2 : `daily_report.py` (cron quotidien, ex: 08:00 UTC)
- Activé via `ENABLE_SCHEDULER=true`
- Séparé de tout code Azure
- Ne modifie aucun chemin de démarrage existant

---

## PARTIE B — SLA à deux niveaux + notification + résolution

### B1. Séparation des statuts : workflow_status + sla_status

**Concept** :
- `workflow_status` : piloté par l'humain (NEW → ACKNOWLEDGED → IN_PROGRESS → RESOLVED → CLOSED, ou ESCALATED)
- `sla_status` : calculé automatiquement (ON_TIME → AT_RISK → BREACHED)

**Changement de schéma** (`storage/local.py` → `_create_schema`) :

```sql
ALTER TABLE alerts ADD COLUMN workflow_status VARCHAR(50) DEFAULT NULL;
ALTER TABLE alerts ADD COLUMN sla_status VARCHAR(50) DEFAULT 'ON_TIME';
ALTER TABLE alerts ADD COLUMN severity VARCHAR(50) DEFAULT NULL;
ALTER TABLE alerts ADD COLUMN escalated_by VARCHAR(100) DEFAULT NULL;
ALTER TABLE alerts ADD COLUMN escalated_to VARCHAR(255) DEFAULT NULL;
ALTER TABLE alerts ADD COLUMN escalated_at DATETIME DEFAULT NULL;
ALTER TABLE alerts ADD COLUMN resolved_by VARCHAR(100) DEFAULT NULL;
ALTER TABLE alerts ADD COLUMN resolved_at DATETIME DEFAULT NULL;
ALTER TABLE alerts ADD COLUMN breach_email_sent TINYINT DEFAULT 0;
ALTER TABLE alerts ADD COLUMN breach_report_sent TINYINT DEFAULT 0;
```

`ALTER TABLE ... ADD COLUMN` est idempotent avec MySQL 8+ (erreur "Duplicate column" silencieusement ignorée via `try/except`).

**Migration des données existantes** : copie `status` → `workflow_status` pour toutes les lignes où `workflow_status IS NULL`.

**Fichier modifié :** `storage/local.py`
- `_create_schema()` : ajouter les ALTER TABLE + migration
- `save_alert()` : ajouter `workflow_status` et `sla_status` aux paramètres et à l'INSERT
- `update_alert_status()` : renommé → met à jour `workflow_status` (et garde `status`同步 pour compatibilité)
- Nouvelle méthode `update_sla_status(token, sla_status)` : met à jour uniquement `sla_status`
- `list_alerts()` : filtre sur `workflow_status` au lieu de `status` (avec fallback)
- Nouvelle méthode `get_users_for_flux(flux_id)` : retourne les utilisateurs assignés au flux/division

**Fichier modifié :** `storage/base.py`
- Ajouter signatures abstraites pour les nouvelles méthodes

### B2. Classification par taux de conformité

**Fichier modifié :** `core/sla_policy.py`

Nouvelle fonction `classify_by_concordance(concordance_pct)` :
```python
def classify_by_concordance(concordance_pct: float) -> tuple[str, float]:
    if concordance_pct < 50.0:
        return "CRITICAL", 2.0    # 2h SLA
    elif concordance_pct < 80.0:
        return "WARNING", 4.0     # 4h SLA
    else:
        return None, 0.0          # Pas d'alerte SLA
```

**Configurable** : seuils et délais dans un dict en haut de fichier :
```python
SLA_THRESHOLDS = {
    "critical_max": 50.0,   # < 50% → CRITICAL
    "warning_max": 80.0,    # 50-80% → WARNING
    # ≥ 80% → pas d'alerte
}
SLA_HOURS = {
    "CRITICAL": 2.0,
    "WARNING": 4.0,
}
```

**Repli par flux** : `FluxConfig` dans `registry/*.json` peut surcharger :
```json
{
    "alert_sla": {
        "critical_max": 50.0,
        "warning_max": 80.0,
        "sla_hours": { "CRITICAL": 2.0, "WARNING": 4.0 }
    }
}
```

`FluxConfig` dans `engine/flux_loader.py` : ajouter champ optionnel `alert_sla: dict = {}`.

`build_sla_meta()` dans `core/sla_policy.py` : appeler `classify_by_concordance()` au lieu de `classify_alert()` pour la sévérité. Garder `classify_alert()` pour les cas spéciaux (FICHIER_MANQUANT, etc.).

### B3. Schéma d'alerte étendu (Registry)

**Fichier modifié :** `engine/flux_loader.py`

Ajouter à `FluxConfig` :
```python
alert_sla: dict = {}       # Seuils/délais SLA surchargés par flux
consultant_email: str = "" # Email du consultant par défaut pour ce flux
```

**Fichier modifié :** `registry/items.json` et `registry/customerbalance.json`

Ajouter optionnellement :
```json
{
    "consultant_email": "consultant@example.com",
    "alert_sla": {
        "critical_max": 50.0,
        "warning_max": 80.0,
        "sla_hours": { "CRITICAL": 2.0, "WARNING": 4.0 }
    }
}
```

### B4. Machine à états — workflow_status

**Fichier modifié :** `core/alert_state_machine.py`

Ce fichier EST modifié — voici exactement quoi et pourquoi :

1. **`transition_alert()`** (ligne 106) : lit actuellement `alert["status"]` pour obtenir le statut courant. Doit être changé pour lire `alert["workflow_status"]` (avec fallback sur `alert["status"]` pour les alertes ancien schéma). L'appel `storage.update_alert_status()` doit écrire dans `workflow_status` ET synchroniser `status` pour compatibilité descendante.

2. **`TRANSITIONS` dict** (ligne 26) : les clés restent identiques (NEW, ACKNOWLEDGED, etc.) car elles décrivent les transitions valides du workflow humain. Pas de changement sur les règles elles-mêmes.

3. **`validate_transition()`** (ligne 63) : pas de changement — elle prend `current_status` en entrée et valide contre `TRANSITIONS`. C'est l'appelant (`transition_alert`) qui lui passe le bon statut.

4. **Blocage des transitions automatiques vers ESCALATED et RESOLVED** : ce fichier ne les bloque PAS directement — il fournit les règles. Le blocage est assuré par la **suppression de tout appel automatique** dans `sla_monitor.py` (voir B6) :
   - **ESCALATED** : la ligne 178-194 de `sla_monitor.py` actuelle fait `transition_alert(..., "ESCALATED", ...)` automatiquement au breach. Ce bloc est SUPPRIMÉ dans B6. Plus aucun code ne déclenche ESCALATED sans action humaine explicite via l'endpoint `POST /api/alerts/<token>/escalate`.
   - **RESOLVED** : la ligne 211-237 de `sla_monitor.py` actuelle fait `transition_alert(..., "CLOSED", ...)` automatiquement. Ce bloc est SUPPRIMÉ dans B6. La seule voie vers RESOLVED est l'endpoint `POST /api/alerts/<token>/resolve` (appel humain explicite).
   - **CLOSED** : la seule voie vers CLOSED est le nouvel endpoint `POST /api/alerts/<token>/close` (appel humain explicite).

5. **Audit trail des transitions sla_status** : `transition_alert()` ne gère que `workflow_status`. Les transitions de `sla_status` (ON_TIME → AT_RISK → BREACHED) sont tracées séparément dans `sla_monitor.py` via `save_tracking()` avec des actions dédiées (`SLA_AT_RISK`, `SLA_BREACHED`) — voir B6 pour le détail.

**Champ ajouté au schéma alerts** (B1) : `severity VARCHAR(50)` — sévérité dérivée du taux de conformité au moment de la création (CRITICAL ou WARNING). Persists tout le cycle de vie de l'alerte.

### B5. Nouvel endpoint : CLOSE

**Fichier modifié :** `backend/api/alerts_api.py`

Nouveau endpoint :
```python
@alerts_bp.post("/api/alerts/<token>/close")
@require_auth
def close_alert(token: str):
    """Passe une alerte RESOLVED → CLOSED (archivage)."""
```

- Transition RESOLVED → CLOSED uniquement
- Pas de condition supplémentaire
- Audit trail via `save_tracking`

### B6. SLA Monitor — Refonte

**Fichier modifié :** `core/sla_monitor.py`

**SUPPRIMER :**
- `auto_close_resolved_job()` — résolution est manuelle
- Escalade automatique dans `monitor_sla_job()` (bloc `if status == "NEW": transition → ESCALATED`)
- `_email_sla_breach_escalation()` — l'escalade est manuelle

**MODIFIER `monitor_sla_job()` :**

```python
def monitor_sla_job(storage, event_bus=None):
    # 1. Lister les alertes avec workflow_status dans SLA_MONITORED_STATUSES
    # 2. Pour chaque alerte :
    #    a. Recalculer remaining_pct via recompute_sla_progress()
    #    b. Si remaining_pct < 20% et sla_status != "AT_RISK" :
    #       → update_sla_status(token, "AT_RISK")
    #       → save_tracking(action="SLA_AT_RISK", comment="20% restant")
    #       → save_alert_history(from_status="ON_TIME", to_status="AT_RISK")
    #       → Notification dashboard (WebSocket) renforcée
    #       → PAS d'email
    #    c. Si breached et sla_status != "BREACHED" :
    #       → update_sla_status(token, "BREACHED")
    #       → save_tracking(action="SLA_BREACHED", comment="SLA depasse")
    #       → save_alert_history(from_status="AT_RISK" ou "ON_TIME", to_status="BREACHED")
    #       → flag_sla_breached(token)
    #       → Si breach_email_sent == 0 :
    #           → Envoyer email breach aux utilisateurs assignés
    #           → UPDATE breach_email_sent = 1
    #       → Si breach_report_sent == 0 :
    #           → Générer rapport détaillé (mode single-alert via detailed_report.py)
    #           → Envoyer par email au consultant
    #           → UPDATE breach_report_sent = 1
```

**Traçabilité complète de TOUTES les transitions** :

| Transition | Tracée via | Table |
|------------|-----------|-------|
| workflow_status (tout) | `save_tracking()` + `save_alert_history()` | alert_tracking + alert_history |
| sla_status ON_TIME → AT_RISK | `save_tracking(action="SLA_AT_RISK")` + `save_alert_history(from="ON_TIME", to="AT_RISK")` | alert_tracking + alert_history |
| sla_status AT_RISK → BREACHED | `save_tracking(action="SLA_BREACHED")` + `save_alert_history(from="AT_RISK", to="BREACHED")` | alert_tracking + alert_history |
| sla_status ON_TIME → BREACHED (direct) | `save_tracking(action="SLA_BREACHED")` + `save_alert_history(from="ON_TIME", to="BREACHED")` | alert_tracking + alert_history |

Les deux tables (tracking + history) sont alimentées pour chaque transition sla_status. `alert_history` enregistre `from_status` / `to_status` pour pouvoir reconstruire la chronologie complète.

**AJOUTER : `_email_sla_breach()`**
- Email à la liste des utilisateurs actifs/assignés au flux/division
- Idempotent via flag `breach_email_sent`
- Résolution du destinataire : `consultant_email` registry → `DEFAULT_CONSULTANT_EMAIL` env → log warning

**AJOUTER : `_send_breach_report()`**
- Génère un rapport détaillé pour UNE alerte (mode single-alert de `detailed_report.py`)
- Envoyé par email au consultant
- Idempotent via flag `breach_report_sent`

### B7. Mode "single alert" dans detailed_report.py

**Fichier modifié :** `engine/detailed_report.py`

Ajouter un paramètre optionnel `single_alert_token: str = None` à `build_detail_report()` :
- Si fourni, ne traiter que les lignes correspondant à cette alerte
- Utiliser les anomalies de l'alerte pour filtrer les lignes pertinentes du merged DataFrame
- Sinon, comportement identique actuel (tout le flux)

**Ou** (alternative plus simple) : créer `build_single_alert_report(alert: dict)` dans le même fichier :
- Prend une alerte complète (avec anomalies)
- Retourne un dict structuré : valeurs Cegid vs Oracle, sévérité, colonne concernée, historique des transitions
- Pas besoin de recharger tout le merged DataFrame
- Utilisable directement par `_send_breach_report()`

**Recommandation** : l'alternative 2 (fonction dédiée) est plus simple et ne casse pas l'interface existante.

### B8. Notification Dashboard (WebSocket)

**Fichier modifié :** `core/sla_monitor.py` (et possiblement `core/email_alert.py`)

Dans `monitor_sla_job()` :
- Quand `sla_status` passe à `AT_RISK` → publier event `alert.sla.at_risk` via event_bus
- Quand `sla_status` passe à `BREACHED` → publier event `alert.sla.breach` via event_bus

Le frontend (`frontend/src/pages/Alerts/Alerts.tsx`) écoute déjà le WebSocket `/ws/alerts` — les nouveaux events seront automatiquement reçus.

Pour les alertes critiques/warnings à la création :
- `email_alert.py` → `_send()` : publier event `alert.new` via WebSocket (déjà fait via `broadcast_new_alert`)
- En cas de rafale : regrouper en UNE notification agrégée (plutôt qu'une par anomalie)

### B9. Notification Groupée (Rafale) — IMPLÉMENTÉ dans cette livraison

**Règle du cahier des charges** : en cas de rafale/anomalie de masse, regrouper en UNE notification agrégée plutôt qu'une par anomalie.

**Fichier modifié :** `core/email_alert.py`

**Mécanisme** : un buffer partagé avec un timer de flush à 30 secondes.

```python
# Buffer partagé (module-level)
_alert_buffer = []          # Liste de dicts {token, flux_id, flux_name, n_critiques, n_warnings, severity}
_alert_buffer_lock = threading.Lock()
_alert_buffer_timer = None

def _flush_alert_buffer():
    """Envoie UNE notification agrégée puis vide le buffer."""
    with _alert_buffer_lock:
        if not _alert_buffer:
            return
        batch = list(_alert_buffer)
        _alert_buffer.clear()
    # Construire et broadcaster la notification agrégée
    total_crit = sum(a["n_critiques"] for a in batch)
    total_warn = sum(a["n_warnings"] for a in batch)
    flux_ids = list(set(a["flux_id"] for a in batch))
    broadcast_event("alerts.batch", {
        "count": len(batch),
        "total_critiques": total_crit,
        "total_warnings": total_warn,
        "flux_ids": flux_ids,
        "alerts": batch,       # Détail de chaque alerte pour le frontend
    })

def _buffer_alert(token, flux_id, flux_name, n_critiques, n_warnings, severity):
    """Ajoute une alerte au buffer et (ré)initialise le timer de flush."""
    global _alert_buffer_timer
    with _alert_buffer_lock:
        _alert_buffer.append({
            "token": token, "flux_id": flux_id, "flux_name": flux_name,
            "n_critiques": n_critiques, "n_warnings": n_warnings, "severity": severity,
        })
    # (Ré)initialiser le timer de 30s
    if _alert_buffer_timer:
        _alert_buffer_timer.cancel()
    _alert_buffer_timer = threading.Timer(30.0, _flush_alert_buffer)
    _alert_buffer_timer.daemon = True
    _alert_buffer_timer.start()
```

**Dans `_send()`** : remplacer l'appel `broadcast_new_alert()` direct par `_buffer_alert()`.

**Frontend** : écouter l'event `alerts.batch` sur le WebSocket `/ws/alerts`. Afficher un résumé groupé : "N nouvelles alertes : X critiques, Y warnings sur Z flux". Clic pour expandre et voir chaque alerte individuelle.

### B10. Arrêt du chrono SLA à RESOLVED

**Fichier modifié :** `core/sla_monitor.py`

Dans `monitor_sla_job()` :
- Les alertes avec `workflow_status = "RESOLVED"` sont déjà exclues par `SLA_EXCLUDED_STATUSES`
- Vérification : `SLA_MONITORED_STATUSES` ne contient PAS "RESOLVED" → OK
- Pas de changement nécessaire ici (c'est déjà le comportement)

**Vérification** : quand un analyste passe une alerte à RESOLVED via l'endpoint `/resolve`, le SLA monitor ne la traite plus → le chrono s'arrête immédiatement.

### B11. Variables d'environnement nouvelles

| Variable | Description | Défaut |
|----------|-------------|--------|
| `QUEUE_BACKEND` | `local` (Azurite) ou `azure` | `local` |
| `ENABLE_SCHEDULER` | Active le scheduler APScheduler | `false` |
| `DEFAULT_CONSULTANT_EMAIL` | Email consultant par défaut pour breach reports | (vide) |
| `AZURITE_CONNECTION_STRING` | Connexion Azurite | `DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;...` |
| `SLA_POLL_INTERVAL_SECONDS` | Intervalle polling worker local | `5` |

### B12. Modifications registry JSON

Ajouter champs optionnels à `FluxConfig` et aux JSON de registry :
- `consultant_email` : string, email du consultant pour les breach reports
- `alert_sla` : dict, surcharge des seuils/délais SLA par flux

---

## Récapitulatif des fichiers

### Fichiers CRÉÉS

| Fichier | Description |
|---------|-------------|
| `core/queue_backends/__init__.py` | Package + factory `get_queue_backend()` |
| `core/queue_backends/base.py` | ABC `QueueBackend` |
| `core/queue_backends/local_backend.py` | `AzuriteQueueBackend` |
| `core/queue_backends/azure_backend.py` | `AzureQueueBackend` (wrapper queue_client.py) |
| `core/local_worker.py` | Worker local (polling queue) |
| `scheduler_worker.py` | Scheduler APScheduler (SLA + daily report) |
| `docker-compose.yml` | Azurite pour stockage local |
| `tests/test_sla_two_level.py` | Tests du SLA à deux niveaux |

### Fichiers MODIFIÉS

| Fichier | Changements |
|---------|-------------|
| `storage/base.py` | Ajouter signatures abstraites (update_sla_status, etc.) |
| `storage/local.py` | Nouvelles colonnes schema, migration, update_sla_status, get_users_for_flux |
| `core/sla_policy.py` | classify_by_concordance(), seuils configurables, repli par flux |
| `core/sla_monitor.py` | Supprimer auto_close + escalade auto, ajouter breach email/report |
| `core/alert_state_machine.py` | Lire/écrire `workflow_status` au lieu de `status` dans `transition_alert()` |
| `core/email_alert.py` | Notification groupée rafale (buffer 30s), severity dans la création |
| `engine/flux_loader.py` | Ajouter alert_sla et consultant_email à FluxConfig |
| `engine/detailed_report.py` | Ajouter build_single_alert_report() |
| `api/alerts_api.py` | Nouvel endpoint POST /close, modifier update_status pour workflow_status |
| `registry/items.json` | Ajouter consultant_email et alert_sla optionnels |
| `registry/customerbalance.json` | Ajouter consultant_email et alert_sla optionnels |
| `config.py` | Ajouter QUEUE_BACKEND, ENABLE_SCHEDULER, DEFAULT_CONSULTANT_EMAIL |
| `startup.py` | Activer le scheduler si ENABLE_SCHEDULER=true |

### Fichiers Azure INTERDITS (jamais modifiés)

- `storage/azure_backend.py`
- `storage/blob_upload.py`
- `core/queue_client.py`
- `azure-pipelines.yml`
- Toute Azure Function

---

## TODO — Éléments exclus de cette livraison (avec justification)

### TODO-1 : Calcul du délai SLA en heures ouvrées

**Exclu de cette livraison.** Le calcul actuel utilise des heures calendaires (`timedelta(hours=sla_hours)`). Le passage aux heures ouvrées nécessiterait :
- Une table de jours fériés configurables (ou une API externe)
- Un calendar filter dans `recompute_sla_progress()` et `compute_sla_at_creation()`
- La gestion des fuseaux horaires par division (Doha, Koweït, etc.)

**Justification** : complexité significative, risque de régression sur le calcul SLA existant, et les délais actuels (2h/4h) sont assez courts pour que la différence heures ouvrées/calendaires soit marginale dans un premier temps. À implémenter dans une itération séparée.

### TODO-2 : État ON_HOLD (pause du chrono SLA)

**Exclu de cette livraison.** L'état ON_HOLD permettrait de mettre en pause le chrono SLA (ex: en attente d'un retour client). Cela nécessiterait :
- Un nouvel état dans le workflow (ON_HOLD entre IN_PROGRESS et RESOLVED)
- Des champs `hold_started_at` / `hold_ended_at` pour calculer la durée de pause
- Une modification de `recompute_sla_progress()` pour exclure la durée de pause du calcul
- De nouvelles règles de transition (comment sortir de ON_HOLD)

**Justification** : cas d'usage métier non prioritaire. Le workflow actuel (NEW → ACKNOWLEDGED → IN_PROGRESS → ESCALATED/RESOLVED) couvre les besoins actuels. L'ajout d'un état supplémentaire nécessite une réflexion UX et une validation métier avant implémentation. À planifier en itération séparée avec le client.

---

## Commandes de test local

```bash
# 1. Démarrer Azurite
docker-compose up -d

# 2. Initialiser la base MySQL (si première fois)
cd backend && python -c "from storage import get_storage; get_storage().init_db()"

# 3. Lancer le scheduler
ENABLE_SCHEDULER=true QUEUE_BACKEND=local python scheduler_worker.py

# 4. Déclencher une alerte CRITICAL de test (concordance < 50%)
python -c "
from core.email_alert import send_alert_async
class FakeResult:
    flux_id='ITEMS'; flux_name='Flux Items'; label='Test CRITICAL'
    error=''; divisions_found=['GLOBAL']
    total_critiques=50; total_warnings=5; total_anomalies=55
    concordance_moyenne=35.0
    pairs=[]
send_alert_async(FakeResult(), analysis_id=0)
"

# 5. Vérifier l'alerte créée
python -c "
from storage import get_storage
alerts = get_storage().list_alerts(flux_id='ITEMS', limit=1)
a = alerts[0]
print(f'status={a[\"status\"]}  workflow_status={a.get(\"workflow_status\")}  sla_status={a.get(\"sla_status\")}  severity={a.get(\"severity\")}')
"

# 6. Résolution manuelle
curl -X POST http://localhost:8000/api/alerts/<token>/resolve \
  -H 'Content-Type: application/json' \
  -d '{"comment": "Corrigé dans Cegid"}'

# 7. Fermeture manuelle
curl -X POST http://localhost:8000/api/alerts/<token>/close \
  -H 'Content-Type: application/json'
```
