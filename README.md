# Flux Monitor

Application web de réconciliation et de suivi des flux financiers. Compare les données Cegid et Oracle, détecte les écarts, génère des rapports et assiste l'utilisateur avec un module IA.

## Stack

| Couche | Technologie |
|--------|-------------|
| Backend | Python 3.12, Flask, Flask-Sock, Gunicorn |
| Frontend | React 19, TypeScript, Vite, React Router 7 |
| Data | Pandas, DuckDB, PyArrow, OpenPyXL, pandera |
| IA | Sentence Transformers, FAISS, scikit-learn, NVIDIA NIM |
| Auth | Locale (username/password, session) |
| Stockage | SQLite (dev) / Azure SQL + Blob Storage (prod) |
| Jobs | APScheduler, ThreadPoolExecutor |
| Alertes | SMTP / SendGrid, WebSocket |
| Monitoring | Azure Monitor OpenTelemetry, python-json-logger |
| CI/CD | Azure Pipelines, Docker, Azure Container Registry |

## Structure

```
backend/
├── app.py                          # Point d'entrée Flask + routes SPA/static
├── startup.py                      # Lancement local (port 5000)
├── config.py                       # Configuration centralisée
├── Dockerfile                      # Image production
├── requirements.txt                # Dépendances Python
├── test_architecture.py            # Tests validation architecture
├── test_blob.py                    # Test connectivité Azure Blob
├── migrate_stats_columns.py        # Migration DB
├── analyse_customer_balance.py     # Analyse CustomerBalance standalone
├── ai/                             # Assistant IA, RAG, vector store
│   ├── llm_client.py               # Client NVIDIA NIM
│   ├── vector_store.py             # Store FAISS pour RAG
│   ├── agent_advisor.py            # Conseils et enrichissement IA
│   └── rag_context.py              # Contexte RAG pour anomalies
├── api/                            # Blueprints et endpoints REST
│   ├── auth.py                     # Auth, profil, admin utilisateurs
│   ├── admin.py                    # Admin (divisions, stats)
│   ├── flux_api.py                 # CRUD flux, analyse schéma, comparaison
│   ├── analysis.py                 # Analyse, historique, stats, export
│   ├── alerts_api.py               # Alertes CRUD, escalade, feedback
│   ├── assistant_api.py            # Chat IA, conversations, streaming SSE
│   ├── smart_compare_api.py        # Comparaison intelligente
│   ├── smart_compare_async.py      # Comparaison asynchrone
│   ├── system_status.py            # Santé, métriques, audit
│   ├── daily_report.py             # Rapports Excel quotidiens/mensuels
│   └── customerbalance_report.py   # Rapports CustomerBalance
├── core/                           # Alertes, jobs, monitoring, SLA
│   ├── job_manager.py              # Gestionnaire asynchrone (ThreadPoolExecutor)
│   ├── email_alert.py              # Envoi SMTP / SendGrid
│   ├── alert_state_machine.py      # Machine à états + SLA
│   ├── scheduler.py                # Tâches planifiées APScheduler
│   ├── monitoring.py               # Métriques requêtes, Azure Monitor
│   └── sla_monitor.py              # Calcul périodique SLA
├── engine/                         # Pipeline analyse, lecture, nettoyage
│   ├── pipeline.py                 # Orchestrateur d'analyse
│   ├── flux_loader.py              # Chargement configs flux
│   ├── generic_reader.py           # Lecture CSV / Excel
│   ├── generic_cleaner.py          # Nettoyage et normalisation
│   ├── generic_comparator.py       # Moteur de comparaison
│   ├── comparator.py               # Comparaison legacy
│   ├── division_splitter.py        # Détection divisions
│   └── schema_detector.py          # Détection automatique schéma
├── storage/                        # Backends de stockage
│   ├── base.py                     # Classe abstraite BaseStorage
│   ├── local.py                    # SQLite LocalStorage
│   ├── azure_backend.py            # Azure SQL + Blob Storage
│   └── blob_upload.py              # Utilitaire upload Blob
├── registry/                       # Définitions JSON des flux
│   ├── sales.json                  # Flux SALES
│   ├── items.json                  # Flux ITEMS
│   └── customerbalance.json        # Flux CUSTOMERBALANCE
├── static/                         # Fichiers statiques (logo, etc.)
├── instance/                       # Runtime (DB, metrics, vectors)
└── reports/                        # Rapports générés

frontend/
├── src/
│   ├── pages/                      # Pages de l'application
│   │   ├── Login/                  # Connexion
│   │   ├── Dashboard/              # Tableau de bord
│   │   ├── Analyze/                # Analyse
│   │   ├── Reporting/              # Rapports
│   │   ├── History/                # Historique
│   │   ├── Alerts/                 # Alertes
│   │   ├── Reports/                # Rapports dédiés
│   │   ├── SmartCompare/           # Comparaison intelligente
│   │   ├── Assistant/              # Assistant IA
│   │   ├── Profile/                # Profil utilisateur
│   │   └── Admin/                  # Administration
│   │       ├── FluxAdmin.tsx       # Gestion des flux
│   │       ├── Users.tsx           # Gestion des utilisateurs
│   │       └── Monitoring.tsx      # Monitoring système
│   ├── components/                 # Composants réutilisables
│   │   ├── Layout/                 # Layout principal
│   │   ├── Sidebar/                # Barre de navigation
│   │   ├── Topbar/                 # Barre supérieure
│   │   ├── Toast/                  # Notifications toast
│   │   ├── PageLoader/             # Spinner de chargement
│   │   ├── ErrorBoundary/          # Gestion d'erreurs
│   │   └── AsyncAnalysisProgress.tsx # Progression asynchrone
│   ├── contexts/                   # Contextes React
│   │   ├── AuthContext.tsx          # Authentification
│   │   └── ToastContext.tsx         # Toasts
│   ├── hooks/                      # Hooks personnalisés
│   │   ├── useApi.ts               # Requêtes API
│   │   ├── useAsyncJob.ts          # Suivi jobs asynchrones
│   │   └── useAlertsWebSocket.ts   # WebSocket alertes
│   ├── lib/                        # Utilitaires
│   │   ├── api.ts                  # Client API (axios)
│   │   └── user.ts                 # Utilitaires utilisateur
│   ├── assets/                     # Images et ressources
│   ├── App.tsx                     # Routes principales
│   ├── main.tsx                    # Point d'entrée
│   └── index.css                   # Styles globaux
└── vite.config.ts
```

