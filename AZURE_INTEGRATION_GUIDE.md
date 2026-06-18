# Guide d'intégration Azure — Flux Monitor

## Architecture cible

```
React (Frontend)
  │  Upload fichiers (multipart)
  ▼
Flask API (Backend — Azure App Service)
  │
  ├── Sauvegarde fichiers → Azure Blob Storage
  ├── Métadonnées → Azure SQL Database
  ├── Tâches asynchrones → Azure Queue Storage
  ├── Monitoring → Application Insights
  └── Dashboard → Azure Monitor Dashboard
```

---

## 1. Azure Blob Storage — Stockage des fichiers uploadés

### Objectif
Actuellement, les fichiers uploadés (Cegid, Oracle) sont sauvegardés dans `/tmp/flux_uploads` et supprimés après analyse. Avec Blob Storage, ils sont persistés, traçables et accessibles depuis n'importe quelle instance.

### Ce qui existe déjà
- `backend/storage/azure_backend.py` contient déjà `upload_blob()`, `download_blob()`, `list_blobs()` (lignes 658-671)
- `backend/config.py` définit déjà les conteneurs `AZURE_BLOB_CONTAINER_CEGID`, `AZURE_BLOB_CONTAINER_ORACLE`, `AZURE_BLOB_CONTAINER_RESULTS`
- `backend/requirements.txt` inclut déjà `azure-storage-blob>=12.19.0`

### Ce qu'il faut modifier

#### 1.1. Variables d'environnement à ajouter dans Azure App Service

| Variable | Valeur | Description |
|----------|--------|-------------|
| `AZURE_STORAGE_CONNECTION_STRING` | `DefaultEndpointsProtocol=...` | Chaîne de connexion du Storage Account |
| `AZURE_BLOB_CONTAINER_CEGID` | `cegid-files` | Conteneur pour fichiers Cegid |
| `AZURE_BLOB_CONTAINER_ORACLE` | `oracle-files` | Conteneur pour fichiers Oracle |
| `AZURE_BLOB_CONTAINER_RESULTS` | `flux-results` | Conteneur pour résultats d'analyse |
| `AZURE_STORAGE_UPLOAD_ENABLED` | `true` | Activer le stockage Blob pour les uploads |

#### 1.2. Service de upload Blob — Nouveau fichier

Créer `backend/core/blob_upload.py` :

```python
"""
core/blob_upload.py — Upload des fichiers d'analyse vers Azure Blob Storage
"""

import os
import logging
from azure.storage.blob import BlobServiceClient
from config import settings

log = logging.getLogger(__name__)

# Activation depuis la config
BLOB_UPLOAD_ENABLED = os.environ.get("AZURE_STORAGE_UPLOAD_ENABLED", "false").lower() == "true"

def get_blob_service() -> BlobServiceClient | None:
    if not settings.use_azure or not BLOB_UPLOAD_ENABLED:
        return None
    try:
        return BlobServiceClient.from_connection_string(settings.azure.BLOB_CONNECTION_STRING)
    except Exception as e:
        log.error("Impossible de créer BlobServiceClient: %s", e)
        return None

def upload_file_to_blob(file_path: str, container_name: str, blob_name: str) -> str | None:
    """Upload un fichier vers Blob Storage. Retourne l'URL du blob ou None."""
    service = get_blob_service()
    if not service:
        log.info("Blob storage désactivé — fichier %s non uploadé", blob_name)
        return None
    try:
        container_client = service.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_name)
        with open(file_path, "rb") as f:
            blob_client.upload_blob(f, overwrite=True)
        log.info("Fichier uploadé vers Blob: %s/%s", container_name, blob_name)
        return blob_client.url
    except Exception as e:
        log.error("Erreur upload blob %s/%s: %s", container_name, blob_name, e)
        return None

def download_blob_to_temp(container_name: str, blob_name: str) -> str | None:
    """Télécharge un blob vers un fichier temporaire. Retourne le chemin ou None."""
    import tempfile
    service = get_blob_service()
    if not service:
        return None
    try:
        container_client = service.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_name)
        suffix = os.path.splitext(blob_name)[1] or ".tmp"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            data = blob_client.download_blob().readall()
            f.write(data)
            return f.name
    except Exception as e:
        log.error("Erreur download blob %s/%s: %s", container_name, blob_name, e)
        return None
```

