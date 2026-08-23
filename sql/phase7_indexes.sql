-- ============================================================================
-- PHASE 7 — INDEXES P0 (à exécuter MANUELLEMENT sur la base de production)
-- ============================================================================
--
-- ⚠️  CE SCRIPT NE DOIT PAS ÊTRE EXÉCUTÉ AUTOMATIQUEMENT.
--     Revue + exécution manuelle par l'exploitant, idéalement pendant un
--     moment calme (les CREATE INDEX InnoDB sont online/INPLACE sur MySQL 5.6+,
--     mais restent coûteux en I/O sur les grosses tables).
--
-- Contenu :
--   ÉTAPE 1 : préflight — comptages de lignes + taille des tables (lecture seule)
--   ÉTAPE 2 : création idempotente de 3 index (via INFORMATION_SCHEMA +
--             PREPARE/EXECUTE, équivalent portable de « CREATE INDEX IF NOT
--             EXISTS », qui n'existe pas en MySQL < 8.0.x... et pas du tout
--             pour les index). Ré-exécutable sans erreur.
--
-- Index ajoutés (audit P0) :
--   alerts         (workflow_status, created_at)
--   alert_tracking (alert_token, created_at)
--   alert_history  (alert_token, to_status, created_at)
--
-- Testé sur MySQL 8 en conteneur jetable (schéma recréé depuis
-- sql/create_tables.sql à jour) ; voir la branche phase7-database.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- ÉTAPE 1 — PRÉFLIGHT (lecture seule : à lancer d'abord pour arbitrer le timing)
-- ---------------------------------------------------------------------------
-- Règle empirique : en dessous de ~100k lignes l'index est quasi instantané ;
-- au-delà, prévoir quelques secondes à minutes d'I/O selon le matériel.

SELECT
    t.TABLE_NAME                                   AS table_name,
    t.TABLE_ROWS                                   AS approx_rows,
    ROUND(t.DATA_LENGTH  / 1024 / 1024, 1)         AS data_mb,
    ROUND(t.INDEX_LENGTH / 1024 / 1024, 1)         AS index_mb
FROM INFORMATION_SCHEMA.TABLES t
WHERE t.TABLE_SCHEMA = DATABASE()
  AND t.TABLE_NAME IN ('alerts', 'alert_tracking', 'alert_history')
ORDER BY t.TABLE_NAME;

-- Comptages exacts (TABLE_ROWS ci-dessus est une estimation InnoDB)
SELECT 'alerts'         AS table_name, COUNT(*) AS exact_rows FROM alerts
UNION ALL
SELECT 'alert_tracking', COUNT(*) FROM alert_tracking
UNION ALL
SELECT 'alert_history',  COUNT(*) FROM alert_history;


-- ---------------------------------------------------------------------------
-- ÉTAPE 2 — CRÉATION IDEMPOTENTE DES INDEX
-- ---------------------------------------------------------------------------

-- 2.1) alerts (workflow_status, created_at)
SET @idx_exists := (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'alerts'
      AND INDEX_NAME   = 'idx_alerts_workflow_created'
);
SET @ddl := IF(
    @idx_exists = 0,
    'CREATE INDEX idx_alerts_workflow_created ON `alerts` (`workflow_status`, `created_at`)',
    'SELECT ''SKIP: idx_alerts_workflow_created existe deja'' AS info'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2.2) alert_tracking (alert_token, created_at)
SET @idx_exists := (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'alert_tracking'
      AND INDEX_NAME   = 'idx_alert_tracking_token_created'
);
SET @ddl := IF(
    @idx_exists = 0,
    'CREATE INDEX idx_alert_tracking_token_created ON `alert_tracking` (`alert_token`, `created_at`)',
    'SELECT ''SKIP: idx_alert_tracking_token_created existe deja'' AS info'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2.3) alert_history (alert_token, to_status, created_at)
SET @idx_exists := (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'alert_history'
      AND INDEX_NAME   = 'idx_alert_history_token_status_created'
);
SET @ddl := IF(
    @idx_exists = 0,
    'CREATE INDEX idx_alert_history_token_status_created ON `alert_history` (`alert_token`, `to_status`, `created_at`)',
    'SELECT ''SKIP: idx_alert_history_token_status_created existe deja'' AS info'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;


-- ---------------------------------------------------------------------------
-- ÉTAPE 3 — VÉRIFICATION POST-CRÉATION
-- ---------------------------------------------------------------------------

SELECT TABLE_NAME, INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns_
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND INDEX_NAME IN (
      'idx_alerts_workflow_created',
      'idx_alert_tracking_token_created',
      'idx_alert_history_token_status_created'
  )
GROUP BY TABLE_NAME, INDEX_NAME
ORDER BY TABLE_NAME, INDEX_NAME;