## Prérequis

- Python 3.12+
- Node.js 20+
- npm

## Installation

```powershell
# Backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
cd backend
pip install -r requirements.txt

# Frontend
cd frontend
npm install
```

## Configuration

Créez `backend/.env` :

```env
FLASK_ENV=development
SECRET_KEY=dev-secret-change-me
STORAGE_BACKEND=local
LOCAL_DB_PATH=instance/flux_monitor.db
ADMIN_USER=admin
ADMIN_PASSWORD=Ch@ng3Me!2024

# IA — clé NVIDIA NIM (obligatoire pour l'assistant)
NVIDIA_API_KEY=nvapi-xxx

# Alertes email (optionnel)
ALERT_EMAIL_ENABLED=false
# ALERT_SMTP_HOST=smtp.sendgrid.net
# ALERT_SMTP_USER=apikey
# ALERT_SMTP_PASSWORD=xxx
# ALERT_EMAIL_TO=destinataire@example.com
```

## Lancement

```powershell
# Terminal 1 — Backend (port 5000)
cd backend
python startup.py

# Terminal 2 — Frontend (port 5173, proxy vers 5000)
cd frontend
npm run dev
```

Identifiants : `admin` / `Ch@ng3Me!2024`

## Commandes

```powershell
# Backend
python backend/startup.py                  # Lancement serveur
python backend/test_architecture.py        # Tests validation architecture
python backend/test_blob.py                # Test Azure Blob
python backend/migrate_stats_columns.py    # Migration DB
python backend/analyse_customer_balance.py # Analyse CustomerBalance

# Frontend
cd frontend
npm run dev        # Dev server
npm run build      # Production build
npm run lint       # ESLint
```