#### 1.3. Modifier `backend/api/analysis.py` pour uploader vers Blob

Dans la fonction `analyze()`, remplacer la section qui écrit les fichiers temporaires (lignes 68-79) et la section de nettoyage (lignes 97-101) :

```python
# Après avoir créé les fichiers temporaires (lignes 68-79 actuelles) :

# Upload vers Blob Storage si activé
blob_cegid_url = None
blob_oracle_url = None
if settings.use_azure:
    from core.blob_upload import upload_file_to_blob
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    blob_cegid_url = upload_file_to_blob(
        path_cegid,
        settings.azure.BLOB_CONTAINER_CEGID,
        f"{flux_id}/{timestamp}_{f_cegid.filename}"
    )
    blob_oracle_url = upload_file_to_blob(
        path_oracle,
        settings.azure.BLOB_CONTAINER_ORACLE,
        f"{flux_id}/{timestamp}_{f_oracle.filename}"
    )

# Puis dans le summary, ajouter les URLs :
# (après la ligne 111, avant la troncature des anomalies)
summary["blob_cegid_url"] = blob_cegid_url
summary["blob_oracle_url"] = blob_oracle_url
```

#### 1.4. Modifier `backend/api/smart_compare_api.py` pour Blob

Dans `_read_file()` (ligne 50), faire la même chose : uploader le fichier après l'avoir sauvegardé en temporaire.

#### 1.5. Sauvegarde du rapport d'écart (gap report) vers Blob

Après chaque analyse, le summary JSON complet peut être sauvegardé dans le conteneur `flux-results` :

```python
# Dans analysis.py, après avoir construit summary (ligne 137)
if settings.use_azure:
    from core.blob_upload import upload_file_to_blob
    import json
    report_blob_name = f"{flux_id}/{timestamp}_report.json"
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(summary, f, ensure_ascii=False, default=str)
        report_path = f.name
    upload_file_to_blob(report_path, settings.azure.BLOB_CONTAINER_RESULTS, report_blob_name)
    os.unlink(report_path)
```

---

## 2. Application Insights — Monitoring des performances et erreurs

### Objectif
- Capturer toutes les requêtes HTTP (temps de réponse, status codes)
- Logger les exceptions avec stacktrace
- Suivre les événements métier (analyses, alertes, suggestions IA)
- Mesurer les temps de traitement des comparaisons

### Ce qui existe déjà
- `backend/core/monitoring.py` contient `setup_azure_monitoring()`, `track_event()`, `track_exception()` (lignes 173-258)
- `backend/requirements.txt` inclut `azure-monitor-opentelemetry>=1.6.0`
- Appelé dans `backend/app.py` ligne 316 : `setup_azure_monitoring(app)`

### Ce qu'il faut faire

#### 2.1. Créer une ressource Application Insights

Dans le portail Azure :
1. Créer une ressource **Application Insights**
2. Copier la **Connection String** (onglet "Overview" ou "Properties")
3. La définir dans Azure App Service → Settings → Environment variables :

| Variable | Valeur |
|----------|--------|
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | `InstrumentationKey=...;IngestionEndpoint=...` |

#### 2.2. Ajouter des événements métier personnalisés

Dans `backend/core/monitoring.py`, des helpers existent déjà. Les utiliser dans les points clés :

```python
# Après chaque analyse réussie — dans analysis.py
from core.monitoring import track_event
track_event("analyse_terminee", {
    "flux_id": flux_id,
    "n_critiques": summary.get("n_critiques", 0),
    "n_warnings": summary.get("n_warnings", 0),
    "concordance_pct": summary.get("concordance_pct", 0),
    "duree_secondes": summary.get("duree_secondes", 0),
})
```

