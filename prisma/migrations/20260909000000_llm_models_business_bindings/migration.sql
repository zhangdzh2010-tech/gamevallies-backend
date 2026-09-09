-- Additive migration. Old providers/routes remain untouched for rollback.
CREATE TABLE `llm_gateway_models` (
  `id` VARCHAR(36) NOT NULL,
  `provider_id` VARCHAR(36) NOT NULL,
  `model_id` VARCHAR(128) NOT NULL,
  `name` VARCHAR(128) NOT NULL,
  `enabled` BOOLEAN NOT NULL DEFAULT true,
  `context_window` INTEGER NULL,
  `max_output_tokens` INTEGER NULL,
  `capability_flags` JSON NOT NULL,
  `configuration_version` INTEGER NOT NULL DEFAULT 1,
  `latest_test` JSON NULL,
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE INDEX `llm_gateway_models_provider_id_model_id_key` (`provider_id`, `model_id`),
  CONSTRAINT `llm_gateway_models_provider_id_fkey` FOREIGN KEY (`provider_id`) REFERENCES `llm_gateway_providers` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE `llm_business_bindings` (
  `id` VARCHAR(36) NOT NULL,
  `region` VARCHAR(32) NOT NULL,
  `stage` VARCHAR(32) NOT NULL,
  `step_keys` JSON NOT NULL,
  `primary_model_id` VARCHAR(36) NOT NULL,
  `fallback_model_id` VARCHAR(36) NULL,
  `revision` INTEGER NOT NULL DEFAULT 1,
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE INDEX `llm_business_bindings_region_stage_key` (`region`, `stage`),
  CONSTRAINT `llm_business_bindings_primary_model_id_fkey` FOREIGN KEY (`primary_model_id`) REFERENCES `llm_gateway_models` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `llm_business_bindings_fallback_model_id_fkey` FOREIGN KEY (`fallback_model_id`) REFERENCES `llm_gateway_models` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Import legacy defaults and explicitly routed models, but do not activate any
-- business bindings: mixed legacy routes must be confirmed by an administrator.
INSERT INTO `llm_gateway_models`
 (`id`, `provider_id`, `model_id`, `name`, `enabled`, `context_window`, `max_output_tokens`, `capability_flags`, `updated_at`)
SELECT UUID(), p.id, candidate.model_id, candidate.model_id, p.enabled,
  CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(p.extra_config, '$.contextWindow')), 'null') AS UNSIGNED),
  CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(p.extra_config, '$.maxTokens')), 'null') AS UNSIGNED),
  COALESCE(JSON_EXTRACT(p.extra_config, '$.capabilityFlags'), p.capability_flags, JSON_OBJECT()), CURRENT_TIMESTAMP(3)
FROM (
  SELECT id AS provider_id, model AS model_id FROM llm_gateway_providers
  UNION SELECT id, fast_model FROM llm_gateway_providers
  UNION SELECT provider_id, model_override FROM llm_step_routes
  UNION SELECT provider_id, fast_model_override FROM llm_step_routes
  UNION
  SELECT p.id, CASE JSON_TYPE(j.entry)
    WHEN 'STRING' THEN JSON_UNQUOTE(j.entry)
    WHEN 'OBJECT' THEN JSON_UNQUOTE(JSON_EXTRACT(j.entry, '$.id'))
    ELSE NULL END
  FROM llm_gateway_providers p
  JOIN JSON_TABLE(COALESCE(JSON_EXTRACT(p.extra_config, '$.availableModels'), JSON_ARRAY()),
    '$[*]' COLUMNS(entry JSON PATH '$')) j ON TRUE
) candidate JOIN llm_gateway_providers p ON p.id = candidate.provider_id
WHERE candidate.model_id IS NOT NULL AND TRIM(candidate.model_id) <> '';