## Flux (Registry)

Les flux sont définis dans `backend/registry/` via des fichiers JSON :

| Fichier | Flux |
|---------|------|
| `sales.json` | Flux SALES |
| `items.json` | Flux ITEMS |
| `customerbalance.json` | Flux CUSTOMERBALANCE |

Chaque définition contient les mappings de colonnes Cegid → Oracle, les seuils de tolérance et les règles de comparaison.

## Docker

```powershell
docker build -t flux-monitor -f backend/Dockerfile .
docker run -p 5000:5000 --env-file backend/.env flux-monitor
```

L'image utilise Python 3.12-slim et expose le port 5000.

## API

### Authentification & Utilisateurs
| Méthode | Endpoint | Rôle |
|---------|----------|------|
| POST | `/api/login` | Connexion |
| POST | `/api/logout` | Déconnexion |
| GET | `/api/me` | Profil courant |
| GET | `/api/session` | Infos session |
| PUT | `/api/profile` | Modifier profil |
| PUT | `/api/profile/password` | Changer mot de passe |
| GET | `/api/admin/users` | Lister utilisateurs |
| POST | `/api/admin/users` | Créer utilisateur |
| PUT | `/api/admin/users/<id>` | Modifier utilisateur |
| DELETE | `/api/admin/users/<id>` | Supprimer utilisateur |
| GET | `/api/admin/stats` | Statistiques admin |
| GET | `/api/divisions` | Lister divisions |
| POST | `/api/divisions` | Créer division |
| PUT | `/api/divisions/<code>` | Modifier division |
| DELETE | `/api/divisions/<code>` | Supprimer division |

### Flux
| Méthode | Endpoint | Rôle |
|---------|----------|------|
| GET | `/api/flux` | Liste des flux configurés |
| GET | `/api/flux/<flux_id>` | Détail d'un flux |
| POST | `/api/flux` | Créer un flux |
| PUT | `/api/flux/<flux_id>` | Modifier un flux |
| DELETE | `/api/flux/<flux_id>` | Supprimer un flux |
| POST | `/api/flux/analyser-schema` | Analyser schéma CSV |
| POST | `/api/flux/comparer` | Comparer deux CSV |
| GET | `/api/flux/historique/<flux_id>` | Historique des écarts |
| PATCH | `/api/flux/ecart/<id>/statut` | Màj statut écart |

### Analyse
| Méthode | Endpoint | Rôle |
|---------|----------|------|
| POST | `/api/analyze` | Lancer une analyse |
| GET | `/api/history` | Historique des analyses |
| GET | `/api/history/latest` | Dernière analyse par flux |
| GET | `/api/history/<id>` | Détail d'une analyse |
| DELETE | `/api/history/<id>` | Supprimer une analyse |
| GET | `/api/analysis/<id>/anomalies` | Anomalies paginées |
| GET | `/api/analysis/<id>/export/excel` | Exporter en Excel |
| GET | `/api/stats` | Statistiques par flux |
| GET | `/api/reporting` | Rapport détaillé |

### Alertes
| Méthode | Endpoint | Rôle |
|---------|----------|------|
| GET | `/alert/<token>` | Page alerte (publique) |
| GET | `/alert/<token>/ack` | Acquittement par email |
| GET | `/alert/<token>/progress` | En cours par email |
| GET | `/api/alerts` | Lister les alertes |
| GET | `/api/alerts/<token>` | Détail alerte |
| PATCH | `/api/alerts/<token>/status` | Màj statut |
| POST | `/api/alerts/<token>/track` | Tracker depuis le dashboard |
| POST | `/api/alerts/<token>/verify` | Vérifier résolution |
| POST | `/api/alerts/<token>/resolve` | Résoudre (machine à états) |
| POST | `/api/alerts/<token>/escalate` | Escalader |
| POST | `/api/alerts/<token>/feedback` | Feedback suggestion IA |
| GET | `/api/alerts/<token>/suggest` | Suggestion IA |
| GET | `/api/users/consultants` | Liste consultants |

