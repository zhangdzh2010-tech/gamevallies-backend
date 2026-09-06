-- CreateTable
CREATE TABLE `users` (
    `id` VARCHAR(36) NOT NULL,
    `username` VARCHAR(32) NOT NULL,
    `display_name` VARCHAR(64) NULL,
    `avatar_url` VARCHAR(191) NULL,
    `email` VARCHAR(255) NULL,
    `phone` VARCHAR(20) NULL,
    `password_hash` VARCHAR(191) NULL,
    `auth_provider` ENUM('email', 'phone', 'wechat', 'google', 'apple') NOT NULL DEFAULT 'email',
    `wx_open_id` VARCHAR(191) NULL,
    `wx_union_id` VARCHAR(191) NULL,
    `role` ENUM('user', 'creator', 'moderator', 'admin') NOT NULL DEFAULT 'user',
    `is_pro` BOOLEAN NOT NULL DEFAULT false,
    `pro_expires` DATETIME(3) NULL,
    `bio` VARCHAR(200) NULL,
    `follower_count` INTEGER NOT NULL DEFAULT 0,
    `following_count` INTEGER NOT NULL DEFAULT 0,
    `game_count` INTEGER NOT NULL DEFAULT 0,
    `total_plays` BIGINT NOT NULL DEFAULT 0,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `users_username_key`(`username`),
    UNIQUE INDEX `users_email_key`(`email`),
    UNIQUE INDEX `users_phone_key`(`phone`),
    UNIQUE INDEX `users_wx_open_id_key`(`wx_open_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `refresh_tokens` (
    `id` VARCHAR(36) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `token` VARCHAR(500) NOT NULL,
    `expires_at` DATETIME(3) NOT NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `revoked_at` DATETIME(3) NULL,

    UNIQUE INDEX `refresh_tokens_token_key`(`token`),
    INDEX `refresh_tokens_user_id_idx`(`user_id`),
    INDEX `refresh_tokens_token_idx`(`token`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `games` (
    `id` VARCHAR(36) NOT NULL,
    `author_id` VARCHAR(36) NOT NULL,
    `title` VARCHAR(64) NOT NULL,
    `description` TEXT NULL,
    `user_idea` TEXT NULL,
    `slug` VARCHAR(128) NULL,
    `status` ENUM('draft', 'generating', 'review', 'published', 'banned', 'failed') NOT NULL DEFAULT 'draft',
    `failed_stage` VARCHAR(64) NULL,
    `failed_reason` TEXT NULL,
    `retry_count` INTEGER NOT NULL DEFAULT 0,
    `last_error_at` DATETIME(3) NULL,
    `game_type` VARCHAR(32) NULL,
    `tags` JSON NOT NULL,
    `forked_from` VARCHAR(36) NULL,
    `fork_depth` INTEGER NOT NULL DEFAULT 0,
    `code_bundle_id` VARCHAR(255) NULL,
    `version` INTEGER NOT NULL DEFAULT 1,
    `thumbnail_url` VARCHAR(191) NULL,
    `play_count` BIGINT NOT NULL DEFAULT 0,
    `like_count` BIGINT NOT NULL DEFAULT 0,
    `fork_count` BIGINT NOT NULL DEFAULT 0,
    `comment_count` INTEGER NOT NULL DEFAULT 0,
    `avg_play_time` DOUBLE NOT NULL DEFAULT 0,
    `quality_score` DOUBLE NOT NULL DEFAULT 0,
    `visibility` VARCHAR(16) NOT NULL DEFAULT 'public',
    `allow_comments` BOOLEAN NOT NULL DEFAULT true,
    `allow_fork` BOOLEAN NOT NULL DEFAULT true,
    `can_play` BOOLEAN NOT NULL DEFAULT true,
    `require_subscription` BOOLEAN NOT NULL DEFAULT false,
    `access_grant_source` ENUM('none', 'free_quota', 'subscription_quota', 'subscription_unlock') NOT NULL DEFAULT 'none',
    `access_grant_subscription_id` VARCHAR(36) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,
    `published_at` DATETIME(3) NULL,

    UNIQUE INDEX `games_slug_key`(`slug`),
    INDEX `games_author_id_idx`(`author_id`),
    INDEX `games_game_type_quality_score_idx`(`game_type`, `quality_score`),
    INDEX `games_forked_from_idx`(`forked_from`),
    INDEX `games_status_published_at_idx`(`status`, `published_at`),
    INDEX `games_status_play_count_idx`(`status`, `play_count`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `user_quotas` (
    `id` VARCHAR(36) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `total_free_quota` INTEGER NOT NULL DEFAULT 5,
    `used_free_quota` INTEGER NOT NULL DEFAULT 0,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `user_quotas_user_id_key`(`user_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `subscription_plans` (
    `id` VARCHAR(64) NOT NULL,
    `name` VARCHAR(128) NOT NULL,
    `description` VARCHAR(255) NULL,
    `price` INTEGER NOT NULL,
    `currency` VARCHAR(3) NOT NULL DEFAULT 'CNY',
    `period` ENUM('monthly', 'yearly') NOT NULL,
    `quota` INTEGER NOT NULL,
    `features` JSON NOT NULL,
    `recommended` BOOLEAN NOT NULL DEFAULT false,
    `badge` VARCHAR(32) NULL,
    `sort_order` INTEGER NOT NULL DEFAULT 0,
    `active` BOOLEAN NOT NULL DEFAULT true,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `subscription_plans_active_sort_order_idx`(`active`, `sort_order`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `user_subscriptions` (
    `id` VARCHAR(36) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `plan_id` VARCHAR(64) NOT NULL,
    `status` ENUM('active', 'expired', 'cancelled') NOT NULL DEFAULT 'active',
    `started_at` DATETIME(3) NOT NULL,
    `expires_at` DATETIME(3) NOT NULL,
    `used_this_period` INTEGER NOT NULL DEFAULT 0,
    `quota_this_period` INTEGER NOT NULL,
    `auto_renew` BOOLEAN NOT NULL DEFAULT false,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `user_subscriptions_user_id_status_expires_at_idx`(`user_id`, `status`, `expires_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `subscription_orders` (
    `id` VARCHAR(64) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `plan_id` VARCHAR(64) NOT NULL,
    `amount` INTEGER NOT NULL,
    `currency` VARCHAR(3) NOT NULL DEFAULT 'CNY',
    `status` ENUM('pending', 'paid', 'failed', 'refunded', 'canceled') NOT NULL DEFAULT 'pending',
    `provider` VARCHAR(32) NOT NULL DEFAULT 'wechat_pay',
    `out_trade_no` VARCHAR(64) NOT NULL,
    `payment_id` VARCHAR(128) NULL,
    `prepay_id` VARCHAR(128) NULL,
    `game_id_to_unlock` VARCHAR(36) NULL,
    `description` VARCHAR(255) NULL,
    `payment_payload` JSON NULL,
    `payment_response` JSON NULL,
    `paid_at` DATETIME(3) NULL,
    `expires_at` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `subscription_orders_out_trade_no_key`(`out_trade_no`),
    INDEX `subscription_orders_user_id_status_created_at_idx`(`user_id`, `status`, `created_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `game_bundles` (
    `id` VARCHAR(36) NOT NULL,
    `game_id` VARCHAR(36) NOT NULL,
    `version` INTEGER NOT NULL DEFAULT 1,
    `html_code` LONGTEXT NOT NULL,
    `css_code` LONGTEXT NULL,
    `js_code` LONGTEXT NULL,
    `spec` JSON NULL,
    `ai_conversation` JSON NULL,
    `generation_meta` JSON NULL,
    `metadata` JSON NULL,
    `preview_url` VARCHAR(191) NULL,
    `code_size_bytes` INTEGER NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `game_bundles_game_id_idx`(`game_id`),
    INDEX `game_bundles_created_at_idx`(`created_at`),
    UNIQUE INDEX `game_bundles_game_id_version_key`(`game_id`, `version`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `game_creation_sessions` (
    `id` VARCHAR(36) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `status` VARCHAR(32) NOT NULL DEFAULT 'collecting',
    `entry_mode` VARCHAR(32) NOT NULL DEFAULT 'create',
    `initial_prompt` LONGTEXT NOT NULL,
    `title_draft` VARCHAR(64) NULL,
    `region_hint` VARCHAR(32) NULL,
    `locale` VARCHAR(16) NULL,
    `ai_state` VARCHAR(32) NULL,
    `revision` INTEGER NOT NULL DEFAULT 1,
    `slot_state` JSON NOT NULL,
    `missing_required` JSON NOT NULL,
    `skipped_slots` JSON NOT NULL,
    `current_question` JSON NULL,
    `conversation` JSON NOT NULL,
    `generated_game_id` VARCHAR(36) NULL,
    `generation_task_id` VARCHAR(36) NULL,
    `source_game_id` VARCHAR(36) NULL,
    `question_budget` INTEGER NOT NULL DEFAULT 4,
    `questions_asked` INTEGER NOT NULL DEFAULT 0,
    `expires_at` DATETIME(3) NULL,
    `last_client_request_id` VARCHAR(64) NULL,
    `last_client_message_id` VARCHAR(64) NULL,
    `last_generate_key` VARCHAR(64) NULL,
    `metadata` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `game_creation_sessions_user_id_status_updated_at_idx`(`user_id`, `status`, `updated_at`),
    INDEX `game_creation_sessions_generated_game_id_idx`(`generated_game_id`),
    INDEX `game_creation_sessions_generation_task_id_idx`(`generation_task_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `game_templates` (
    `id` VARCHAR(36) NOT NULL,
    `template_id` VARCHAR(255) NOT NULL,
    `game_type` VARCHAR(32) NULL,
    `content` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `game_templates_template_id_key`(`template_id`),
    INDEX `game_templates_game_type_idx`(`game_type`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `cloud_provider_accounts` (
    `id` VARCHAR(36) NOT NULL,
    `vendor` VARCHAR(32) NOT NULL,
    `account_key` VARCHAR(64) NOT NULL,
    `display_name` VARCHAR(128) NOT NULL,
    `enabled` BOOLEAN NOT NULL DEFAULT true,
    `credential_mode` VARCHAR(32) NOT NULL DEFAULT 'env_secret',
    `default_registry` VARCHAR(255) NULL,
    `default_registry_namespace` VARCHAR(128) NULL,
    `default_vpc_id` VARCHAR(128) NULL,
    `default_subnet_id` VARCHAR(128) NULL,
    `default_security_group_id` VARCHAR(128) NULL,
    `synced_at` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `cloud_provider_accounts_account_key_key`(`account_key`),
    INDEX `cloud_provider_accounts_vendor_enabled_idx`(`vendor`, `enabled`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `cloud_region_catalog` (
    `id` VARCHAR(36) NOT NULL,
    `account_id` VARCHAR(36) NOT NULL,
    `vendor` VARCHAR(32) NOT NULL,
    `region_code` VARCHAR(64) NOT NULL,
    `region_name` VARCHAR(64) NOT NULL,
    `region_group` VARCHAR(32) NULL,
    `vcr_supported` BOOLEAN NOT NULL DEFAULT true,
    `vefaas_supported` BOOLEAN NOT NULL DEFAULT true,
    `apig_supported` BOOLEAN NOT NULL DEFAULT true,
    `deploy_supported` BOOLEAN NOT NULL DEFAULT true,
    `enabled` BOOLEAN NOT NULL DEFAULT true,
    `synced_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `cloud_region_catalog_vendor_enabled_idx`(`vendor`, `enabled`),
    UNIQUE INDEX `cloud_region_catalog_account_id_region_code_key`(`account_id`, `region_code`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `ai_engine_region_targets` (
    `id` VARCHAR(36) NOT NULL,
    `account_id` VARCHAR(36) NOT NULL,
    `region_catalog_id` VARCHAR(36) NOT NULL,
    `vendor` VARCHAR(32) NOT NULL,
    `cloud_region_code` VARCHAR(64) NOT NULL,
    `execution_region` VARCHAR(32) NOT NULL,
    `display_name` VARCHAR(128) NOT NULL,
    `function_name` VARCHAR(128) NOT NULL,
    `registry` VARCHAR(255) NOT NULL,
    `registry_namespace` VARCHAR(128) NOT NULL,
    `image_repository` VARCHAR(128) NOT NULL,
    `service_region_env` VARCHAR(32) NOT NULL,
    `ai_engine_url` VARCHAR(255) NULL,
    `deploy_enabled` BOOLEAN NOT NULL DEFAULT true,
    `deploy_status` VARCHAR(32) NOT NULL DEFAULT 'pending',
    `last_revision` VARCHAR(64) NULL,
    `last_image_tag` VARCHAR(128) NULL,
    `last_release_status` VARCHAR(64) NULL,
    `last_deploy_error` TEXT NULL,
    `last_deployed_at` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `ai_engine_region_targets_deploy_enabled_deploy_status_idx`(`deploy_enabled`, `deploy_status`),
    INDEX `ai_engine_region_targets_vendor_cloud_region_code_idx`(`vendor`, `cloud_region_code`),
    UNIQUE INDEX `ai_engine_region_targets_execution_region_key`(`execution_region`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `llm_gateway_providers` (
    `id` VARCHAR(36) NOT NULL,
    `name` VARCHAR(64) NOT NULL,
    `provider_type` ENUM('openai_compatible', 'anthropic') NOT NULL,
    `region_target_id` VARCHAR(36) NULL,
    `cloud_vendor` VARCHAR(32) NULL,
    `cloud_region_code` VARCHAR(64) NULL,
    `region` VARCHAR(32) NOT NULL DEFAULT 'cn_shanghai',
    `base_url` VARCHAR(255) NOT NULL,
    `api_key` TEXT NOT NULL,
    `model` VARCHAR(128) NOT NULL,
    `fast_model` VARCHAR(128) NULL,
    `request_timeout_s` INTEGER NOT NULL DEFAULT 600,
    `connect_timeout_s` INTEGER NOT NULL DEFAULT 15,
    `enabled` BOOLEAN NOT NULL DEFAULT true,
    `priority` INTEGER NOT NULL DEFAULT 100,
    `description` VARCHAR(255) NULL,
    `extra_config` JSON NULL,
    `capability_flags` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `llm_gateway_providers_enabled_region_priority_idx`(`enabled`, `region`, `priority`),
    INDEX `llm_gateway_providers_provider_type_region_idx`(`provider_type`, `region`),
    INDEX `llm_gateway_providers_region_target_id_idx`(`region_target_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `llm_step_routes` (
    `id` VARCHAR(36) NOT NULL,
    `step_key` VARCHAR(64) NOT NULL,
    `region` VARCHAR(32) NOT NULL DEFAULT 'cn_shanghai',
    `provider_id` VARCHAR(36) NOT NULL,
    `fallback_provider_ids` JSON NOT NULL,
    `model_override` VARCHAR(128) NULL,
    `fast_model_override` VARCHAR(128) NULL,
    `request_timeout_s` INTEGER NULL,
    `connect_timeout_s` INTEGER NULL,
    `enabled` BOOLEAN NOT NULL DEFAULT true,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `llm_step_routes_enabled_region_idx`(`enabled`, `region`),
    INDEX `llm_step_routes_provider_id_idx`(`provider_id`),
    UNIQUE INDEX `llm_step_routes_step_key_region_key`(`step_key`, `region`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `llm_step_catalog` (
    `id` VARCHAR(36) NOT NULL,
    `step_key` VARCHAR(64) NOT NULL,
    `step_order` INTEGER NOT NULL,
    `stage_label` VARCHAR(64) NULL,
    `display_name` VARCHAR(128) NOT NULL,
    `description` VARCHAR(255) NULL,
    `output_class` VARCHAR(32) NULL DEFAULT 'medium_structured',
    `min_output_tokens` INTEGER NULL,
    `max_output_tokens` INTEGER NULL,
    `enabled` BOOLEAN NOT NULL DEFAULT true,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `llm_step_catalog_step_key_key`(`step_key`),
    INDEX `llm_step_catalog_enabled_step_order_idx`(`enabled`, `step_order`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `llm_gateway_test_records` (
    `id` VARCHAR(36) NOT NULL,
    `provider_id` VARCHAR(36) NOT NULL,
    `success` BOOLEAN NOT NULL,
    `region` VARCHAR(32) NOT NULL DEFAULT 'cn_shanghai',
    `resolved_endpoint` VARCHAR(255) NULL,
    `model` VARCHAR(128) NULL,
    `latency_ms` INTEGER NULL,
    `http_status` INTEGER NULL,
    `error_message` TEXT NULL,
    `tested_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `llm_gateway_test_records_provider_id_tested_at_idx`(`provider_id`, `tested_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `generation_tasks` (
    `id` VARCHAR(36) NOT NULL,
    `game_id` VARCHAR(36) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `task_type` ENUM('pipeline_run', 'pipeline_iterate') NOT NULL,
    `region` VARCHAR(32) NOT NULL DEFAULT 'cn_shanghai',
    `status` ENUM('queued', 'running', 'succeeded', 'failed', 'canceled', 'timed_out') NOT NULL DEFAULT 'queued',
    `timeout_s` INTEGER NOT NULL,
    `version` INTEGER NULL,
    `pipeline_version` VARCHAR(16) NULL,
    `prompt_bundle_id` VARCHAR(64) NULL,
    `prompt_bundle_version` INTEGER NULL,
    `runtime_profile` VARCHAR(64) NULL,
    `contract_version` VARCHAR(32) NULL,
    `failure_family` VARCHAR(64) NULL,
    `primary_artifact_id` VARCHAR(36) NULL,
    `cancel_requested` BOOLEAN NOT NULL DEFAULT false,
    `upstream_task_id` VARCHAR(64) NULL,
    `progress_stage` VARCHAR(64) NULL,
    `progress_pct` INTEGER NULL,
    `progress_message` VARCHAR(255) NULL,
    `failed_stage` VARCHAR(64) NULL,
    `error_message` TEXT NULL,
    `retry_count` INTEGER NOT NULL DEFAULT 0,
    `fallback` VARCHAR(64) NULL,
    `ws_channel` VARCHAR(128) NOT NULL,
    `preview_url` VARCHAR(255) NULL,
    `gateway_config_version` INTEGER NULL,
    `route_snapshot` JSON NULL,
    `result_summary` JSON NULL,
    `metadata` JSON NULL,
    `started_at` DATETIME(3) NULL,
    `completed_at` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `generation_tasks_game_id_created_at_idx`(`game_id`, `created_at`),
    INDEX `generation_tasks_region_status_updated_at_idx`(`region`, `status`, `updated_at`),
    INDEX `generation_tasks_user_id_status_created_at_idx`(`user_id`, `status`, `created_at`),
    INDEX `generation_tasks_status_updated_at_idx`(`status`, `updated_at`),
    INDEX `generation_tasks_pipeline_version_status_updated_at_idx`(`pipeline_version`, `status`, `updated_at`),
    INDEX `generation_tasks_runtime_profile_status_created_at_idx`(`runtime_profile`, `status`, `created_at`),
    INDEX `generation_tasks_failure_family_created_at_idx`(`failure_family`, `created_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `generation_task_events` (
    `id` VARCHAR(36) NOT NULL,
    `task_id` VARCHAR(36) NOT NULL,
    `game_id` VARCHAR(36) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `event_type` ENUM('status', 'progress', 'llm_call', 'note', 'error') NOT NULL,
    `stage` VARCHAR(64) NULL,
    `percentage` INTEGER NULL,
    `message` VARCHAR(255) NOT NULL,
    `details` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `generation_task_events_task_id_created_at_idx`(`task_id`, `created_at`),
    INDEX `generation_task_events_game_id_created_at_idx`(`game_id`, `created_at`),
    INDEX `generation_task_events_user_id_created_at_idx`(`user_id`, `created_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `llm_call_logs` (
    `id` VARCHAR(36) NOT NULL,
    `task_id` VARCHAR(36) NULL,
    `game_id` VARCHAR(36) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `stage` VARCHAR(64) NOT NULL,
    `step_key` VARCHAR(64) NOT NULL,
    `provider_id` VARCHAR(36) NULL,
    `provider_name` VARCHAR(64) NULL,
    `provider_type` VARCHAR(32) NULL,
    `region` VARCHAR(32) NULL,
    `model` VARCHAR(128) NULL,
    `request_timeout_s` INTEGER NULL,
    `connect_timeout_s` INTEGER NULL,
    `latency_ms` INTEGER NULL,
    `http_status` INTEGER NULL,
    `input_tokens` INTEGER NULL,
    `output_tokens` INTEGER NULL,
    `total_tokens` INTEGER NULL,
    `output_class` VARCHAR(32) NULL,
    `is_primary_provider` BOOLEAN NULL DEFAULT true,
    `failover_reason` VARCHAR(255) NULL,
    `provider_verified` BOOLEAN NULL,
    `success` BOOLEAN NOT NULL DEFAULT false,
    `upstream_request_id` VARCHAR(128) NULL,
    `error_code` VARCHAR(128) NULL,
    `error_message` TEXT NULL,
    `error_body_excerpt` TEXT NULL,
    `config_version` INTEGER NULL,
    `route_snapshot` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `llm_call_logs_task_id_created_at_idx`(`task_id`, `created_at`),
    INDEX `llm_call_logs_game_id_created_at_idx`(`game_id`, `created_at`),
    INDEX `llm_call_logs_step_key_created_at_idx`(`step_key`, `created_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `social_interactions` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `user_id` VARCHAR(36) NOT NULL,
    `target_type` ENUM('game', 'user', 'comment') NOT NULL,
    `target_id` VARCHAR(36) NOT NULL,
    `action` ENUM('like', 'follow', 'play', 'fork', 'share', 'report') NOT NULL,
    `metadata` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `social_interactions_target_type_target_id_idx`(`target_type`, `target_id`),
    INDEX `social_interactions_user_id_action_idx`(`user_id`, `action`),
    UNIQUE INDEX `social_interactions_user_id_target_type_target_id_action_key`(`user_id`, `target_type`, `target_id`, `action`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `user_follows` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `follower_id` VARCHAR(36) NOT NULL,
    `following_id` VARCHAR(36) NOT NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `user_follows_following_id_created_at_idx`(`following_id`, `created_at`),
    INDEX `user_follows_follower_id_created_at_idx`(`follower_id`, `created_at`),
    UNIQUE INDEX `user_follows_follower_id_following_id_key`(`follower_id`, `following_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `comments` (
    `id` VARCHAR(36) NOT NULL,
    `game_id` VARCHAR(36) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `parent_id` VARCHAR(36) NULL,
    `content` TEXT NOT NULL,
    `like_count` INTEGER NOT NULL DEFAULT 0,
    `status` ENUM('visible', 'hidden', 'deleted') NOT NULL DEFAULT 'visible',
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `comments_game_id_created_at_idx`(`game_id`, `created_at`),
    INDEX `comments_user_id_idx`(`user_id`),
    INDEX `comments_parent_id_idx`(`parent_id`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `creator_earnings` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `creator_id` VARCHAR(36) NOT NULL,
    `game_id` VARCHAR(36) NULL,
    `earning_type` ENUM('ad_revenue', 'tip', 'prize', 'referral') NOT NULL,
    `amount` DECIMAL(12, 4) NOT NULL,
    `currency` VARCHAR(3) NOT NULL DEFAULT 'CNY',
    `status` ENUM('pending', 'settled', 'withdrawn') NOT NULL DEFAULT 'pending',
    `period_start` DATE NULL,
    `period_end` DATE NULL,
    `settled_at` DATETIME(3) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `creator_earnings_creator_id_status_idx`(`creator_id`, `status`),
    INDEX `creator_earnings_creator_id_created_at_idx`(`creator_id`, `created_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `notifications` (
    `id` VARCHAR(36) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `actor_id` VARCHAR(36) NULL,
    `type` ENUM('like', 'follow', 'comment', 'fork', 'system', 'challenge', 'earning') NOT NULL,
    `target_id` VARCHAR(36) NULL,
    `content` TEXT NULL,
    `is_read` BOOLEAN NOT NULL DEFAULT false,
    `metadata` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `notifications_user_id_is_read_created_at_idx`(`user_id`, `is_read`, `created_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `system_configs` (
    `id` VARCHAR(36) NOT NULL,
    `config_key` VARCHAR(128) NOT NULL,
    `config_value` LONGTEXT NOT NULL,
    `description` VARCHAR(255) NULL,
    `category` VARCHAR(64) NOT NULL DEFAULT 'general',
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    UNIQUE INDEX `system_configs_config_key_key`(`config_key`),
    INDEX `system_configs_category_idx`(`category`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `app_releases` (
    `id` VARCHAR(36) NOT NULL,
    `platform` ENUM('ios', 'android') NOT NULL,
    `channel` VARCHAR(32) NOT NULL DEFAULT 'production',
    `version_name` VARCHAR(64) NOT NULL,
    `build_number` VARCHAR(64) NULL,
    `release_notes` LONGTEXT NULL,
    `download_url` VARCHAR(512) NULL,
    `qr_code_url` VARCHAR(512) NULL,
    `file_name` VARCHAR(255) NULL,
    `file_size` BIGINT NULL,
    `mime_type` VARCHAR(128) NULL,
    `storage_key` VARCHAR(512) NULL,
    `checksum_sha256` VARCHAR(64) NULL,
    `source_type` ENUM('upload', 'external_url', 'app_store') NOT NULL DEFAULT 'upload',
    `status` ENUM('draft', 'published', 'archived') NOT NULL DEFAULT 'draft',
    `is_active` BOOLEAN NOT NULL DEFAULT false,
    `published_at` DATETIME(3) NULL,
    `created_by` VARCHAR(64) NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `app_releases_platform_channel_status_idx`(`platform`, `channel`, `status`),
    INDEX `app_releases_platform_channel_is_active_idx`(`platform`, `channel`, `is_active`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `app_promo_events` (
    `id` VARCHAR(36) NOT NULL,
    `scene` VARCHAR(64) NOT NULL,
    `event_type` VARCHAR(64) NOT NULL,
    `user_id` VARCHAR(36) NULL,
    `game_id` VARCHAR(36) NULL,
    `device_id` VARCHAR(128) NOT NULL,
    `platform` VARCHAR(32) NULL,
    `channel` VARCHAR(64) NULL,
    `user_agent` VARCHAR(512) NULL,
    `ip_hash` VARCHAR(128) NULL,
    `extra_json` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `app_promo_events_scene_event_type_created_at_idx`(`scene`, `event_type`, `created_at`),
    INDEX `app_promo_events_platform_created_at_idx`(`platform`, `created_at`),
    INDEX `app_promo_events_device_id_created_at_idx`(`device_id`, `created_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `generation_artifacts` (
    `id` VARCHAR(36) NOT NULL,
    `task_id` VARCHAR(36) NULL,
    `game_id` VARCHAR(36) NOT NULL,
    `user_id` VARCHAR(36) NOT NULL,
    `artifact_type` VARCHAR(64) NOT NULL,
    `content_type` VARCHAR(64) NOT NULL,
    `storage_type` VARCHAR(32) NOT NULL,
    `payload_json` JSON NULL,
    `payload_text` LONGTEXT NULL,
    `payload_url` VARCHAR(512) NULL,
    `sha256` VARCHAR(64) NULL,
    `size_bytes` INTEGER NULL,
    `compression` VARCHAR(32) NULL,
    `expires_at` DATETIME(3) NULL,
    `metadata` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `generation_artifacts_task_id_artifact_type_created_at_idx`(`task_id`, `artifact_type`, `created_at`),
    INDEX `generation_artifacts_game_id_artifact_type_created_at_idx`(`game_id`, `artifact_type`, `created_at`),
    INDEX `generation_artifacts_user_id_artifact_type_created_at_idx`(`user_id`, `artifact_type`, `created_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `prompt_bundles` (
    `id` VARCHAR(64) NOT NULL,
    `version` INTEGER NOT NULL,
    `status` VARCHAR(16) NOT NULL DEFAULT 'draft',
    `product_policy` LONGTEXT NOT NULL,
    `locked_contract_override` LONGTEXT NULL,
    `repair_playbook` LONGTEXT NOT NULL,
    `profile_overrides` JSON NULL,
    `metadata` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `prompt_bundles_status_updated_at_idx`(`status`, `updated_at`),
    PRIMARY KEY (`id`, `version`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `runtime_profile_catalog` (
    `id` VARCHAR(64) NOT NULL,
    `display_name` VARCHAR(128) NOT NULL,
    `enabled` BOOLEAN NOT NULL DEFAULT true,
    `skeleton_version` VARCHAR(32) NOT NULL,
    `contract_schema` JSON NOT NULL,
    `few_shot_prompt` LONGTEXT NULL,
    `metadata` JSON NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at` DATETIME(3) NOT NULL,

    INDEX `runtime_profile_catalog_enabled_updated_at_idx`(`enabled`, `updated_at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- AddForeignKey
ALTER TABLE `refresh_tokens` ADD CONSTRAINT `refresh_tokens_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `games` ADD CONSTRAINT `games_author_id_fkey` FOREIGN KEY (`author_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `games` ADD CONSTRAINT `games_forked_from_fkey` FOREIGN KEY (`forked_from`) REFERENCES `games`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `user_quotas` ADD CONSTRAINT `user_quotas_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `user_subscriptions` ADD CONSTRAINT `user_subscriptions_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `user_subscriptions` ADD CONSTRAINT `user_subscriptions_plan_id_fkey` FOREIGN KEY (`plan_id`) REFERENCES `subscription_plans`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `subscription_orders` ADD CONSTRAINT `subscription_orders_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `subscription_orders` ADD CONSTRAINT `subscription_orders_plan_id_fkey` FOREIGN KEY (`plan_id`) REFERENCES `subscription_plans`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `game_bundles` ADD CONSTRAINT `game_bundles_game_id_fkey` FOREIGN KEY (`game_id`) REFERENCES `games`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `game_creation_sessions` ADD CONSTRAINT `game_creation_sessions_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `cloud_region_catalog` ADD CONSTRAINT `cloud_region_catalog_account_id_fkey` FOREIGN KEY (`account_id`) REFERENCES `cloud_provider_accounts`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `ai_engine_region_targets` ADD CONSTRAINT `ai_engine_region_targets_account_id_fkey` FOREIGN KEY (`account_id`) REFERENCES `cloud_provider_accounts`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `ai_engine_region_targets` ADD CONSTRAINT `ai_engine_region_targets_region_catalog_id_fkey` FOREIGN KEY (`region_catalog_id`) REFERENCES `cloud_region_catalog`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `llm_gateway_providers` ADD CONSTRAINT `llm_gateway_providers_region_target_id_fkey` FOREIGN KEY (`region_target_id`) REFERENCES `ai_engine_region_targets`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `llm_step_routes` ADD CONSTRAINT `llm_step_routes_provider_id_fkey` FOREIGN KEY (`provider_id`) REFERENCES `llm_gateway_providers`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `llm_gateway_test_records` ADD CONSTRAINT `llm_gateway_test_records_provider_id_fkey` FOREIGN KEY (`provider_id`) REFERENCES `llm_gateway_providers`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `generation_tasks` ADD CONSTRAINT `generation_tasks_game_id_fkey` FOREIGN KEY (`game_id`) REFERENCES `games`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `generation_tasks` ADD CONSTRAINT `generation_tasks_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `generation_task_events` ADD CONSTRAINT `generation_task_events_task_id_fkey` FOREIGN KEY (`task_id`) REFERENCES `generation_tasks`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `generation_task_events` ADD CONSTRAINT `generation_task_events_game_id_fkey` FOREIGN KEY (`game_id`) REFERENCES `games`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `generation_task_events` ADD CONSTRAINT `generation_task_events_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `llm_call_logs` ADD CONSTRAINT `llm_call_logs_task_id_fkey` FOREIGN KEY (`task_id`) REFERENCES `generation_tasks`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `llm_call_logs` ADD CONSTRAINT `llm_call_logs_game_id_fkey` FOREIGN KEY (`game_id`) REFERENCES `games`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `llm_call_logs` ADD CONSTRAINT `llm_call_logs_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `llm_call_logs` ADD CONSTRAINT `llm_call_logs_provider_id_fkey` FOREIGN KEY (`provider_id`) REFERENCES `llm_gateway_providers`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `social_interactions` ADD CONSTRAINT `social_interactions_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `user_follows` ADD CONSTRAINT `user_follows_follower_id_fkey` FOREIGN KEY (`follower_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `user_follows` ADD CONSTRAINT `user_follows_following_id_fkey` FOREIGN KEY (`following_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `comments` ADD CONSTRAINT `comments_game_id_fkey` FOREIGN KEY (`game_id`) REFERENCES `games`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `comments` ADD CONSTRAINT `comments_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `comments` ADD CONSTRAINT `comments_parent_id_fkey` FOREIGN KEY (`parent_id`) REFERENCES `comments`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `creator_earnings` ADD CONSTRAINT `creator_earnings_creator_id_fkey` FOREIGN KEY (`creator_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `creator_earnings` ADD CONSTRAINT `creator_earnings_game_id_fkey` FOREIGN KEY (`game_id`) REFERENCES `games`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `notifications` ADD CONSTRAINT `notifications_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `generation_artifacts` ADD CONSTRAINT `generation_artifacts_task_id_fkey` FOREIGN KEY (`task_id`) REFERENCES `generation_tasks`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `generation_artifacts` ADD CONSTRAINT `generation_artifacts_game_id_fkey` FOREIGN KEY (`game_id`) REFERENCES `games`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `generation_artifacts` ADD CONSTRAINT `generation_artifacts_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

