-- Synthetic fixture for an isolated disposable database only.
INSERT INTO llm_gateway_providers
 (id, name, provider_type, region, base_url, api_key, model, fast_model, extra_config, updated_at)
VALUES ('qa-provider', 'Fixture service', 'openai_compatible', 'cn_shanghai', 'https://fixture.invalid/v1',
 'synthetic-not-a-real-key', 'main-model', 'fast-model',
 JSON_OBJECT('maxTokens', 20000, 'contextWindow', 64000,
  'capabilityFlags', JSON_OBJECT('supports_dialogue', false),
  'availableModels', JSON_ARRAY('main-model', 'unused-model', JSON_OBJECT('id', 'object-model'))), NOW(3));
INSERT INTO llm_step_routes
 (id, step_key, region, provider_id, model_override, fast_model_override, fallback_provider_ids, updated_at)
VALUES ('qa-route', 'code_generate.full', 'cn_shanghai', 'qa-provider', 'custom-model', 'custom-fast-model', JSON_ARRAY(), NOW(3));