### Comparaison Intelligente
| Méthode | Endpoint | Rôle |
|---------|----------|------|
| POST | `/api/smart/preview` | Prévisualiser colonnes |
| POST | `/api/smart/run` | Comparaison intelligente |
| POST | `/api/smart/learn` | Sauvegarder mapping |
| POST | `/api/smart/analyze-anomalies` | Analyse IA des anomalies |
| GET | `/api/smart/mappings` | Mappings sauvegardés |
| POST | `/api/smart/run-async` | Comparaison asynchrone |
| GET | `/api/smart/jobs/<id>` | Statut job |
| GET | `/api/smart/jobs/<id>/result` | Résultat job |
| GET | `/api/smart/test-ai` | Test connexion IA |

### Assistant IA
| Méthode | Endpoint | Rôle |
|---------|----------|------|
| POST | `/api/assistant/chat` | Chat avec l'assistant |
| POST | `/api/assistant/chat-stream` | Chat streaming (SSE) |
| GET | `/api/assistant/conversations` | Lister conversations |
| GET | `/api/assistant/conversations/<id>` | Détail conversation |
| DELETE | `/api/assistant/conversations/<id>` | Supprimer conversation |
| GET | `/api/assistant/status` | Statut de l'assistant |
| GET | `/api/assistant/suggestions` | Suggestions |

### Rapports
| Méthode | Endpoint | Rôle |
|---------|----------|------|
| GET | `/api/report/daily` | Rapport quotidien Excel |
| GET | `/api/report/monthly` | Rapport mensuel Excel |
| GET | `/api/report/by-division` | Rapports par division (ZIP) |
| GET | `/api/report/divisions` | Divisions disponibles |
| GET | `/api/report/customerbalance` | Rapport CustomerBalance JSON |
| GET | `/api/report/customerbalance/csv` | Rapport CustomerBalance CSV |
| GET | `/api/report/customerbalance/excel` | Rapport CustomerBalance Excel |
| GET | `/api/customerbalance/report` | Rapport CustomerBalance dédié |

### Système
| Méthode | Endpoint | Rôle |
|---------|----------|------|
| GET | `/health` | Santé de l'app |
| GET | `/api/system/health` | Santé détaillée (admin) |
| GET | `/api/system/metrics` | Métriques système (admin) |
| GET | `/api/system/audit` | Journal d'audit (admin) |
| GET | `/api/system/perf` | Métriques performance |

### WebSocket
| Endpoint | Rôle |
|----------|------|
| `WS /ws/alerts` | Alertes et jobs en temps réel |

## Déploiement Azure

Pipeline CI/CD dans `azure-pipelines.yml` :
- Build du frontend React
- Build et push Docker sur ACR (`flasktraineracr.azurecr.io/pfeamani`)
- Déploiement sur Azure App Service

Variables requises en production :

```env
FLASK_ENV=production
SECRET_KEY=<secret>
STORAGE_BACKEND=azure
AZURE_SQL_CONNECTION_STRING=<...>
AZURE_STORAGE_CONNECTION_STRING=<...>
NVIDIA_API_KEY=nvapi-<...>
```

## Scripts utilitaires

| Script | Rôle |
|--------|------|
| `backend/test_architecture.py` | Validation machine à états, SLA, storage |
| `backend/test_blob.py` | Test de connectivité Azure Blob |
| `backend/migrate_stats_columns.py` | Migration des colonnes de statistiques en base |
| `backend/analyse_customer_balance.py` | Analyse autonome du flux CustomerBalance |

## Projet

PFE — Flux Monitor, TimSoft Group
