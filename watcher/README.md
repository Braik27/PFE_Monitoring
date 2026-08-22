# Watcher de Flux & Détecteur de Fichiers Manquants

Ce composant externe automatise le déclenchement des analyses et la détection de "fichiers manquants" pour la plateforme **Flux Monitor**.

## Fonctionnalités

1. **Surveillance en temps réel** : Utilise la bibliothèque `watchdog` pour surveiller un dossier configuré. Dès qu'un couple de fichiers `cegid.csv` et `oracle.csv` est détecté dans un sous-dossier, le watcher :
   - Déplace les fichiers dans un sous-dossier `processed/` (avec un préfixe d'horodatage pour éviter le traitement multiple ou les boucles d'événements).
   - Envoie une requête d'analyse `POST /api/flux/comparer` authentifiée via un compte technique dédié (`watcher_agent`).
2. **Détection de retards (Missing files)** : Planifie une tâche (via la bibliothèque `schedule`) qui vérifie régulièrement les heures limites configurées dans la table `expected_flux` :
   - Si les fichiers ne sont pas arrivés après l'heure configurée, et qu'aucun traitement réussi ou alerte n'a été enregistré pour la journée en cours, le watcher appelle `POST /api/alerts/manual` pour générer une alerte critique `FICHIER_MANQUANT`.

---

## Configuration

La configuration s'effectue via des variables d'environnement définies dans un fichier `.env` à la racine du projet ou directement dans le dossier `watcher`.

Exemple de variables supportées :
```env
# URL de l'API Flux Monitor
API_BASE_URL=http://127.0.0.1:5000

# Dossier à surveiller
WATCHER_DIR=c:/Users/USER/Desktop/Full/watcher/watch_folder

# Compte technique de l'agent watcher
WATCHER_USER=watcher_agent
WATCHER_PASSWORD=watcher_pass_123

# Configuration DB de secours pour lecture directe d'expected_flux
STORAGE_BACKEND=local
LOCAL_DB_PATH=c:/Users/USER/Desktop/Full/flux_monitor.db
```

---

## Lancement

### 1. Installation des dépendances

Depuis le dossier `watcher`, installez les modules requis :
```bash
pip install -r requirements.txt
```

### 2. Démarrage du Watcher

Lancez simplement le script :
```bash
python watcher.py
```

Les logs s'affichent dans le terminal et sont également enregistrés dans `watcher/watcher.log`.

---

## Exécution des Tests

Pour exécuter les tests unitaires pytest (qui valident la logique décisionnelle pure indépendamment de l'I/O réseau ou de la base de données) :

```bash
pytest test_watcher_logic.py -v
```