```python
# Dans smart_compare_api.py — après chaque comparaison
track_event("smart_compare_terminee", {
    "flux_id": flux_id,
    "total_lignes": total_rows,
    "n_anomalies": n_anomalies,
    "duree_ms": duration_ms,
})
```

```python
# Dans alerts_api.py — après création d'alerte
track_event("alerte_creee", {
    "flux_name": flux_name,
    "n_critiques": n_critiques,
    "token": token,
})
```

```python
# Dans assistant_api.py — après suggestion IA
track_event("ia_suggestion", {
    "type": "assistant",
    "user": username,
    "n_alertes": n_alertes,
})
```

```python
# Dans engine/pipeline.py — tracker les échecs
try:
    result = run_analysis(req)
except Exception as e:
    from core.monitoring import track_exception
    track_exception(e, {"flux_id": flux_id, "step": "pipeline"})
    raise
```

#### 2.3. Métriques personnalisées (temps de réponse, comparaisons)

Application Insights capture déjà automatiquement :
- Durée de chaque requête HTTP (dans `requests/duration`)
- Taux d'erreur (`requests/failed`)
- Dépendances (appels SQL, HTTP externes)

Pour des métriques personnalisées supplémentaires, créer `backend/core/metrics_middleware.py` :

```python
"""
core/metrics_middleware.py — Métriques custom pour Application Insights
"""

from opentelemetry import metrics
from opentelemetry.metrics import Observation

meter = metrics.get_meter("flux_monitor")

# Compteurs
analyses_counter = meter.create_counter(
    "flux.analyses.total",
    description="Nombre total d'analyses lancées",
)

alerts_counter = meter.create_counter(
    "flux.alerts.total",
    description="Nombre total d'alertes générées",
)

comparisons_counter = meter.create_counter(
    "flux.comparisons.total",
    description="Nombre total de comparaisons",
)

failed_comparisons_counter = meter.create_counter(
    "flux.comparisons.failed",
    description="Nombre de comparaisons échouées",
)

# Histogramme pour les durées
comparison_duration = meter.create_histogram(
    "flux.comparison.duration.ms",
    description="Durée des comparaisons en ms",
    unit="ms",
)

gaps_found = meter.create_histogram(
    "flux.gaps.found",
    description="Nombre d'écarts trouvés par analyse",
)

def record_analysis():
    analyses_counter.add(1)

def record_alert():
    alerts_counter.add(1)

def record_comparison(duration_ms: float, failed: bool = False):
    comparisons_counter.add(1)
    comparison_duration.record(duration_ms)
    if failed:
        failed_comparisons_counter.add(1)

def record_gaps(n_gaps: int):
    gaps_found.record(n_gaps)
```

---

## 3. Azure Queue Storage — Traitement asynchrone

### Objectif
Actuellement, le projet utilise `ThreadPoolExecutor` dans `core/job_manager.py` pour les tâches async. En production avec plusieurs instances (scale-out), cela ne fonctionne pas car chaque instance a son propre pool. Azure Queue Storage permet de distribuer les tâches entre toutes les instances.

### Architecture proposée

```
POST /api/smart/run-async
  ↓
Flask envoie un message → Azure Queue Storage
  ↓
Azure Function (ou Worker dans App Service) lit la queue
  ↓
Traitement de la comparaison
  ↓
Résultat sauvegardé → Blob Storage + Azure SQL
  ↓
Frontend notifié via WebSocket / polling GET /api/smart/jobs/<id>
```

### Ce qu'il faut créer

#### 3.1. Configuration — Ajouter dans `backend/config.py`

```python
@dataclass(frozen=True)
class AzureQueueConfig:
    CONNECTION_STRING: str = field(default_factory=lambda: _env("AZURE_QUEUE_CONNECTION_STRING"))
    QUEUE_NAME:        str = field(default_factory=lambda: _env("AZURE_QUEUE_NAME", "comparison-jobs"))
```

Et dans `AppConfig` (ligne 94) :
```python
queue: AzureQueueConfig = field(default_factory=AzureQueueConfig)
```

