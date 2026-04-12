import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { randomUUID } from 'crypto';
import { PrismaService } from '../prisma/prisma.service';
import promptCatalog from './catalogs/prompt-catalog.json';
import promptBundleCatalog from './catalogs/prompt-bundle-catalog.json';
import runtimeProfileCatalog from './catalogs/runtime-profile-catalog.json';
import {
  LEGACY_TO_CANONICAL_RUNTIME_PROFILE_IDS,
} from './runtime-profile-ids';

type ColumnPatch = {
  table: string;
  name: string;
  sql: string;
};

const TABLE_COLUMN_PATCHES: ColumnPatch[] = [
  {
    table: 'games',
    name: 'can_play',
    sql: 'ALTER TABLE games ADD COLUMN can_play BOOLEAN NOT NULL DEFAULT TRUE',
  },
  {
    table: 'games',
    name: 'require_subscription',
    sql: 'ALTER TABLE games ADD COLUMN require_subscription BOOLEAN NOT NULL DEFAULT FALSE',
  },
  {
    table: 'games',
    name: 'access_grant_source',
    sql: "ALTER TABLE games ADD COLUMN access_grant_source ENUM('none', 'free_quota', 'subscription_quota', 'subscription_unlock') NOT NULL DEFAULT 'none'",
  },
  {
    table: 'games',
    name: 'access_grant_subscription_id',
    sql: 'ALTER TABLE games ADD COLUMN access_grant_subscription_id VARCHAR(36) NULL',
  },
  {
    table: 'llm_gateway_providers',
    name: 'region_target_id',
    sql: 'ALTER TABLE llm_gateway_providers ADD COLUMN region_target_id VARCHAR(36) NULL',
  },
  {
    table: 'llm_gateway_providers',
    name: 'cloud_vendor',
    sql: "ALTER TABLE llm_gateway_providers ADD COLUMN cloud_vendor VARCHAR(32) NULL",
  },
  {
    table: 'llm_gateway_providers',
    name: 'cloud_region_code',
    sql: "ALTER TABLE llm_gateway_providers ADD COLUMN cloud_region_code VARCHAR(64) NULL",
  },
  {
    table: 'generation_tasks',
    name: 'region',
    sql: "ALTER TABLE generation_tasks ADD COLUMN region VARCHAR(32) NOT NULL DEFAULT 'cn_shanghai' AFTER task_type",
  },
  {
    table: 'generation_tasks',
    name: 'pipeline_version',
    sql: "ALTER TABLE generation_tasks ADD COLUMN pipeline_version VARCHAR(16) NULL AFTER version",
  },
  {
    table: 'generation_tasks',
    name: 'prompt_bundle_id',
    sql: 'ALTER TABLE generation_tasks ADD COLUMN prompt_bundle_id VARCHAR(64) NULL AFTER pipeline_version',
  },
  {
    table: 'generation_tasks',
    name: 'prompt_bundle_version',
    sql: 'ALTER TABLE generation_tasks ADD COLUMN prompt_bundle_version INT NULL AFTER prompt_bundle_id',
  },
  {
    table: 'generation_tasks',
    name: 'runtime_profile',
    sql: 'ALTER TABLE generation_tasks ADD COLUMN runtime_profile VARCHAR(64) NULL AFTER prompt_bundle_version',
  },
  {
    table: 'generation_tasks',
    name: 'contract_version',
    sql: 'ALTER TABLE generation_tasks ADD COLUMN contract_version VARCHAR(32) NULL AFTER runtime_profile',
  },
  {
    table: 'generation_tasks',
    name: 'failure_family',
    sql: 'ALTER TABLE generation_tasks ADD COLUMN failure_family VARCHAR(64) NULL AFTER contract_version',
  },
  {
    table: 'generation_tasks',
    name: 'primary_artifact_id',
    sql: 'ALTER TABLE generation_tasks ADD COLUMN primary_artifact_id VARCHAR(36) NULL AFTER failure_family',
  },
  {
    table: 'llm_call_logs',
    name: 'input_tokens',
    sql: 'ALTER TABLE llm_call_logs ADD COLUMN input_tokens INT NULL AFTER http_status',
  },
  {
    table: 'llm_call_logs',
    name: 'output_tokens',
    sql: 'ALTER TABLE llm_call_logs ADD COLUMN output_tokens INT NULL AFTER input_tokens',
  },
  {
    table: 'llm_call_logs',
    name: 'total_tokens',
    sql: 'ALTER TABLE llm_call_logs ADD COLUMN total_tokens INT NULL AFTER output_tokens',
  },
  {
    table: 'llm_call_logs',
    name: 'output_class',
    sql: 'ALTER TABLE llm_call_logs ADD COLUMN output_class VARCHAR(32) NULL',
  },
  {
    table: 'llm_call_logs',
    name: 'is_primary_provider',
    sql: 'ALTER TABLE llm_call_logs ADD COLUMN is_primary_provider BOOLEAN DEFAULT TRUE',
  },
  {
    table: 'llm_call_logs',
    name: 'failover_reason',
    sql: 'ALTER TABLE llm_call_logs ADD COLUMN failover_reason VARCHAR(255) NULL',
  },
  {
    table: 'llm_call_logs',
    name: 'provider_verified',
    sql: 'ALTER TABLE llm_call_logs ADD COLUMN provider_verified BOOLEAN DEFAULT NULL',
  },
  {
    table: 'llm_step_catalog',
    name: 'output_class',
    sql: "ALTER TABLE llm_step_catalog ADD COLUMN output_class VARCHAR(32) DEFAULT 'medium_structured'",
  },
  {
    table: 'llm_step_catalog',
    name: 'min_output_tokens',
    sql: 'ALTER TABLE llm_step_catalog ADD COLUMN min_output_tokens INT DEFAULT NULL',
  },
  {
    table: 'llm_step_catalog',
    name: 'max_output_tokens',
    sql: 'ALTER TABLE llm_step_catalog ADD COLUMN max_output_tokens INT DEFAULT NULL',
  },
  {
    table: 'llm_gateway_providers',
    name: 'capability_flags',
    sql: "ALTER TABLE llm_gateway_providers ADD COLUMN capability_flags JSON DEFAULT ('{}')",
  },
];

