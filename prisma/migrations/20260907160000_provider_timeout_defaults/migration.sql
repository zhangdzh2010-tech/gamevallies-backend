-- Keep new providers aligned with the requested 30 minute LLM defaults.
-- Existing provider overrides are preserved and remain managed by the admin API.
ALTER TABLE `llm_gateway_providers`
  ALTER COLUMN `request_timeout_s` SET DEFAULT 1800,
  ALTER COLUMN `connect_timeout_s` SET DEFAULT 1800;