#### 3.2. Service Queue — Nouveau fichier `backend/core/queue_service.py`

```python
"""
core/queue_service.py — Azure Queue Storage pour les tâches asynchrones
"""
import json
import logging
from azure.storage.queue import (
    QueueServiceClient,
    QueueClient,
    QueueMessage,
    TextBase64EncodePolicy,
    TextBase64DecodePolicy,
)
from config import settings

log = logging.getLogger(__name__)

_queue_service: QueueServiceClient | None = None

def get_queue_client() -> QueueClient | None:
    global _queue_service
    if not settings.use_azure:
        return None
    try:
        if _queue_service is None:
            _queue_service = QueueServiceClient.from_connection_string(
                settings.queue.CONNECTION_STRING
            )
        queue_client = _queue_service.get_queue_client(
            settings.queue.QUEUE_NAME,
            message_encode_policy=TextBase64EncodePolicy(),
            message_decode_policy=TextBase64DecodePolicy(),
        )
        queue_client.create_queue()  # No-op si existe déjà
        return queue_client
    except Exception as e:
        log.error("Erreur initialisation queue: %s", e)
        return None

def enqueue_job(job_type: str, payload: dict) -> bool:
    """Envoie un message dans la queue. Retourne True si succès."""
    queue = get_queue_client()
    if not queue:
        return False
    try:
        message = json.dumps({
            "job_type": job_type,
            "payload": payload,
            "created_at": __import__("datetime").datetime.now().isoformat(),
        })
        queue.send_message(message)
        log.info("Message envoyé dans la queue: %s", job_type)
        return True
    except Exception as e:
        log.error("Erreur envoi queue %s: %s", job_type, e)
        return False

def dequeue_job() -> dict | None:
    """Lit et supprime le prochain message de la queue."""
    queue = get_queue_client()
    if not queue:
        return None
    try:
        messages = queue.receive_messages(messages_per_page=1, visibility_timeout=300)
        for msg in messages:
            data = json.loads(msg.content)
            queue.delete_message(msg.id, msg.pop_receipt)
            return data
    except Exception as e:
        log.error("Erreur dequeuing: %s", e)
    return None

def peek_queue_length() -> int:
    """Retourne le nombre de messages dans la queue."""
    queue = get_queue_client()
    if not queue:
        return 0
    try:
        props = queue.get_queue_properties()
        return props.approximate_message_count or 0
    except Exception:
        return 0
```

#### 3.3. Worker — Nouveau fichier `backend/core/queue_worker.py`

```python
"""
core/queue_worker.py — Worker qui traite les messages de la queue Azure.

Démarrage automatique dans app.py (thread séparé).
En production, on peut aussi utiliser Azure Functions pour scaler.
"""
import json
import logging
import threading
import time
from core.queue_service import dequeue_job
from core.job_manager import get_job_manager, JobStatus

log = logging.getLogger(__name__)


def process_message(data: dict) -> None:
    """Traite un message de la queue selon son type."""
    job_type = data.get("job_type")
    payload = data.get("payload", {})

    log.info("Traitement job %s: %s", job_type, payload.get("job_id", "?"))

    if job_type == "smart_compare":
        from engine.smart_pipeline import run_smart_compare_async
        run_smart_compare_async(payload)
    elif job_type == "analysis":
        from engine.pipeline import run_analysis_async
        run_analysis_async(payload)
    else:
        log.warning("Type de job inconnu: %s", job_type)


def worker_loop(interval: float = 2.0):
    """Boucle infinie qui vérifie la queue toutes les `interval` secondes."""
    log.info("Queue worker démarré (intervalle=%ss)", interval)
    while True:
        try:
            msg = dequeue_job()
            if msg:
                process_message(msg)
        except Exception as e:
            log.error("Erreur worker loop: %s", e)
        time.sleep(interval)


def start_queue_worker():
    """Démarre le worker dans un thread daemon."""
    thread = threading.Thread(target=worker_loop, daemon=True)
    thread.start()
    log.info("Queue worker thread démarré")
```

