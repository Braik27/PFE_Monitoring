# Flux Monitor — TimSoft

Plateforme de réconciliation et de détection d'anomalies entre exports comptables **Cegid** et **Oracle**. Développée pour **ABA Luxury** (by TimSoft).

## Table des matières

- [Vue d'ensemble](#vue-densemble)
- [Architecture](#architecture)
- [Stack technique](#stack-technique)
- [Fonctionnalités](#fonctionnalités)
- [Structure du projet](#structure-du-projet)
- [Installation locale](#installation-locale)
- [Configuration](#configuration)
- [Déploiement](#déploiement)
- [API REST](#api-rest)
- [Tests](#tests)
- [CI/CD](#cicd)
- [Contribuer](#contribuer)

## Vue d'ensemble

**Flux Monitor** automatise la comparaison de fichiers financiers exportés depuis deux ERP distincts :
- **Cegid** (comptabilité / ventes)
- **Oracle** (ERP groupe)

Le moteur détecte les écarts de prix, quantités, statuts, doublons, lignes manquantes et lignes CSV mal formées. Les anomalies sont enrichies par une **IA explicative** (LLM + RAG vectoriel) et pilotées par un **cycle de vie SLA** (création → accusé → résolution/escalade).

Le projet comprend aussi :
- Un **assistant IA conversationnel** pour interroger l'historique des anomalies.
- Des **rapports Excel** journaliers et par division.
- Un **pipeline asynchrone** (Azure Queue + Azure Function) pour les analyses longues.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Frontend      │────▶│   Backend Flask  │────▶│   Storage        │
│   React 19 SPA  │     │   (Python 3.12)  │     │   (SQLite /      │
│                 │◀────│                  │◀────│    Azure SQL)    │
└─────────────────┘     └────────┬─────────┘     └──────────────────┘
                                │
                    ┌───────────┼──────────────┐
                    │           │              │
              ┌─────▼──────┐ ┌──▼─────┐  ┌────▼──────────┐
              │  Engine    │ │  Core  │  │   AI / RAG    │
              │  (Reader   │ │ (SLA,  │  │ (Llama 3.3,   │
              │   Cleaner  │ │ Jobs,  │  │  FAISS,       │
              │   Compare) │ │ Mail)  │  │  RAG)         │
              └────────────┘ └────────┘  └───────────────┘
                                │
                      ┌─────────▼──────────┐
                      │  Azure (prod only) │
                      │  Blob / Queue /    │
                      │  Azure Function    │
                      └────────────────────┘
```

### Flux de traitement principal

1. **Upload** des fichiers Cegid / Oracle (CSV, Excel) via l'UI.
2. **Lecture** (`generic_reader.py`) : détection automatique de l'encodage, du séparateur, de la ligne d'en-tête.
3. **Nettoyage** (`generic_cleaner.py`) : normalisation des dates, des numériques, suppression des zéros inutiles.
4. **Comparaison** (`generic_comparator.py` / `comparator.py`) :
   - Appariement par clé(s) déclarée(s) dans le **registry**.
   - Détection des écarts de valeurs avec normalisation texte (suppression caractères spéciaux, collapse des espaces).
   - Détection des doublons, des absents et des lignes CSV invalides (`LIGNES_CSV_INVALIDES`).
5. **Enrichissement IA** : le LLM génère des explications contextualisées pour chaque anomalie.
6. **Alerting SLA** : création d'alertes, suivis, rappels automatiques, escalade.
7. **Rapports** : exports Excel par division, emails journaliers.

## Stack technique

| Couche | Technologie |
|--------|-------------|
| **Frontend** | React 19, TypeScript 6, Vite 8, TanStack React Query 5, React Router 7, Lucide, Axios, react-use-websocket |
| **Backend** | Python 3.12+, Flask 3, Gunicorn, Flask-Sock |
| **Base de données** | SQLite (dev) / Azure SQL (prod), SQLAlchemy 2, PyMySQL, pyodbc |
| **IA / LLM** | NVIDIA NIM (Llama 3.3 70B), FAISS, sentence-transformers (all-MiniLM-L6-v2) |
| **Machine Learning** | scikit-learn (Isolation Forest) |
| **Stockage cloud** | Azure Blob Storage, Azure Queue Storage |
| **Traitement asynchrone** | Celery + Redis (optionnel), APScheduler, Azure Functions (Queue-trigger) |
| **Fichiers** | Pandas, OpenPyXL, xlrd, PyArrow, DuckDB, Pandera |
| **Monitoring** | Azure Monitor / Application Insights (OpenTelemetry) |
| **Email** | SMTP (smtplib) / SendGrid |
| **CI/CD** | Azure Pipelines → Docker → Azure Container Registry → Azure App Service |

## Fonctionnalités

- **Analyse de flux** : upload de paires Cegid/Oracle, comparaison automatique avec registry configurable.
- **Smart Compare** : mapping automatique des colonnes avec apprentissage au fil du temps.
- **Détection d'anomalies** :
  - Écarts de valeurs (prix, quantités, statuts, descriptions).
  - Doublons et absents.
  - Lignes CSV mal formées (`LIGNES_CSV_INVALIDES`).
- **SLA & Alerting** :
  - Cycle de vie : `NEW → ACKNOWLEDGED → RESOLVED → CLOSED` (ou `ESCALATED`).
  - Politique dynamique par criticité (P1_FILE 2h, P1_MASS 4h, P2 4h, P3 24h).
  - Rappels automatiques à 20% de temps restant.
  - Escalade automatique en cas de breach.
- **IA Explicative** : enrichissement des anomalies par LLM avec contexte RAG.
- **Assistant IA** : chat conversationnel sur l'historique des anomalies.
- **Rapports** : exports Excel par division, emails journaliers, rapports customer balance.
- **Administration** : gestion des utilisateurs, des flux, des divisions, monitoring système.

## Structure du projet

```
Full/
├── frontend/                          # Application React
│   ├── src/
│   │   ├── pages/                     # Login, Dashboard, Analyze, History, Alerts,
│   │   │                              # Reports, Assistant, Profile, Admin
│   │   ├── components/                # Layout, Topbar, AsyncProgress, SLA widgets
│   │   ├── hooks/                     # useApi, useAsyncJob, useAlertsWebSocket
│   │   ├── contexts/                  # AuthContext, ToastContext
│   │   └── lib/                       # api.ts (Axios), user.ts
│   ├── package.json
│   └── vite.config.ts                 # Proxy dev vers Flask
│
├── backend/                           # API Flask + Moteur + IA
│   ├── app.py                         # Point d'entrée Flask, blueprints, WebSocket
│   ├── config.py                      # Configuration centralisée
│   ├── startup.py                     # Script de démarrage dev
│   ├── requirements.txt
│   ├── Dockerfile
│   │
│   ├── api/                           # Blueprints Flask
│   │   ├── auth.py                    # Authentification
│   │   ├── flux_api.py                # Gestion des flux
│   │   ├── analysis.py                # Endpoints d'analyse
│   │   ├── alerts_api.py              # CRUD alertes + actions SLA
│   │   ├── sla_api.py                 # Métriques SLA
│   │   ├── smart_compare_api.py       # Mapping auto de colonnes
│   │   ├── smart_compare_async.py     # Compare asynchrone
│   │   ├── assistant_api.py           # Assistant IA
│   │   ├── daily_report.py            # Rapports journaliers email
│   │   ├── customerbalance_report.py  # Rapports customer balance
│   │   └── admin.py                   # Administration
│   │
│   ├── engine/                        # Moteur de réconciliation
│   │   ├── pipeline.py                # Orchestrateur d'analyse
│   │   ├── flux_loader.py             # Chargement config flux (registry JSON)
│   │   ├── generic_reader.py          # Lecture CSV/Excel multi-encodage
│   │   ├── generic_cleaner.py         # Normalisation et nettoyage
│   │   ├── generic_comparator.py      # Comparaison Cegid vs Oracle
│   │   ├── comparator.py              # Comparaison async (API REST)
│   │   ├── schema_detector.py         # Détection auto de schéma
│   │   ├── division_splitter.py       # Détection divisions (KWT, KSA...)
│   │   ├── preprocessor.py            # Pré-traitement DataFrames
│   │   └── detailed_report.py         # Génération Excel
│   │
│   ├── ai/                            # Intelligence Artificielle
│   │   ├── llm_client.py              # Client NVIDIA NIM
│   │   ├── vector_store.py            # Index FAISS pour RAG
│   │   ├── rag_context.py             # Construction contexte RAG
│   │   └── agent_advisor.py           # Conseiller IA anomalies
│   │
│   ├── core/                          # Services métier
│   │   ├── job_manager.py             # Queue asynchrone (ThreadPool)
│   │   ├── sla_monitor.py             # Surveillance SLA, rappels, escalade
│   │   ├── sla_policy.py              # Politique et règles SLA
│   │   ├── alert_state_machine.py     # Machine à états alertes
│   │   ├── email_alert.py             # Envoi emails (SMTP/SendGrid)
│   │   ├── queue_client.py            # Client Azure Queue
│   │   ├── scheduler.py               # Planificateur legacy
│   │   └── monitoring.py              # Métriques + Azure Monitor
│   │
│   ├── storage/                       # Abstraction stockage
│   │   ├── base.py                    # BaseStorage (ABC)
│   │   ├── local.py                   # Implémentation SQLite / MySQL
│   │   ├── azure_backend.py           # Implémentation Azure SQL
│   │   └── blob_upload.py             # Upload vers Azure Blob
│   │
│   ├── registry/                      # Configuration des flux (JSON)
│   ├── instance/                      # Base SQLite + vecteurs FAISS
│   └── static/                        # Assets statiques
│
├── sql/
│   └── create_tables.sql              # Schéma MySQL (Bronze / Silver / Gold)
│
├── tests/                             # Tests d'intégration et unitaires
├── watcher/                           # utilitaire de surveillance fichiers
├── instance/
│   ├── flux_monitor.db                # Base SQLite locale
│   └── vectors/                       # Index FAISS
│
├── CHANGELOG.md
├── README.md
├── azure-pipelines.yml
├── migrate_sqlite_to_mysql.py
└── .gitignore
```

## Installation locale

### Prérequis

- **Node.js** 20+
- **Python** 3.12+
- **Redis** (optionnel, pour Celery en production)
- **Abonnement Azure** (pour le déploiement)

### Backend

```bash
cd backend

# Créer l'environnement virtuel
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate   # Linux/Mac

# Installer les dépendances
pip install -r requirements.txt

# Lancer le serveur
python startup.py
# → http://localhost:8000
# Login par défaut : admin / admin123
```

### Frontend

```bash
cd frontend

npm install
npm run dev
# → http://localhost:5173
# Les appels API /api, /ws, /static sont proxifiés vers http://127.0.0.1:8000
```

### Azure Function (traitement asynchrone optionnel)

```bash
# 1. Démarrer Azurite (émulateur Azure Storage local)
azurite --silent --location ./azurite-data

# 2. Créer la queue
python create_queue.py

# 3. Uploader des fichiers de test
python upload_test_files.py

# 4. Démarrer la Function localement
func start

# 5. Envoyer un message de test
python send_test_message.py
```

## Configuration

### Variables d'environnement

Créer un fichier `.env` dans `backend/` (ou utiliser les App Settings Azure en production) :

| Variable | Description | Valeur par défaut |
|----------|-------------|-------------------|
| `FLASK_ENV` | `development` ou `production` | `development` |
| `SECRET_KEY` | Clé secrète Flask pour les sessions | — |
| `STORAGE_BACKEND` | `local` (SQLite) ou `azure` (Azure SQL) | `local` |
| `AZURE_SQL_CONNECTION_STRING` | Chaîne de connexion Azure SQL | — |
| `AZURE_STORAGE_CONNECTION_STRING` | Connexion Azure Blob / Queue | — |
| `NVIDIA_API_KEY` | Clé API pour le LLM NVIDIA NIM | — |
| `ALERT_SMTP_HOST` | Serveur SMTP pour les emails | — |
| `ALERT_SMTP_PORT` | Port SMTP | `587` |
| `ALERT_SMTP_USER` | Utilisateur SMTP | — |
| `ALERT_SMTP_PASSWORD` | Mot de passe SMTP | — |
| `ALERT_FROM_EMAIL` | Expéditeur des emails d'alerte | — |
| `REDIS_URL` | URL Redis pour Celery (optionnel) | `redis://localhost:6379/0` |

### Configuration des flux (Registry)

Les flux sont définis dans `backend/registry/*.json`. Chaque fichier JSON décrit :
- `flux_id` : identifiant technique du flux.
- `flux_name` : nom affiché dans l'UI.
- `source` / `target` : systèmes sources et cibles.
- `column_names` : liste des colonnes attendues.
- `key_columns` : colonnes utilisées comme clé de rapprochement.
- `comparison_rules` : règles de comparaison par colonne (sévérité, tolérance).
- `pre_processing` : règles de filtrage et dédoublonnage spécifiques par source.

Exemple pour `items.json` :
```json
{
  "flux_id": "ITEMS",
  "flux_name": "Items Master",
  "key_columns": ["ITEM_CODE"],
  "comparison_rules": [
    { "column": "DESCRIPTION", "tolerance": 0, "severity": "WARNING" },
    { "column": "UNIT_PRICE", "tolerance": 0.01, "severity": "CRITIQUE" },
    { "column": "STATUS", "tolerance": 0, "severity": "CRITIQUE" }
  ]
}
```

## Déploiement

### Docker (production)

```bash
docker build -t flux-monitor -f backend/Dockerfile .
docker run -p 8000:8000 --env-file backend/.env flux-monitor
```

Le Dockerfile utilise **Gunicorn** pour servir Flask. Le frontend React est pré-construit par Azure Pipelines et injecté dans l'image.

### Azure (recommandé)

1. **Azure Container Registry** : pousse l'image Docker.
2. **Azure App Service** : héberge le conteneur avec les variables d'environnement configurées dans App Settings.
3. **Azure SQL Database** : base de données de production.
4. **Azure Blob Storage** : stockage des fichiers Cegid / Oracle et des résultats.
5. **Azure Queue Storage** + **Azure Function** : traitement asynchrone des analyses longues.
6. **Azure Monitor / Application Insights** : surveillance et logs.

## API REST

### Authentification

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `POST` | `/api/auth/login` | Connexion |
| `POST` | `/api/auth/logout` | Déconnexion |
| `POST` | `/api/auth/forgot-password` | Demande de réinitialisation |
| `POST` | `/api/auth/reset-password` | Réinitialisation du mot de passe |

### Flux & Analyse

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/flux` | Liste des flux |
| `POST` | `/api/flux/analyze` | Lancer une analyse synchrone |
| `POST` | `/api/flux/comparer` | Lancer une comparaison asynchrone |
| `GET` | `/api/flux/status/<job_id>` | Statut d'un job asynchrone |
| `GET` | `/api/flux/result/<job_id>` | Résultat d'un job asynchrone |

### Smart Compare

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `POST` | `/api/smart/preview` | Prévisualisation + mapping automatique |
| `POST` | `/api/smart/run` | Exécuter la comparaison avec mapping |
| `POST` | `/api/smart/learn` | Sauvegarder un mapping appris |
| `GET` | `/api/smart/mappings` | Lister les mappings appris |

### Alertes & SLA

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/alerts` | Liste des alertes |
| `PATCH` | `/api/alerts/<token>/status` | Changer le statut d'une alerte |
| `POST` | `/api/alerts/<token>/track` | Tracker une alerte |
| `GET` | `/api/sla/metrics` | Métriques SLA |

### Assistant IA

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `POST` | `/api/assistant/chat` | Chat avec l'assistant IA |
| `POST` | `/api/assistant/analyze-anomalies` | Analyse IA d'un lot d'anomalies |

### WebSocket

| Endpoint | Description |
|----------|-------------|
| `/ws/alerts` | WebSocket pour les alertes en temps réel et la progression des jobs |

## Tests

```bash
# Tests unitaires et d'intégration
pytest

# Ou avec couverture
pytest --cov=backend
```

Les fixtures de test se trouvent dans `tests/fixtures/` :
- `item_flux/` : fichiers de test pour le flux Items.
- `customer_balance/` : fichiers de test pour le flux Customer Balance.

## CI/CD

Le projet utilise **Azure Pipelines** (`azure-pipelines.yml`) :

1. **Build frontend** : Node.js 20 construit le bundle React.
2. **Build image Docker** : injection du frontend dans l'image Flask/Gunicorn.
3. **Push ACR** : pousse l'image vers Azure Container Registry.
4. **Deploy** : déploie sur Azure App Service.

Le pipeline se déclenche automatiquement sur les pushes vers `master`.

## Contribuer

1. Créer une branche depuis `master`.
2. Respecter la convention de nommage : `feat/xxx`, `fix/xxx`, `chore/xxx`.
3. Vérifier que les tests passent (`pytest`).
4. Ouvrir une Pull Request vers `master`.

## Licence

Interne — ABA Luxury / TimSoft.
