-- =====================================================================
-- SCRIPT DE CRÉATION DES TABLES MYSQL — PROJET MONITORING
-- =====================================================================
-- A coller directement dans l'onglet SQL de phpMyAdmin
-- =====================================================================

-- ---------------------------------------------------------------------
-- BRONZE (Données brutes, configurations et logs système)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `users` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `username` VARCHAR(100) NOT NULL UNIQUE,
    `password_hash` VARCHAR(255) NOT NULL,
    `role` VARCHAR(50) NOT NULL DEFAULT 'analyst',
    `email` VARCHAR(150) DEFAULT '',
    `avatar` VARCHAR(255) DEFAULT '',
    `full_name` VARCHAR(150) DEFAULT '',
    `active` TINYINT DEFAULT 1,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `divisions` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `code` VARCHAR(50) NOT NULL UNIQUE,
    `name` VARCHAR(150) NOT NULL,
    `country` VARCHAR(10),
    `flag` VARCHAR(20),
    `active` TINYINT DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `expected_flux` (
    `flux_id` VARCHAR(100) PRIMARY KEY,
    `division` VARCHAR(50) NOT NULL,
    `expected_hour` VARCHAR(10) NOT NULL,
    `source_path` VARCHAR(500) NOT NULL,
    `active` TINYINT DEFAULT 1,
    `last_check_at` VARCHAR(100) NULL,
    `last_status` VARCHAR(100) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `smart_mappings` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `flux_key` VARCHAR(100) NOT NULL,
    `cegid_col` VARCHAR(100) NOT NULL,
    `oracle_col` VARCHAR(100) NOT NULL,
    `usage_count` INT DEFAULT 1,
    `last_used` DATETIME NULL,
    `created_by` VARCHAR(100) NULL,
    UNIQUE KEY `uq_mappings` (`flux_key`, `cegid_col`, `oracle_col`),
    INDEX `idx_smart_mappings_key` (`flux_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `jobs` (
    `id` VARCHAR(255) PRIMARY KEY,
    `job_type` VARCHAR(100) NOT NULL,
    `status` VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    `progress` INT DEFAULT 0,
    `step_label` VARCHAR(255) NULL,
    `result_json` LONGTEXT NULL,
    `error` TEXT NULL,
    `meta_json` TEXT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `started_at` DATETIME NULL,
    `ended_at` DATETIME NULL,
    `flux_id` VARCHAR(100) NULL,
    `blob_cegid` VARCHAR(500) NULL,
    `blob_oracle` VARCHAR(500) NULL,
    `analyst` VARCHAR(100) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `conversations` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `user_id` VARCHAR(100) NOT NULL,
    `title` VARCHAR(255) NOT NULL DEFAULT 'Nouvelle conversation',
    `summary` TEXT NULL,
    `msg_count` INT DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `messages` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `conversation_id` INT NOT NULL,
    `role` VARCHAR(50) NOT NULL,
    `content` LONGTEXT NOT NULL,
    `context_keys` TEXT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `user_patterns` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `user_id` VARCHAR(100) NOT NULL,
    `pattern` VARCHAR(255) NOT NULL,
    `count` INT DEFAULT 1,
    `last_seen` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uq_user_pattern` (`user_id`, `pattern`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- SILVER (Données nettoyées, suivi opérationnel et alertes)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `alerts` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `token` VARCHAR(100) NOT NULL UNIQUE,
    `analysis_id` INT NULL,
    `flux_id` VARCHAR(100) NOT NULL,
    `flux_name` VARCHAR(255) NOT NULL DEFAULT '',
    `label` VARCHAR(255) NOT NULL DEFAULT '',
    `n_critiques` INT DEFAULT 0,
    `n_warnings` INT DEFAULT 0,
    `concordance` DOUBLE DEFAULT 100.0,
    `anomalies_json` LONGTEXT NOT NULL,
    `status` VARCHAR(50) NOT NULL DEFAULT 'NEW',
    `email_sent_to` VARCHAR(255) DEFAULT '',
    `sla_breached` TINYINT DEFAULT 0,
    `sla_deadline` VARCHAR(100) NULL,
    `sla_hours` DOUBLE NULL,
    `remaining_pct` DOUBLE NULL,
    `flux_type` VARCHAR(50) NULL,
    `severity_class` VARCHAR(20) NULL,
    `detected_at` VARCHAR(100) NULL,
    `expected_hour` VARCHAR(10) NULL,
    `detection_latency_minutes` DOUBLE NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_alerts_token` (`token`),
    INDEX `idx_alerts_flux_id` (`flux_id`),
    INDEX `idx_alerts_status` (`status`),
    INDEX `idx_alerts_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `alert_tracking` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `alert_token` VARCHAR(100) NOT NULL,
    `username` VARCHAR(100) NOT NULL DEFAULT 'system',
    `action` VARCHAR(100) NOT NULL,
    `comment` TEXT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `alert_history` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `alert_token` VARCHAR(100) NOT NULL,
    `username` VARCHAR(100) NOT NULL,
    `from_status` VARCHAR(50) NULL,
    `to_status` VARCHAR(50) NOT NULL,
    `comment` TEXT NOT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_alert_history_token` (`alert_token`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ia_feedbacks` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `alert_token` VARCHAR(100) NOT NULL,
    `flux_id` VARCHAR(100) NOT NULL DEFAULT '',
    `flux_name` VARCHAR(255) NOT NULL DEFAULT '',
    `n_critiques` INT DEFAULT 0,
    `n_warnings` INT DEFAULT 0,
    `anomalies_json` LONGTEXT NULL,
    `action_taken` VARCHAR(255) NOT NULL DEFAULT '',
    `resolution_hours` DOUBLE NULL,
    `feedback_score` INT NOT NULL DEFAULT 3,
    `feedback_comment` TEXT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `correction_history` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `flux_id` VARCHAR(100) NOT NULL,
    `error_type` VARCHAR(100) NOT NULL,
    `column_name` VARCHAR(100) NULL,
    `solution_applied` TEXT NOT NULL,
    `was_effective` TINYINT DEFAULT 1,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- GOLD (Résultats consolidés de comparaison)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `analyses` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `flux_id` VARCHAR(100) NOT NULL,
    `label` VARCHAR(255) NOT NULL DEFAULT '',
    `summary` LONGTEXT NOT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_analyses_flux` (`flux_id`),
    INDEX `idx_analyses_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ecarts` (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `timestamp` VARCHAR(100) NULL,
    `flux_id` VARCHAR(100) NULL,
    `article_id` VARCHAR(255) NULL,
    `type_ecart` VARCHAR(100) NULL,
    `colonne` VARCHAR(100) NULL,
    `valeur_cegid` TEXT NULL,
    `valeur_oracle` TEXT NULL,
    `details` TEXT NULL,
    `statut` VARCHAR(50) DEFAULT 'nouveau',
    INDEX `idx_ecarts_flux` (`flux_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