#### 3.4. Variables d'environnement pour la queue

| Variable | Valeur | Description |
|----------|--------|-------------|
| `AZURE_QUEUE_CONNECTION_STRING` | `DefaultEndpointsProtocol=...` | Même Storage Account que Blob |
| `AZURE_QUEUE_NAME` | `comparison-jobs` | Nom de la queue |

#### 3.5. Modifier `backend/api/smart_compare_async.py`

Dans la route POST `/api/smart/run-async`, remplacer l'appel à `get_job_manager().submit()` par un envoi vers la queue :

```python
# Au lieu de :
# job = get_job_manager().submit(...)

# Faire :
if settings.use_azure and settings.queue.CONNECTION_STRING:
    from core.queue_service import enqueue_job
    job_id = str(uuid.uuid4())
    enqueued = enqueue_job("smart_compare", {
        "job_id": job_id,
        "paths": {"cegid": path_cegid, "oracle": path_oracle},
        "config": config,
        "user": session.get("user", {}).get("username", ""),
    })
    if not enqueued:
        # Fallback vers le JobManager local
        job = get_job_manager().submit(...)
        job_id = job.job_id
else:
    job = get_job_manager().submit(...)
    job_id = job.job_id

return jsonify({"ok": True, "job_id": job_id, "message": "Analyse lancée..."})
```

---

## 4. Azure Monitor Dashboard — Tableau de bord visuel

### Objectif
Créer un dashboard Azure Monitor qui affiche en temps réel les métriques de l'application.

### 4.1. Métriques disponibles dans Application Insights

Après avoir déployé avec App Insights activé, les métriques suivantes sont automatiquement disponibles :

| Métrique | Source | Description |
|----------|--------|-------------|
| `requests/count` | Auto | Nombre total de requêtes |
| `requests/failed` | Auto | Requêtes en erreur (5xx) |
| `requests/duration` | Auto | Temps de réponse moyen |
| `exceptions/count` | Auto | Exceptions non gérées |

Avec les métriques custom du step 2.3 :

| Métrique | Description |
|----------|-------------|
| `flux.analyses.total` | Nombre total d'analyses |
| `flux.alerts.total` | Alertes générées |
| `flux.comparisons.total` | Comparaisons effectuées |
| `flux.comparisons.failed` | Comparaisons échouées |
| `flux.comparison.duration.ms` | Durée des comparaisons |
| `flux.gaps.found` | Nombre d'écarts trouvés |

### 4.2. Créer le dashboard dans Azure

Via le portail Azure ou avec Terraform/Bicep. Voici une template ARM :

