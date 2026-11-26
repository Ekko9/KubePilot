-- KubePilot DevOps v1.0.0 - MySQL 5.7.22+/8.0 初始化结构
-- 用法：mysql -h <host> -P <port> -u <user> -p < sql/init.sql
-- 本文件仅创建数据库结构，不包含数据库密码、平台管理员密码或其他密钥。
-- 应用首次启动时会创建初始管理员及内置通知模板。
-- 数据库只保存密码哈希；至少 8 位且包含大小写字母、数字和特殊字符的强度规则由应用统一校验。

CREATE DATABASE IF NOT EXISTS `kubepilot_devops`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
USE `kubepilot_devops`;

CREATE TABLE IF NOT EXISTS `users` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(80) NOT NULL,
  `display_name` VARCHAR(120) NOT NULL DEFAULT '',
  `password_hash` VARCHAR(255) NOT NULL,
  `email` VARCHAR(255) NOT NULL DEFAULT '',
  `phone` VARCHAR(40) NOT NULL DEFAULT '',
  `is_admin` BOOLEAN NOT NULL DEFAULT FALSE,
  `role` VARCHAR(32) NOT NULL DEFAULT 'operator',
  `menu_permissions` JSON NULL,
  `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
  `must_change_password` BOOLEAN NOT NULL DEFAULT TRUE,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_username` (`username`),
  KEY `ix_users_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `deployment_records` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `namespace` VARCHAR(120) NOT NULL,
  `workload` VARCHAR(180) NOT NULL,
  `image` VARCHAR(500) NOT NULL,
  `replicas` INT NOT NULL DEFAULT 1,
  `revision` INT NULL,
  `status` VARCHAR(40) NOT NULL DEFAULT 'submitted',
  `strategy` VARCHAR(40) NOT NULL DEFAULT 'RollingUpdate',
  `message` TEXT NOT NULL,
  `operator` VARCHAR(80) NOT NULL DEFAULT 'system',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_deployment_records_namespace` (`namespace`),
  KEY `ix_deployment_records_workload` (`workload`),
  KEY `ix_deployment_records_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `build_configurations` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(180) NOT NULL,
  `description` VARCHAR(500) NOT NULL DEFAULT '',
  `enabled` BOOLEAN NOT NULL DEFAULT TRUE,
  `git_url` VARCHAR(600) NOT NULL,
  `git_ref` VARCHAR(180) NOT NULL DEFAULT 'main',
  `context_dir` VARCHAR(300) NOT NULL DEFAULT '.',
  `dockerfile` VARCHAR(300) NOT NULL DEFAULT 'Dockerfile',
  `destination` VARCHAR(600) NOT NULL,
  `namespace` VARCHAR(120) NOT NULL DEFAULT 'devops-platform',
  `git_secret_name` VARCHAR(180) NOT NULL DEFAULT '',
  `git_auth_secret_name` VARCHAR(180) NOT NULL DEFAULT '',
  `registry_secret_name` VARCHAR(180) NOT NULL DEFAULT '',
  `build_args` JSON NOT NULL,
  `timeout_seconds` INT NOT NULL DEFAULT 1800,
  `allow_concurrent` BOOLEAN NOT NULL DEFAULT FALSE,
  `retention_count` INT NOT NULL DEFAULT 20,
  `created_by` VARCHAR(80) NOT NULL DEFAULT 'system',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_build_configurations_name` (`name`),
  KEY `ix_build_configurations_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `image_builds` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(180) NOT NULL,
  `config_id` INT NULL,
  `git_url` VARCHAR(600) NOT NULL,
  `git_ref` VARCHAR(180) NOT NULL DEFAULT 'main',
  `context_dir` VARCHAR(300) NOT NULL DEFAULT '.',
  `dockerfile` VARCHAR(300) NOT NULL DEFAULT 'Dockerfile',
  `destination` VARCHAR(600) NOT NULL,
  `namespace` VARCHAR(120) NOT NULL DEFAULT 'devops-platform',
  `build_args` JSON NOT NULL,
  `job_name` VARCHAR(180) NOT NULL DEFAULT '',
  `status` VARCHAR(40) NOT NULL DEFAULT 'pending',
  `message` TEXT NOT NULL,
  `created_by` VARCHAR(80) NOT NULL DEFAULT 'system',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `finished_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  KEY `ix_image_builds_name` (`name`),
  KEY `ix_image_builds_config_id` (`config_id`),
  KEY `ix_image_builds_destination` (`destination`),
  CONSTRAINT `fk_image_builds_config` FOREIGN KEY (`config_id`)
    REFERENCES `build_configurations` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `alert_rule_templates` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(180) NOT NULL,
  `description` VARCHAR(500) NOT NULL DEFAULT '',
  `metric` VARCHAR(80) NOT NULL,
  `operator` VARCHAR(8) NOT NULL DEFAULT '>',
  `threshold` DOUBLE NOT NULL DEFAULT 80,
  `duration_seconds` INT NOT NULL DEFAULT 60,
  `repeat_interval_minutes` INT NOT NULL DEFAULT 30,
  `severity` VARCHAR(20) NOT NULL DEFAULT 'warning',
  `namespace` VARCHAR(120) NOT NULL DEFAULT '*',
  `target_pattern` VARCHAR(180) NOT NULL DEFAULT '*',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_alert_rule_templates_name` (`name`),
  KEY `ix_alert_rule_templates_metric` (`metric`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `alert_rules` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(180) NOT NULL,
  `metric` VARCHAR(80) NOT NULL,
  `operator` VARCHAR(8) NOT NULL DEFAULT '>',
  `threshold` DOUBLE NOT NULL,
  `duration_seconds` INT NOT NULL DEFAULT 60,
  `repeat_interval_minutes` INT NOT NULL DEFAULT 30,
  `severity` VARCHAR(20) NOT NULL DEFAULT 'warning',
  `namespace` VARCHAR(120) NOT NULL DEFAULT '*',
  `target_pattern` VARCHAR(180) NOT NULL DEFAULT '*',
  `template_id` INT NULL,
  `channel_ids` JSON NOT NULL,
  `enabled` BOOLEAN NOT NULL DEFAULT TRUE,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_alert_rules_name` (`name`),
  KEY `ix_alert_rules_metric` (`metric`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `notification_channels` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(180) NOT NULL,
  `channel_type` VARCHAR(40) NOT NULL,
  `config_encrypted` TEXT NOT NULL,
  `enabled` BOOLEAN NOT NULL DEFAULT TRUE,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_notification_channels_name` (`name`),
  KEY `ix_notification_channels_type` (`channel_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `alert_events` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `rule_id` INT NULL,
  `fingerprint` VARCHAR(255) NOT NULL,
  `title` VARCHAR(300) NOT NULL,
  `message` TEXT NOT NULL,
  `severity` VARCHAR(20) NOT NULL DEFAULT 'warning',
  `status` VARCHAR(20) NOT NULL DEFAULT 'firing',
  `target` VARCHAR(300) NOT NULL DEFAULT '',
  `value` DOUBLE NULL,
  `started_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `resolved_at` DATETIME NULL,
  `last_notified_at` DATETIME NULL,
  `assignee` VARCHAR(80) NOT NULL DEFAULT '',
  `handling_note` TEXT NOT NULL,
  `acknowledged_at` DATETIME NULL,
  `resolved_by` VARCHAR(80) NOT NULL DEFAULT '',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_alert_events_rule_id` (`rule_id`),
  KEY `ix_alert_events_fingerprint` (`fingerprint`),
  KEY `ix_alert_events_status` (`status`),
  CONSTRAINT `fk_alert_events_rule` FOREIGN KEY (`rule_id`)
    REFERENCES `alert_rules` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `incident_activities` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `event_id` INT NOT NULL,
  `action` VARCHAR(40) NOT NULL DEFAULT 'update',
  `from_status` VARCHAR(20) NOT NULL DEFAULT '',
  `to_status` VARCHAR(20) NOT NULL DEFAULT '',
  `assignee` VARCHAR(80) NOT NULL DEFAULT '',
  `note` TEXT NOT NULL,
  `operator` VARCHAR(80) NOT NULL DEFAULT 'system',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_incident_activities_event_id` (`event_id`),
  KEY `ix_incident_activities_created_at` (`created_at`),
  CONSTRAINT `fk_incident_activities_event` FOREIGN KEY (`event_id`)
    REFERENCES `alert_events` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `alert_subscriptions` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(180) NOT NULL,
  `user_id` INT NOT NULL,
  `rule_id` INT NULL,
  `channel_ids` JSON NOT NULL,
  `enabled` BOOLEAN NOT NULL DEFAULT TRUE,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_alert_subscriptions_user_id` (`user_id`),
  KEY `ix_alert_subscriptions_rule_id` (`rule_id`),
  CONSTRAINT `fk_alert_subscriptions_user` FOREIGN KEY (`user_id`)
    REFERENCES `users` (`id`),
  CONSTRAINT `fk_alert_subscriptions_rule` FOREIGN KEY (`rule_id`)
    REFERENCES `alert_rules` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `notification_templates` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(180) NOT NULL,
  `channel_type` VARCHAR(40) NOT NULL,
  `title_template` VARCHAR(500) NOT NULL DEFAULT '{title}',
  `message_template` TEXT NOT NULL,
  `created_by` VARCHAR(80) NOT NULL DEFAULT 'system',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_notification_templates_name` (`name`),
  KEY `ix_notification_templates_type` (`channel_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `notification_records` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `channel_id` INT NULL,
  `channel_name` VARCHAR(180) NOT NULL DEFAULT '',
  `channel_type` VARCHAR(40) NOT NULL DEFAULT '',
  `event_id` INT NULL,
  `title` VARCHAR(500) NOT NULL,
  `message` TEXT NOT NULL,
  `status` VARCHAR(20) NOT NULL DEFAULT 'sent',
  `error` TEXT NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_notification_records_channel_id` (`channel_id`),
  KEY `ix_notification_records_event_id` (`event_id`),
  KEY `ix_notification_records_status` (`status`),
  KEY `ix_notification_records_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `on_call_shifts` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `user_id` INT NOT NULL,
  `starts_at` DATETIME NOT NULL,
  `ends_at` DATETIME NOT NULL,
  `note` VARCHAR(500) NOT NULL DEFAULT '',
  `created_by` VARCHAR(80) NOT NULL DEFAULT 'system',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_on_call_shifts_user_id` (`user_id`),
  KEY `ix_on_call_shifts_starts_at` (`starts_at`),
  KEY `ix_on_call_shifts_ends_at` (`ends_at`),
  CONSTRAINT `fk_on_call_shifts_user` FOREIGN KEY (`user_id`)
    REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `resource_revisions` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `namespace` VARCHAR(120) NOT NULL,
  `api_version` VARCHAR(120) NOT NULL,
  `kind` VARCHAR(120) NOT NULL,
  `name` VARCHAR(253) NOT NULL,
  `version` INT NOT NULL,
  `manifest` JSON NOT NULL,
  `action` VARCHAR(40) NOT NULL DEFAULT 'update',
  `created_by` VARCHAR(80) NOT NULL DEFAULT 'system',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_resource_revisions_namespace` (`namespace`),
  KEY `ix_resource_revisions_api_version` (`api_version`),
  KEY `ix_resource_revisions_kind` (`kind`),
  KEY `ix_resource_revisions_name` (`name`),
  KEY `ix_resource_revisions_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `audit_logs` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(80) NOT NULL,
  `display_name` VARCHAR(120) NOT NULL DEFAULT '',
  `action` VARCHAR(120) NOT NULL,
  `resource` VARCHAR(300) NOT NULL DEFAULT '',
  `detail` JSON NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_audit_logs_username` (`username`),
  KEY `ix_audit_logs_display_name` (`display_name`),
  KEY `ix_audit_logs_action` (`action`),
  KEY `ix_audit_logs_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