const GAME_SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS cloud_provider_accounts (
    id VARCHAR(36) NOT NULL,
    vendor VARCHAR(32) NOT NULL,
    account_key VARCHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    credential_mode VARCHAR(32) NOT NULL DEFAULT 'env_secret',
    default_registry VARCHAR(255) NULL,
    default_registry_namespace VARCHAR(128) NULL,
    default_vpc_id VARCHAR(128) NULL,
    default_subnet_id VARCHAR(128) NULL,
    default_security_group_id VARCHAR(128) NULL,
    synced_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY cloud_provider_accounts_account_key_key (account_key),
    INDEX cloud_provider_accounts_vendor_enabled_idx (vendor, enabled)
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS cloud_region_catalog (
    id VARCHAR(36) NOT NULL,
    account_id VARCHAR(36) NOT NULL,
    vendor VARCHAR(32) NOT NULL,
    region_code VARCHAR(64) NOT NULL,
    region_name VARCHAR(64) NOT NULL,
    region_group VARCHAR(32) NULL,
    vcr_supported BOOLEAN NOT NULL DEFAULT TRUE,
    vefaas_supported BOOLEAN NOT NULL DEFAULT TRUE,
    apig_supported BOOLEAN NOT NULL DEFAULT TRUE,
    deploy_supported BOOLEAN NOT NULL DEFAULT TRUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY cloud_region_catalog_account_id_region_code_key (account_id, region_code),
    INDEX cloud_region_catalog_vendor_enabled_idx (vendor, enabled),
    CONSTRAINT cloud_region_catalog_account_id_fkey FOREIGN KEY (account_id) REFERENCES cloud_provider_accounts(id) ON DELETE CASCADE ON UPDATE CASCADE
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS ai_engine_region_targets (
    id VARCHAR(36) NOT NULL,
    account_id VARCHAR(36) NOT NULL,
    region_catalog_id VARCHAR(36) NOT NULL,
    vendor VARCHAR(32) NOT NULL,
    cloud_region_code VARCHAR(64) NOT NULL,
    execution_region VARCHAR(32) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    function_name VARCHAR(128) NOT NULL,
    registry VARCHAR(255) NOT NULL,
    registry_namespace VARCHAR(128) NOT NULL,
    image_repository VARCHAR(128) NOT NULL,
    service_region_env VARCHAR(32) NOT NULL,
    ai_engine_url VARCHAR(255) NULL,
    deploy_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    deploy_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    last_revision VARCHAR(64) NULL,
    last_image_tag VARCHAR(128) NULL,
    last_release_status VARCHAR(64) NULL,
    last_deploy_error LONGTEXT NULL,
    last_deployed_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY ai_engine_region_targets_execution_region_key (execution_region),
    INDEX ai_engine_region_targets_deploy_enabled_deploy_status_idx (deploy_enabled, deploy_status),
    INDEX ai_engine_region_targets_vendor_cloud_region_code_idx (vendor, cloud_region_code),
    CONSTRAINT ai_engine_region_targets_account_id_fkey FOREIGN KEY (account_id) REFERENCES cloud_provider_accounts(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT ai_engine_region_targets_region_catalog_id_fkey FOREIGN KEY (region_catalog_id) REFERENCES cloud_region_catalog(id) ON DELETE CASCADE ON UPDATE CASCADE
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS llm_gateway_providers (
    id VARCHAR(36) NOT NULL,
    name VARCHAR(64) NOT NULL,
    provider_type ENUM('openai_compatible', 'anthropic') NOT NULL,
    region_target_id VARCHAR(36) NULL,
    cloud_vendor VARCHAR(32) NULL,
    cloud_region_code VARCHAR(64) NULL,
    region VARCHAR(32) NOT NULL DEFAULT 'cn_shanghai',
    base_url VARCHAR(255) NOT NULL,
    api_key LONGTEXT NOT NULL,
    model VARCHAR(128) NOT NULL,
    fast_model VARCHAR(128) NULL,
    request_timeout_s INT NOT NULL DEFAULT 600,
    connect_timeout_s INT NOT NULL DEFAULT 15,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    priority INT NOT NULL DEFAULT 100,
    description VARCHAR(255) NULL,
    extra_config JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    INDEX llm_gateway_providers_enabled_region_priority_idx (enabled, region, priority),
    INDEX llm_gateway_providers_provider_type_region_idx (provider_type, region),
    INDEX llm_gateway_providers_region_target_id_idx (region_target_id)
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS llm_step_routes (
    id VARCHAR(36) NOT NULL,
    step_key VARCHAR(64) NOT NULL,
    region VARCHAR(32) NOT NULL DEFAULT 'cn_shanghai',
    provider_id VARCHAR(36) NOT NULL,
    fallback_provider_ids JSON NOT NULL,
    model_override VARCHAR(128) NULL,
    fast_model_override VARCHAR(128) NULL,
    request_timeout_s INT NULL,
    connect_timeout_s INT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY llm_step_routes_step_key_region_key (step_key, region),
    INDEX llm_step_routes_enabled_region_idx (enabled, region),
    INDEX llm_step_routes_provider_id_idx (provider_id),
    CONSTRAINT llm_step_routes_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES llm_gateway_providers(id) ON DELETE CASCADE ON UPDATE CASCADE
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS llm_step_catalog (
    id VARCHAR(36) NOT NULL,
    step_key VARCHAR(64) NOT NULL,
    step_order INT NOT NULL,
    stage_label VARCHAR(64) NULL,
    display_name VARCHAR(128) NOT NULL,
    description VARCHAR(255) NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY llm_step_catalog_step_key_key (step_key),
    INDEX llm_step_catalog_enabled_step_order_idx (enabled, step_order)
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS llm_gateway_test_records (
    id VARCHAR(36) NOT NULL,
    provider_id VARCHAR(36) NOT NULL,
    success BOOLEAN NOT NULL,
    region VARCHAR(32) NOT NULL DEFAULT 'cn_shanghai',
    resolved_endpoint VARCHAR(255) NULL,
    model VARCHAR(128) NULL,
    latency_ms INT NULL,
    http_status INT NULL,
    error_message LONGTEXT NULL,
    tested_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    INDEX llm_gateway_test_records_provider_id_tested_at_idx (provider_id, tested_at),
    CONSTRAINT llm_gateway_test_records_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES llm_gateway_providers(id) ON DELETE CASCADE ON UPDATE CASCADE
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS generation_tasks (
    id VARCHAR(36) NOT NULL,
    game_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    task_type ENUM('pipeline_run', 'pipeline_iterate') NOT NULL,
    region VARCHAR(32) NOT NULL DEFAULT 'cn_shanghai',
    status ENUM('queued', 'running', 'succeeded', 'failed', 'canceled', 'timed_out') NOT NULL DEFAULT 'queued',
    timeout_s INT NOT NULL,
    version INT NULL,
    pipeline_version VARCHAR(16) NULL,
    prompt_bundle_id VARCHAR(64) NULL,
    prompt_bundle_version INT NULL,
    runtime_profile VARCHAR(64) NULL,
    contract_version VARCHAR(32) NULL,
    failure_family VARCHAR(64) NULL,
    primary_artifact_id VARCHAR(36) NULL,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    upstream_task_id VARCHAR(64) NULL,
    progress_stage VARCHAR(64) NULL,
    progress_pct INT NULL,
    progress_message VARCHAR(255) NULL,
    failed_stage VARCHAR(64) NULL,
    error_message LONGTEXT NULL,
    retry_count INT NOT NULL DEFAULT 0,
    fallback VARCHAR(64) NULL,
    ws_channel VARCHAR(128) NOT NULL,
    preview_url VARCHAR(255) NULL,
    gateway_config_version INT NULL,
    route_snapshot JSON NULL,
    result_summary JSON NULL,
    metadata JSON NULL,
    started_at DATETIME(3) NULL,
    completed_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    INDEX generation_tasks_game_id_created_at_idx (game_id, created_at),
    INDEX generation_tasks_region_status_updated_at_idx (region, status, updated_at),
    INDEX generation_tasks_user_id_status_created_at_idx (user_id, status, created_at),
    INDEX generation_tasks_status_updated_at_idx (status, updated_at),
    INDEX generation_tasks_pipeline_version_status_updated_at_idx (pipeline_version, status, updated_at),
    INDEX generation_tasks_runtime_profile_status_created_at_idx (runtime_profile, status, created_at),
    INDEX generation_tasks_failure_family_created_at_idx (failure_family, created_at),
    CONSTRAINT generation_tasks_game_id_fkey FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT generation_tasks_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS generation_task_events (
    id VARCHAR(36) NOT NULL,
    task_id VARCHAR(36) NOT NULL,
    game_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    event_type ENUM('status', 'progress', 'llm_call', 'note', 'error') NOT NULL,
    stage VARCHAR(64) NULL,
    percentage INT NULL,
    message VARCHAR(255) NOT NULL,
    details JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    INDEX generation_task_events_task_id_created_at_idx (task_id, created_at),
    INDEX generation_task_events_game_id_created_at_idx (game_id, created_at),
    INDEX generation_task_events_user_id_created_at_idx (user_id, created_at),
    CONSTRAINT generation_task_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES generation_tasks(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT generation_task_events_game_id_fkey FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT generation_task_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS llm_call_logs (
    id VARCHAR(36) NOT NULL,
    task_id VARCHAR(36) NULL,
    game_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    stage VARCHAR(64) NOT NULL,
    step_key VARCHAR(64) NOT NULL,
    provider_id VARCHAR(36) NULL,
    provider_name VARCHAR(64) NULL,
    provider_type VARCHAR(32) NULL,
    region VARCHAR(32) NULL,
    model VARCHAR(128) NULL,
    request_timeout_s INT NULL,
    connect_timeout_s INT NULL,
    latency_ms INT NULL,
    http_status INT NULL,
    input_tokens INT NULL,
    output_tokens INT NULL,
    total_tokens INT NULL,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    upstream_request_id VARCHAR(128) NULL,
    error_code VARCHAR(128) NULL,
    error_message LONGTEXT NULL,
    error_body_excerpt LONGTEXT NULL,
    config_version INT NULL,
    route_snapshot JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    INDEX llm_call_logs_task_id_created_at_idx (task_id, created_at),
    INDEX llm_call_logs_game_id_created_at_idx (game_id, created_at),
    INDEX llm_call_logs_step_key_created_at_idx (step_key, created_at),
    CONSTRAINT llm_call_logs_task_id_fkey FOREIGN KEY (task_id) REFERENCES generation_tasks(id) ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT llm_call_logs_game_id_fkey FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT llm_call_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT llm_call_logs_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES llm_gateway_providers(id) ON DELETE SET NULL ON UPDATE CASCADE
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS generation_artifacts (
    id VARCHAR(36) NOT NULL,
    task_id VARCHAR(36) NULL,
    game_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    artifact_type VARCHAR(64) NOT NULL,
    content_type VARCHAR(64) NOT NULL,
    storage_type VARCHAR(32) NOT NULL,
    payload_json JSON NULL,
    payload_text LONGTEXT NULL,
    payload_url VARCHAR(512) NULL,
    sha256 VARCHAR(64) NULL,
    size_bytes INT NULL,
    compression VARCHAR(32) NULL,
    expires_at DATETIME(3) NULL,
    metadata JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    INDEX generation_artifacts_task_id_artifact_type_created_at_idx (task_id, artifact_type, created_at),
    INDEX generation_artifacts_game_id_artifact_type_created_at_idx (game_id, artifact_type, created_at),
    INDEX generation_artifacts_user_id_artifact_type_created_at_idx (user_id, artifact_type, created_at),
    CONSTRAINT generation_artifacts_task_id_fkey FOREIGN KEY (task_id) REFERENCES generation_tasks(id) ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT generation_artifacts_game_id_fkey FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT generation_artifacts_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS game_creation_sessions (
    id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'collecting',
    entry_mode VARCHAR(32) NOT NULL DEFAULT 'create',
    initial_prompt LONGTEXT NOT NULL,
    title_draft VARCHAR(64) NULL,
    revision INT NOT NULL DEFAULT 1,
    slot_state JSON NOT NULL,
    missing_required JSON NOT NULL,
    skipped_slots JSON NOT NULL,
    current_question JSON NULL,
    conversation JSON NOT NULL,
    generated_game_id VARCHAR(36) NULL,
    generation_task_id VARCHAR(36) NULL,
    source_game_id VARCHAR(36) NULL,
    question_budget INT NOT NULL DEFAULT 4,
    metadata JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    INDEX game_creation_sessions_user_id_status_updated_at_idx (user_id, status, updated_at),
    INDEX game_creation_sessions_generated_game_id_idx (generated_game_id),
    INDEX game_creation_sessions_generation_task_id_idx (generation_task_id),
    CONSTRAINT game_creation_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS prompt_bundles (
    id VARCHAR(64) NOT NULL,
    version INT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    product_policy LONGTEXT NOT NULL,
    locked_contract_override LONGTEXT NULL,
    repair_playbook LONGTEXT NOT NULL,
    profile_overrides JSON NULL,
    metadata JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id, version),
    INDEX prompt_bundles_status_updated_at_idx (status, updated_at)
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
  `CREATE TABLE IF NOT EXISTS runtime_profile_catalog (
    id VARCHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    skeleton_version VARCHAR(32) NOT NULL,
    contract_schema JSON NOT NULL,
    few_shot_prompt LONGTEXT NULL,
    metadata JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    INDEX runtime_profile_catalog_enabled_updated_at_idx (enabled, updated_at)
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`,
];

const DEFAULT_LLM_STEP_CATALOG = [
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411001',
    stepKey: 'dialogue.slot_extract',
    stepOrder: 10,
    stageLabel: 'Flow 01 - Creation Session',
    displayName: '对话槽位提取',
    description: '在对话模式下从用户输入中抽取结构化槽位',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411002',
    stepKey: 'dialogue.reply',
    stepOrder: 20,
    stageLabel: 'Flow 01 - Creation Session',
    displayName: '对话回复生成',
    description: '在对话模式下生成追问或确认回复',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411003',
    stepKey: 'intent_parse',
    stepOrder: 30,
    stageLabel: 'Flow 02 - Structured Intent',
    displayName: '意图解析',
    description: '将自然语言描述解析成 GameSpec',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411005',
    stepKey: 'code_generate.full',
    stepOrder: 40,
    stageLabel: 'Flow 03 - Create Generation',
    displayName: '代码生成（Full LLM）',
    description: '完全依赖 LLM 生成首版代码',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411006',
    stepKey: 'qa_fix',
    stepOrder: 110,
    stageLabel: 'Flow 05 - QA Repair Families',
    displayName: 'QA 自动修复',
    description: '在静态或运行时 QA 失败后进行自动修复',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411007',
    stepKey: 'code_review',
    stepOrder: 50,
    stageLabel: 'Flow 03 - Create Generation',
    displayName: '代码审查',
    description: 'LLM 对生成结果进行完整性与可玩性审查',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411008',
    stepKey: 'iterate.classify',
    stepOrder: 70,
    stageLabel: 'Flow 04 - Iterate Generation',
    displayName: '迭代反馈分类',
    description: '判断用户反馈属于哪类迭代修改',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411009',
    stepKey: 'iterate.param_adjust',
    stepOrder: 80,
    stageLabel: 'Flow 04 - Iterate Generation',
    displayName: '迭代参数调整',
    description: '通过 LLM 调整游戏数值和参数',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411010',
    stepKey: 'iterate.element_change',
    stepOrder: 90,
    stageLabel: 'Flow 04 - Iterate Generation',
    displayName: '迭代元素修改',
    description: '通过 LLM 修改视觉元素和对象结构',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411011',
    stepKey: 'iterate.mechanic_change',
    stepOrder: 100,
    stageLabel: 'Flow 04 - Iterate Generation',
    displayName: '迭代机制改写',
    description: '通过 LLM 改写游戏核心机制',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411020',
    stepKey: 'qa_fix.syntax_structural',
    stepOrder: 120,
    stageLabel: 'Flow 05 - QA Repair Families',
    displayName: 'QA Fix / Syntax Structural',
    description: 'Only remaining QA repair path: full-document syntax and structural recovery.',
  },
  {
    id: '1e0207d2-a7de-4d49-b4bb-fcdbf0411021',
    stepKey: 'expand_prompt',
    stepOrder: 130,
    stageLabel: 'Flow 90 - Auxiliary',
    displayName: 'Prompt 扩写',
    description: '将用户短描述扩写为详细设计提示词',
  },
];

const DEFAULT_CLOUD_PROVIDER_ACCOUNT = {
  id: '9d5307f1-9ee8-4f45-8b18-4e29c0010001',
  vendor: 'volcengine',
  accountKey: 'volc-default',
  displayName: '火山引擎默认账号',
};

const DEFAULT_CLOUD_REGION_CATALOG = [
  {
    id: '9d5307f1-9ee8-4f45-8b18-4e29c0011001',
    vendor: 'volcengine',
    regionCode: 'cn-shanghai',
    regionName: '上海',
    regionGroup: 'cn_mainland',
  },
  {
    id: '9d5307f1-9ee8-4f45-8b18-4e29c0011002',
    vendor: 'volcengine',
    regionCode: 'ap-southeast-johor',
    regionName: '柔佛',
    regionGroup: 'overseas',
  },
];

const DEFAULT_PROMPT_BUNDLES = Array.isArray(promptBundleCatalog)
  ? promptBundleCatalog
  : [];

const DEFAULT_PROMPT_CATALOG = Array.isArray(promptCatalog)
  ? promptCatalog
  : [];

const REMOVED_PROMPT_CONFIG_KEYS = [
  'prompt.iteration_mobile_layout_guardrails',
  'prompt.qa_fix',
  'prompt.qa_fix_fast',
  'prompt.qa_instruction_input_handlers',
  'prompt.qa_instruction_visible_feedback',
  'prompt.qa_instruction_terminal_state',
  'prompt.qa_instruction_storage',
  'prompt.qa_instruction_blank_screen',
  'prompt.qa_instruction_runtime_js_error',
  'prompt.qa_instruction_mobile_layout',
  'prompt.qa_instruction_forbidden_api',
  'prompt.qa_instruction_generic',
  'bundle.repair.input_contract',
  'bundle.repair.terminal_state',
  'bundle.repair.mobile_layout',
  'bundle.repair.forbidden_api',
  'bundle.repair.runtime_startup',
  'bundle.repair.generic',
];

const REMOVED_LLM_STEP_KEYS = [
  'code_generate.hybrid',
  'llm_design.enrich',
  'qa_fix',
  'qa_fix.input_contract',
  'qa_fix.score_feedback',
  'qa_fix.terminal_state',
  'qa_fix.mobile_layout',
  'qa_fix.runtime_startup',
  'qa_fix.forbidden_api',
  'qa_fix.generic',
  'qa_fix.syntax_rebuild',
];

const MANAGED_LLM_ROUTE_ALIASES = [
  {
    targetStepKey: 'qa_fix.syntax_structural',
    sourceStepKey: 'code_generate.full',
    requestTimeoutS: 90,
    connectTimeoutS: 15,
  },
];

const DEFAULT_RUNTIME_PROFILE_CATALOG = Array.isArray(runtimeProfileCatalog)
  ? runtimeProfileCatalog
  : [];

@Injectable()
export class GameSchemaBootstrapService implements OnModuleInit {
  private readonly logger = new Logger(GameSchemaBootstrapService.name);

  constructor(private readonly prisma: PrismaService) {}

  private resolveLegacyExecutionRegion(): string {
    const explicit = (process.env.AI_ENGINE_DEFAULT_REGION || process.env.SERVICE_REGION || '').trim();
    if (explicit === 'ap_southeast_johor' || explicit === 'cn_shanghai') {
      return explicit;
    }

    const cloudRegion = (process.env.VOLCENGINE_REGION || '').trim();
    if (cloudRegion === 'ap-southeast-johor') {
      return 'ap_southeast_johor';
    }

    return 'cn_shanghai';
  }

  private buildDefaultRegionTargets() {
    const namespace = process.env.VOLCENGINE_REGISTRY_NAMESPACE || 'gamevallies';
    const legacyExecutionRegion = this.resolveLegacyExecutionRegion();
    const legacyCloudRegion =
      legacyExecutionRegion === 'ap_southeast_johor' ? 'ap-southeast-johor' : 'cn-shanghai';
    const legacyUrl = (process.env.AI_ENGINE_URL || '').trim();
    const shanghaiUrl = (process.env.AI_ENGINE_URL_CN_SHANGHAI || '').trim();
    const johorUrl = (process.env.AI_ENGINE_URL_AP_SOUTHEAST_JOHOR || '').trim();

    return [
      {
        id: '9d5307f1-9ee8-4f45-8b18-4e29c0012001',
        regionCatalogId: DEFAULT_CLOUD_REGION_CATALOG[0].id,
        vendor: 'volcengine',
        cloudRegionCode: 'cn-shanghai',
        executionRegion: 'cn_shanghai',
        displayName: 'AI Engine 上海主实例',
        functionName: 'gv-ai-engine-cn',
        registry: process.env.VOLCENGINE_REGISTRY_CN_SHANGHAI || process.env.VOLCENGINE_REGISTRY || 'gamevallies-repo-cn-shanghai.cr.volces.com',
        registryNamespace: namespace,
        imageRepository: 'gv-ai-engine-cn',
        serviceRegionEnv: 'cn_shanghai',
        aiEngineUrl: shanghaiUrl || (legacyExecutionRegion === 'cn_shanghai' && legacyCloudRegion === 'cn-shanghai' ? legacyUrl : ''),
      },
      {
        id: '9d5307f1-9ee8-4f45-8b18-4e29c0012002',
        regionCatalogId: DEFAULT_CLOUD_REGION_CATALOG[1].id,
        vendor: 'volcengine',
        cloudRegionCode: 'ap-southeast-johor',
        executionRegion: 'ap_southeast_johor',
        displayName: 'AI Engine 柔佛主实例',
        functionName: 'gv-ai-engine-global',
        registry: process.env.VOLCENGINE_REGISTRY_AP_SOUTHEAST_JOHOR || process.env.VOLCENGINE_REGISTRY || 'gamevallies-repo-ap-southeast-johor.cr.volces.com',
        registryNamespace: namespace,
        imageRepository: 'gv-ai-engine-global',
        serviceRegionEnv: 'ap_southeast_johor',
        aiEngineUrl: johorUrl || (legacyExecutionRegion === 'ap_southeast_johor' && legacyCloudRegion === 'ap-southeast-johor' ? legacyUrl : ''),
      },
    ];
  }

  private async normalizeLegacyLlmGatewayData(defaultTargets: ReturnType<GameSchemaBootstrapService['buildDefaultRegionTargets']>) {
    const shanghaiTarget = defaultTargets.find((target) => target.executionRegion === 'cn_shanghai');
    const johorTarget = defaultTargets.find((target) => target.executionRegion === 'ap_southeast_johor');

    if (shanghaiTarget) {
      await this.prisma.llmGatewayProvider.updateMany({
        where: { region: 'cn-shanghai' },
        data: {
          region: 'cn_shanghai',
          regionTargetId: shanghaiTarget.id,
          cloudVendor: 'volcengine',
          cloudRegionCode: shanghaiTarget.cloudRegionCode,
        },
      });
      await this.prisma.llmGatewayProvider.updateMany({
        where: { region: 'cn_shanghai', regionTargetId: null },
        data: {
          regionTargetId: shanghaiTarget.id,
          cloudVendor: 'volcengine',
          cloudRegionCode: shanghaiTarget.cloudRegionCode,
        },
      });
      await this.prisma.llmStepRoute.updateMany({
        where: { region: 'cn-shanghai' },
        data: { region: 'cn_shanghai' },
      });
    }

    if (johorTarget) {
      await this.prisma.llmGatewayProvider.updateMany({
        where: { region: 'ap-southeast-johor' },
        data: {
          region: 'ap_southeast_johor',
          regionTargetId: johorTarget.id,
          cloudVendor: 'volcengine',
          cloudRegionCode: johorTarget.cloudRegionCode,
        },
      });
      await this.prisma.llmGatewayProvider.updateMany({
        where: { region: 'ap_southeast_johor', regionTargetId: null },
        data: {
          regionTargetId: johorTarget.id,
          cloudVendor: 'volcengine',
          cloudRegionCode: johorTarget.cloudRegionCode,
        },
      });
      await this.prisma.llmStepRoute.updateMany({
        where: { region: 'ap-southeast-johor' },
        data: { region: 'ap_southeast_johor' },
      });
    }
  }

  private pickDefaultProviderForRegion(providers: Array<{
    id: string;
    name: string;
    baseUrl: string;
    model: string;
    priority: number;
    region: string;
  }>) {
    if (!providers.length) {
      return null;
    }

    return (
      providers.find((provider) => /minimax/i.test(`${provider.name} ${provider.baseUrl} ${provider.model}`))
      || providers[0]
    );
  }

  private async backfillDefaultLlmRoutes() {
    const [providers, existingRoutes] = await Promise.all([
      this.prisma.llmGatewayProvider.findMany({
        where: {
          enabled: true,
          regionTargetId: { not: null },
        },
        select: {
          id: true,
          name: true,
          baseUrl: true,
          model: true,
          priority: true,
          region: true,
        },
        orderBy: [{ priority: 'asc' }, { updatedAt: 'desc' }],
      }),
      this.prisma.llmStepRoute.findMany({
        select: {
          stepKey: true,
          region: true,
        },
      }),
    ]);

    const defaultProviders = new Map<string, { id: string }>();
    for (const region of ['cn_shanghai', 'ap_southeast_johor']) {
      const candidates = providers.filter((provider) => provider.region === region);
      const selected = this.pickDefaultProviderForRegion(candidates);
      if (selected) {
        defaultProviders.set(region, { id: selected.id });
      }
    }

    if (!defaultProviders.size) {
      return;
    }

    const existingRouteKeys = new Set(
      existingRoutes.map((route) => `${route.region}:${route.stepKey}`),
    );

    for (const step of DEFAULT_LLM_STEP_CATALOG) {
      for (const [region, provider] of defaultProviders.entries()) {
        const routeKey = `${region}:${step.stepKey}`;
        if (existingRouteKeys.has(routeKey)) {
          continue;
        }
        await this.prisma.llmStepRoute.upsert({
          where: {
            llm_step_routes_step_key_region_key: {
              stepKey: step.stepKey,
              region,
            },
          },
          create: {
            id: randomUUID(),
            stepKey: step.stepKey,
            region,
            providerId: provider.id,
            fallbackProviderIds: [],
            modelOverride: null,
            fastModelOverride: null,
            requestTimeoutS: null,
            connectTimeoutS: null,
            enabled: true,
          },
          update: {},
        });
      }
    }

    await this.syncManagedLlmRouteAliases();
  }

  private async syncManagedLlmRouteAliases() {
    if (!MANAGED_LLM_ROUTE_ALIASES.length) {
      return;
    }

    const existingRoutes = await this.prisma.llmStepRoute.findMany({
      select: {
        id: true,
        stepKey: true,
        region: true,
        providerId: true,
        fallbackProviderIds: true,
        modelOverride: true,
        fastModelOverride: true,
        enabled: true,
      },
    });

    for (const binding of MANAGED_LLM_ROUTE_ALIASES) {
      const sourceRoutes = existingRoutes.filter((route) => route.stepKey === binding.sourceStepKey);
      for (const sourceRoute of sourceRoutes) {
        await this.prisma.llmStepRoute.upsert({
          where: {
            llm_step_routes_step_key_region_key: {
              stepKey: binding.targetStepKey,
              region: sourceRoute.region,
            },
          },
          create: {
            id: randomUUID(),
            stepKey: binding.targetStepKey,
            region: sourceRoute.region,
            providerId: sourceRoute.providerId,
            fallbackProviderIds: sourceRoute.fallbackProviderIds || [],
            modelOverride: sourceRoute.modelOverride || null,
            fastModelOverride: sourceRoute.fastModelOverride || null,
            requestTimeoutS: binding.requestTimeoutS,
            connectTimeoutS: binding.connectTimeoutS,
            enabled: sourceRoute.enabled !== false,
          },
          update: {
            providerId: sourceRoute.providerId,
            fallbackProviderIds: sourceRoute.fallbackProviderIds || [],
            modelOverride: sourceRoute.modelOverride || null,
            fastModelOverride: sourceRoute.fastModelOverride || null,
            requestTimeoutS: binding.requestTimeoutS,
            connectTimeoutS: binding.connectTimeoutS,
            enabled: sourceRoute.enabled !== false,
          },
        });
      }
    }
  }

  async onModuleInit(): Promise<void> {
    try {
      for (const statement of GAME_SCHEMA_STATEMENTS) {
        await this.prisma.$executeRawUnsafe(statement);
      }

      const patchGroups = new Map<string, ColumnPatch[]>();
      for (const patch of TABLE_COLUMN_PATCHES) {
        const group = patchGroups.get(patch.table) || [];
        group.push(patch);
        patchGroups.set(patch.table, group);
      }

      for (const [tableName, patches] of patchGroups.entries()) {
        const existingColumns = await this.prisma.$queryRawUnsafe<Array<{ columnName: string }>>(
          `SELECT COLUMN_NAME AS columnName
           FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE()
             AND TABLE_NAME = '${tableName}'
             AND COLUMN_NAME IN (${patches.map((patch) => `'${patch.name}'`).join(', ')})`,
        );

        const existing = new Set(existingColumns.map((column) => column.columnName));
        for (const patch of patches) {
          if (!existing.has(patch.name)) {
            await this.prisma.$executeRawUnsafe(patch.sql);
          }
        }
      }

      await this.prisma.$executeRawUnsafe(
        `INSERT INTO cloud_provider_accounts (
           id, vendor, account_key, display_name, enabled, credential_mode,
           default_registry, default_registry_namespace, default_vpc_id, default_subnet_id,
           default_security_group_id, synced_at
         ) VALUES (?, ?, ?, ?, TRUE, 'env_secret', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP(3))
         ON DUPLICATE KEY UPDATE
           vendor = VALUES(vendor),
           display_name = VALUES(display_name),
           enabled = VALUES(enabled),
           credential_mode = VALUES(credential_mode),
           default_registry = VALUES(default_registry),
           default_registry_namespace = VALUES(default_registry_namespace),
           default_vpc_id = VALUES(default_vpc_id),
           default_subnet_id = VALUES(default_subnet_id),
           default_security_group_id = VALUES(default_security_group_id),
           synced_at = VALUES(synced_at)`,
        DEFAULT_CLOUD_PROVIDER_ACCOUNT.id,
        DEFAULT_CLOUD_PROVIDER_ACCOUNT.vendor,
        DEFAULT_CLOUD_PROVIDER_ACCOUNT.accountKey,
        DEFAULT_CLOUD_PROVIDER_ACCOUNT.displayName,
        process.env.VOLCENGINE_REGISTRY || 'gamevallies-repo-cn-shanghai.cr.volces.com',
        process.env.VOLCENGINE_REGISTRY_NAMESPACE || 'gamevallies',
        process.env.VOLCENGINE_VPC_ID || null,
        process.env.VOLCENGINE_SUBNET_ID || null,
        process.env.VOLCENGINE_SECURITY_GROUP_ID || null,
      );

      for (const region of DEFAULT_CLOUD_REGION_CATALOG) {
        await this.prisma.$executeRawUnsafe(
          `INSERT INTO cloud_region_catalog (
             id, account_id, vendor, region_code, region_name, region_group,
             vcr_supported, vefaas_supported, apig_supported, deploy_supported, enabled, synced_at
           ) VALUES (?, ?, ?, ?, ?, ?, TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_TIMESTAMP(3))
           ON DUPLICATE KEY UPDATE
             vendor = VALUES(vendor),
             region_name = VALUES(region_name),
             region_group = VALUES(region_group),
             vcr_supported = VALUES(vcr_supported),
             vefaas_supported = VALUES(vefaas_supported),
             apig_supported = VALUES(apig_supported),
             deploy_supported = VALUES(deploy_supported),
             enabled = VALUES(enabled),
             synced_at = VALUES(synced_at)`,
          region.id,
          DEFAULT_CLOUD_PROVIDER_ACCOUNT.id,
          region.vendor,
          region.regionCode,
          region.regionName,
          region.regionGroup,
        );
      }

      const defaultTargets = this.buildDefaultRegionTargets();

      for (const target of defaultTargets) {
        await this.prisma.$executeRawUnsafe(
          `INSERT INTO ai_engine_region_targets (
             id, account_id, region_catalog_id, vendor, cloud_region_code, execution_region,
             display_name, function_name, registry, registry_namespace, image_repository,
             service_region_env, ai_engine_url, deploy_enabled, deploy_status, last_deployed_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
           ON DUPLICATE KEY UPDATE
             vendor = VALUES(vendor),
             cloud_region_code = VALUES(cloud_region_code),
             display_name = VALUES(display_name),
             function_name = VALUES(function_name),
             registry = VALUES(registry),
             registry_namespace = VALUES(registry_namespace),
             image_repository = VALUES(image_repository),
             service_region_env = VALUES(service_region_env),
             ai_engine_url = CASE
               WHEN VALUES(ai_engine_url) IS NOT NULL AND VALUES(ai_engine_url) <> '' THEN VALUES(ai_engine_url)
               ELSE ai_engine_url
             END,
             deploy_enabled = VALUES(deploy_enabled),
             deploy_status = CASE
               WHEN VALUES(ai_engine_url) IS NOT NULL AND VALUES(ai_engine_url) <> '' THEN 'deployed'
               ELSE deploy_status
             END,
             last_deployed_at = CASE
               WHEN VALUES(ai_engine_url) IS NOT NULL AND VALUES(ai_engine_url) <> '' THEN VALUES(last_deployed_at)
               ELSE last_deployed_at
             END`,
          target.id,
          DEFAULT_CLOUD_PROVIDER_ACCOUNT.id,
          target.regionCatalogId,
          target.vendor,
          target.cloudRegionCode,
          target.executionRegion,
          target.displayName,
          target.functionName,
          target.registry,
          target.registryNamespace,
          target.imageRepository,
          target.serviceRegionEnv,
          target.aiEngineUrl || null,
          target.aiEngineUrl ? 'deployed' : 'pending',
          target.aiEngineUrl ? new Date() : null,
        );
      }

      for (const step of DEFAULT_LLM_STEP_CATALOG) {
        await this.prisma.$executeRawUnsafe(
          `INSERT INTO llm_step_catalog (
             id, step_key, step_order, stage_label, display_name, description, enabled
           ) VALUES (?, ?, ?, ?, ?, ?, TRUE)
           ON DUPLICATE KEY UPDATE
             step_order = VALUES(step_order),
             stage_label = VALUES(stage_label),
             display_name = VALUES(display_name),
             description = VALUES(description),
             enabled = VALUES(enabled)`,
          step.id,
          step.stepKey,
          step.stepOrder,
          step.stageLabel,
          step.displayName,
          step.description,
        );
      }

      if (REMOVED_LLM_STEP_KEYS.length) {
        const stepPlaceholders = REMOVED_LLM_STEP_KEYS.map(() => '?').join(', ');
        await this.prisma.$executeRawUnsafe(
          `DELETE FROM llm_step_routes
           WHERE step_key IN (${stepPlaceholders})`,
          ...REMOVED_LLM_STEP_KEYS,
        );
        await this.prisma.$executeRawUnsafe(
          `DELETE FROM llm_step_catalog
           WHERE step_key IN (${stepPlaceholders})`,
          ...REMOVED_LLM_STEP_KEYS,
        );
      }

      for (const prompt of DEFAULT_PROMPT_CATALOG) {
        await this.prisma.$executeRawUnsafe(
          `INSERT IGNORE INTO system_configs (
             id, config_key, config_value, description, category
           ) VALUES (?, ?, ?, ?, 'prompt')`,
          randomUUID(),
          prompt.key,
          prompt.value,
          prompt.description,
        );
      }

      if (REMOVED_PROMPT_CONFIG_KEYS.length) {
        const placeholders = REMOVED_PROMPT_CONFIG_KEYS.map(() => '?').join(', ');
        await this.prisma.$executeRawUnsafe(
          `DELETE FROM system_configs
           WHERE category = 'prompt'
             AND config_key IN (${placeholders})`,
          ...REMOVED_PROMPT_CONFIG_KEYS,
        );
      }

      for (const bundle of DEFAULT_PROMPT_BUNDLES) {
        await this.prisma.$executeRawUnsafe(
          `INSERT INTO prompt_bundles (
             id, version, status, product_policy, locked_contract_override,
             repair_playbook, profile_overrides, metadata
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
             status = VALUES(status),
             product_policy = VALUES(product_policy),
             locked_contract_override = VALUES(locked_contract_override),
             repair_playbook = VALUES(repair_playbook),
             profile_overrides = VALUES(profile_overrides),
             metadata = VALUES(metadata)`,
          bundle.id,
          bundle.version,
          bundle.status,
          bundle.productPolicy,
          bundle.lockedContractOverride,
          bundle.repairPlaybook,
          JSON.stringify(bundle.profileOverrides),
          JSON.stringify(bundle.metadata),
        );
      }

      for (const profile of DEFAULT_RUNTIME_PROFILE_CATALOG) {
        await this.prisma.$executeRawUnsafe(
          `INSERT INTO runtime_profile_catalog (
             id, display_name, enabled, skeleton_version, contract_schema,
             few_shot_prompt, metadata
           ) VALUES (?, ?, TRUE, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
             display_name = VALUES(display_name),
             enabled = VALUES(enabled),
             skeleton_version = VALUES(skeleton_version),
             contract_schema = VALUES(contract_schema),
             few_shot_prompt = VALUES(few_shot_prompt),
             metadata = VALUES(metadata)`,
          profile.id,
          profile.displayName,
          profile.skeletonVersion,
          JSON.stringify(profile.contractSchema),
          profile.fewShotPrompt,
          JSON.stringify(profile.metadata),
        );
      }

      for (const [legacyId, canonicalId] of Object.entries(LEGACY_TO_CANONICAL_RUNTIME_PROFILE_IDS)) {
        if (legacyId === canonicalId) {
          continue;
        }
        await this.prisma.$executeRawUnsafe(
          `UPDATE generation_tasks
           SET runtime_profile = ?
           WHERE runtime_profile = ?`,
          canonicalId,
          legacyId,
        );
        await this.prisma.$executeRawUnsafe(
          `DELETE FROM runtime_profile_catalog
           WHERE id = ?`,
          legacyId,
        );
      }

      await this.normalizeLegacyLlmGatewayData(defaultTargets);
      await this.backfillDefaultLlmRoutes();

      this.logger.log('Game schema bootstrap finished');
    } catch (error) {
      this.logger.error(`Game schema bootstrap failed: ${error.message}`);
    }
  }
}