```json
{
  "type": "Microsoft.Portal/dashboards",
  "apiVersion": "2020-09-01-preview",
  "name": "FluxMonitor-Dashboard",
  "location": "[resourceGroup().location]",
  "properties": {
    "lenses": {
      "0": {
        "order": 0,
        "parts": {
          "0": {
            "position": { "x": 0, "y": 0, "rowSpan": 2, "colSpan": 2 },
            "metadata": {
              "type": "Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart",
              "settings": {
                "resourceIds": ["[parameters('appInsightsId')]"],
                "metrics": [
                  { "resourceMetadata": { "appInsightsId": "[parameters('appInsightsId')]" },
                    "name": "requests/count", "aggregationType": 1, "namespace": "microsoft.insights/components" }
                ],
                "title": "Requêtes totales",
                "timespan": { "relative": { "duration": 24, "timeUnit": 1 } }
              }
            }
          },
          "1": {
            "position": { "x": 2, "y": 0, "rowSpan": 2, "colSpan": 2 },
            "metadata": {
              "type": "Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart",
              "settings": {
                "resourceIds": ["[parameters('appInsightsId')]"],
                "metrics": [
                  { "resourceMetadata": { "appInsightsId": "[parameters('appInsightsId')]" },
                    "name": "requests/failed", "aggregationType": 1, "namespace": "microsoft.insights/components" }
                ],
                "title": "Requêtes échouées",
                "timespan": { "relative": { "duration": 24, "timeUnit": 1 } }
              }
            }
          },
          "2": {
            "position": { "x": 4, "y": 0, "rowSpan": 2, "colSpan": 2 },
            "metadata": {
              "type": "Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart",
              "settings": {
                "resourceIds": ["[parameters('appInsightsId')]"],
                "metrics": [
                  { "resourceMetadata": { "appInsightsId": "[parameters('appInsightsId')]" },
                    "name": "requests/duration", "aggregationType": 4, "namespace": "microsoft.insights/components" }
                ],
                "title": "Temps de réponse moyen (ms)",
                "timespan": { "relative": { "duration": 24, "timeUnit": 1 } }
              }
            }
          },
          "3": {
            "position": { "x": 0, "y": 2, "rowSpan": 2, "colSpan": 3 },
            "metadata": {
              "type": "Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart",
              "settings": {
                "resourceIds": ["[parameters('appInsightsId')]"],
                "metrics": [
                  { "resourceMetadata": { "appInsightsId": "[parameters('appInsightsId')]" },
                    "name": "flux.comparisons.total", "aggregationType": 1 },
                  { "resourceMetadata": { "appInsightsId": "[parameters('appInsightsId')]" },
                    "name": "flux.comparisons.failed", "aggregationType": 1 }
                ],
                "title": "Comparaisons : succès vs échecs",
                "timespan": { "relative": { "duration": 24, "timeUnit": 1 } }
              }
            }
          },
          "4": {
            "position": { "x": 3, "y": 2, "rowSpan": 2, "colSpan": 3 },
            "metadata": {
              "type": "Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart",
              "settings": {
                "resourceIds": ["[parameters('appInsightsId')]"],
                "metrics": [
                  { "resourceMetadata": { "appInsightsId": "[parameters('appInsightsId')]" },
                    "name": "flux.comparison.duration.ms", "aggregationType": 4 }
                ],
                "title": "Temps de traitement moyen (ms)",
                "timespan": { "relative": { "duration": 24, "timeUnit": 1 } }
              }
            }
          },
          "5": {
            "position": { "x": 0, "y": 4, "rowSpan": 2, "colSpan": 3 },
            "metadata": {
              "type": "Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart",
              "settings": {
                "resourceIds": ["[parameters('appInsightsId')]"],
                "metrics": [
                  { "resourceMetadata": { "appInsightsId": "[parameters('appInsightsId')]" },
                    "name": "flux.gaps.found", "aggregationType": 4 }
                ],
                "title": "Nombre d'écarts trouvés (moyen)",
                "timespan": { "relative": { "duration": 24, "timeUnit": 1 } }
              }
            }
          },
          "6": {
            "position": { "x": 3, "y": 4, "rowSpan": 2, "colSpan": 3 },
            "metadata": {
              "type": "Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart",
              "settings": {
                "resourceIds": ["[parameters('appInsightsId')]"],
                "metrics": [
                  { "resourceMetadata": { "appInsightsId": "[parameters('appInsightsId')]" },
                    "name": "exceptions/count", "aggregationType": 1, "namespace": "microsoft.insights/components" }
                ],
                "title": "Exceptions / Erreurs API",
                "timespan": { "relative": { "duration": 24, "timeUnit": 1 } }
              }
            }
          },
          "7": {
            "position": { "x": 0, "y": 6, "rowSpan": 1, "colSpan": 6 },
            "metadata": {
              "type": "Extension/HubsExtension/PartType/MonitorChartPart",
              "settings": {
                "title": "Top fichiers avec erreurs",
                "subtitle": "Application Insights - Requêtes personnalisées",
                "content": {
                  "options": {
                    "chart": {
                      "metrics": [
                        { "resourceMetadata": { "appInsightsId": "[parameters('appInsightsId')]" },
                          "name": "requests/failed", "aggregationType": 7,
                          "filter": { "dimension": "url", "operator": "eq", "values": ["/api/analyze"] } }
                      ]
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

### 4.3. Déploiement du dashboard

```bash
# Via Azure CLI
az deployment group create \
  --resource-group VotreRG \
  --template-file dashboard-template.json \
  --parameters appInsightsId=/subscriptions/.../providers/microsoft.insights/components/FluxMonitorAI
