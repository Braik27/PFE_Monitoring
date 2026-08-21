# Analyse et Recommandations du Processus d'Alertes et SLA

Ce document présente une cartographie détaillée du processus actuel de gestion des alertes et du respect des SLA (Service Level Agreements), basée sur l'analyse statique du code source du projet. Il détaille également les incohérences identifiées et propose des recommandations concrètes et priorisées.

---

## 1. Cartographie du Processus Actuel (Observé dans le Code)

Le cycle de vie d'une alerte comprend les étapes suivantes dans l'application :

### A. Déclenchement de l'Alerte
- **Automatique** : Lors de l'exécution du pipeline de réconciliation (`engine/pipeline.py`), si des anomalies sont détectées (`result.total_anomalies > 0`) et que le nombre d'anomalies critiques dépasse le seuil paramétré (défini par `ALERT_MIN_CRITIQUES` ou 1 par défaut), la fonction `send_alert_async` de `backend/core/email_alert.py` est appelée.
- **Manuel** : Via le point d'accès `POST /api/alerts/manual` dans `backend/api/alerts_api.py`, principalement pour signaler des fichiers ERP manquants.
- **Action de sauvegarde** : Un token unique est généré (`uuid.uuid4().hex`) et l'alerte est insérée en base de données via `save_alert` avec le statut initialisé à `"NEW"`.

### B. Notification et Escalade
- Un email est envoyé à l'adresse configurée dans `ALERT_EMAIL_TO` contenant des liens directs vers les actions rapides (Prendre en charge, Ignorer/En cours) pointant vers `/alert/<token>/ack` et `/alert/<token>/ignore`.
- **Escalade** : Un utilisateur peut escalader une alerte existante via `POST /api/alerts/<token>/escalate` en renseignant un email cible (qui doit correspondre à un utilisateur de rôle `consultant`, `team_leader` ou `admin`). Le statut passe à `"ESCALATED"`, et un email d'escalade est envoyé au destinataire.

### C. Prise en Charge et Traitement
- L'utilisateur peut cliquer sur "Je prends en charge" dans l'email ou cliquer sur le bouton correspondant sur le panel détaillé de l'UI. Le statut passe à `"ACKNOWLEDGED"`.
- Une action d'historique (tracking) est enregistrée dans la table `alert_tracking`.

### D. Résolution et Clôture
- L'alerte est résolue via `POST /api/alerts/<token>/resolve` (ou PATCH status à `RESOLVED`).
- L'utilisateur doit fournir une explication textuelle (solution appliquée) qui est enregistrée dans le tracking. Le statut passe à `"RESOLVED"`.
- *Le statut `"CLOSED"` est défini dans le code de la machine d'état mais n'est jamais assigné.*

### E. Suivi et Calcul du SLA
Il existe **deux systèmes de suivi de SLA distincts** dans le code :
1. **Actif en Production (APScheduler - `core/scheduler.py`)** :
   - Une tâche récurrente (`check_sla_breaches`) s'exécute toutes les 5 minutes.
   - Elle récupère les alertes non résolues et non clôturées. Si une alerte de statut `NEW`, `ACKNOWLEDGED` ou `IN_PROGRESS` a été créée il y a **plus de 4 heures**, elle est marquée comme `sla_breached = 1`, un enregistrement de tracking `SLA_BREACHED` est inséré, et un email d'alerte de dépassement est envoyé.
2. **Inactif en Production (`core/sla_monitor.py`)** :
   - Un système de calcul dynamique de SLA calcule le délai théorique en fonction du type de flux, de la sévérité (nombre d'erreurs critiques/warnings), du backlog général et de l'état d'escalade.
   - Cette tâche n'est jamais lancée par `app.py` en production.

---

## 2. Incohérences et Manques Identifiés

1. **Le statut `CLOSED` est orphelin** : Bien que défini dans `alert_state_machine.py` comme l'état final succédant à `RESOLVED`, aucun traitement automatique ou action manuelle ne passe les alertes au statut `CLOSED`. Elles restent indéfiniment au statut `RESOLVED`.
2. **Contournement de la Machine d'États** : Les contrôles de transition de `alert_state_machine.py` ne sont appliqués que lors de la résolution de l'alerte via le endpoint `/resolve`. Les autres changements de statut (par exemple via le endpoint PATCH `/api/alerts/<token>/status`) modifient directement la base de données sans valider la transition par la machine d'états.
3. **Doublon et Inactivité du SLA Dynamique** : Deux logiques de SLA coexistent. La logique de SLA dynamique (`sla_monitor.py`) est plus riche mais inactive, tandis que la vérification basique des 4 heures (`scheduler.py`) est active mais rigide.
4. **Pas de relance automatique** : Si une alerte est prise en charge (`ACKNOWLEDGED`) mais n'évolue pas pendant plusieurs heures, aucun mécanisme ne relance l'utilisateur ou n'alerte le responsable avant le dépassement effectif du SLA.
5. **Absence de suivi visuel global** : L'interface utilisateur ne présente pas de vue consolidée du temps restant par alerte (les comptes à rebours ne sont visibles que lorsqu'on sélectionne une alerte individuellement).