```

Ou le créer manuellement depuis le portail :
1. Azure Monitor → Dashboards → New dashboard
2. Ajouter les tuiles "Metrics chart" pour chaque métrique
3. Configurer la ressource App Insights comme source
4. Publier et partager

---

## 5. Déploiement et configuration Azure

### 5.1. Ressources Azure nécessaires

| Ressource | SKU recommandé | Usage |
|-----------|----------------|-------|
| Azure App Service | B1 (dev) / S1 (prod) | Héberger Flask + React |
| Azure SQL Database | S0 (dev) / S1 (prod) | Base de données |
| Azure Storage Account (Blob + Queue) | Standard LRS | Fichiers + Queue |
| Application Insights | Workspace-based | Monitoring |
| Azure Container Registry | Basic | Stockage image Docker |

### 5.2. Pipeline CI/CD — `azure-pipelines.yml`

Le pipeline existant (lignes 1-90) est déjà configuré pour :
1. Builder le frontend React (`npm ci` + `npm run build`)
2. Builder l'image Docker
3. Pusher vers ACR (Azure Container Registry)

**Ajout : déploiement vers App Service**

```yaml
# Ajouter à la fin du pipeline
- stage: Deploy
  displayName: Deploy to Azure App Service
  dependsOn: Docker
  jobs:
    - deployment: DeployApp
      displayName: Deploy to App Service
      pool:
        vmImage: $(vmImageName)
      environment: production
      strategy:
        runOnce:
          deploy:
            steps:
              - task: AzureWebAppContainer@1
                displayName: Deploy container to App Service
                inputs:
                  azureSubscription: 'Votre-Azure-Service-Connection'
                  appName: 'flux-monitor-app'
                  containers: '$(containerRegistry)/$(imageRepository):$(tag)'
```

### 5.3. Variables d'environnement Azure App Service

Dans App Service → Settings → Environment variables, définir :

```
FLASK_ENV=production
SECRET_KEY=<générer une clé aléatoire>
STORAGE_BACKEND=azure
AZURE_SQL_CONNECTION_STRING=Server=tcp:...;Database=...;...
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=...
AZURE_BLOB_CONTAINER_CEGID=cegid-files
AZURE_BLOB_CONTAINER_ORACLE=oracle-files
AZURE_BLOB_CONTAINER_RESULTS=flux-results
AZURE_QUEUE_CONNECTION_STRING=DefaultEndpointsProtocol=...
AZURE_QUEUE_NAME=comparison-jobs
AZURE_STORAGE_UPLOAD_ENABLED=true
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...
ADMIN_USER=admin
ADMIN_PASSWORD=<mot de passe admin>
```

---

## 6. Ordre des modifications recommandé

1. **Phase 1 — Blob Storage** (le plus simple, déjà partiellement fait)
   - Créer `core/blob_upload.py`
   - Modifier `analysis.py` et `smart_compare_api.py`
   - Tester en local avec `AZURE_STORAGE_UPLOAD_ENABLED=true` + connection string

2. **Phase 2 — Application Insights**
   - Créer la ressource App Insights
   - Ajouter `APPLICATIONINSIGHTS_CONNECTION_STRING` dans App Settings
   - Déployer et vérifier que les données arrivent dans Azure Portal
   - Ajouter les `track_event()` aux points clés

3. **Phase 3 — Queue Storage**
   - Créer `core/queue_service.py` et `core/queue_worker.py`
   - Modifier `smart_compare_async.py`
   - Tester localement avec Azurite (émulateur Azure Storage)
   - Déployer et monitorer

4. **Phase 4 — Dashboard**
   - Une fois les métriques App Insights qui arrivent
   - Créer le dashboard via le portail ou template ARM
   - Partager avec l'équipe