---

## 3. Tableau Comparatif : Comportement Actuel vs Recommandé

| Étape du processus | Comportement Actuel | Comportement Recommandé |
| :--- | :--- | :--- |
| **Création d'Alerte** | Statut `"NEW"`. SLA de 4h rigide vérifié par tâche de fond. | Statut `"NEW"`. Enregistrement d'un timestamp de deadline SLA calculé dynamiquement dès la création. |
| **Prise en Charge** | Statut passe à `"ACKNOWLEDGED"`. Aucune limitation de durée dans cet état. | Statut passe à `"ACKNOWLEDGED"`. Si aucune action après 2h, envoi d'un email de rappel ou ré-escalade automatique. |
| **Ignorer l'alerte** | Non implémenté ou partiel (anciennement bouton "En cours"). | Statut passe à `"IGNORED"`. Notification automatique immédiate à la personne ayant escaladé l'alerte. |
| **Résolution** | Statut passe à `"RESOLVED"`. Requiert un commentaire de résolution. | Statut passe à `"RESOLVED"`. Requiert un commentaire et clôture automatique après 24h sans réouverture. |
| **Clôture** | Jamais effectuée (statut `"CLOSED"` inutilisé). | Passage automatique de `"RESOLVED"` à `"CLOSED"` par une tâche de fond après un délai de garde (ex: 24h ou 48h). |
| **Audit des transitions** | Tracking partiel dans `alert_tracking`. La table `alert_history` est inutilisée. | Enregistrement systématique de chaque transition d'état dans `alert_history` avec identifiant de l'auteur, ancien statut, nouveau statut, et horodatage. |

---

## 4. Recommandations Concrètes et Priorisées

### A. Quick Wins (Corrections Rapides et Sûres)
1. **Activer la clôture automatique des alertes résolues** :
   - Ajouter une tâche planifiée dans `core/scheduler.py` qui s'exécute quotidiennement pour passer au statut `"CLOSED"` toutes les alertes restées au statut `"RESOLVED"` depuis plus de 24 heures.
2. **Centraliser les transitions via la Machine d'États** :
   - Modifier le endpoint `PATCH /api/alerts/<token>/status` pour qu'il utilise la fonction `transition_alert` de `alert_state_machine.py` plutôt que de modifier directement la base de données.
3. **Sécuriser la table d'Audit** :
   - Écrire systématiquement dans `alert_history` lors de chaque changement d'état via `transition_alert` afin de garantir une traçabilité totale.

### B. Améliorations Structurelles (Moyen Terme)
1. **Unifier la gestion des SLA** :
   - Supprimer le job redondant `check_sla_breaches` dans `scheduler.py` au profit de l'activation officielle du planificateur de `core/sla_monitor.py`.
   - Utiliser la deadline calculée dynamiquement par `compute_sla_deadline` et l'enregistrer en base lors de la création de l'alerte.
2. **Alertes de pré-dépassement** :
   - Ajouter une notification (email/push) lorsque le temps restant avant le dépassement du SLA est inférieur à 20% (environ 48 minutes pour un SLA de 4h), afin de permettre une réaction proactive.
3. **Dashboard de Suivi SLA** :
   - Ajouter un onglet ou un tableau de bord visuel listant toutes les alertes actives triées par urgence de SLA (temps restant avant dépassement), avec un code couleur clair (Vert > 2h, Orange < 1h, Rouge dépassé).
