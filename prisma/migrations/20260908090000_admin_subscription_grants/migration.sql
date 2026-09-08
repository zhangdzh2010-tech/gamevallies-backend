CREATE TABLE `admin_subscription_grants` (
 `id` VARCHAR(36) NOT NULL, `user_id` VARCHAR(36) NOT NULL, `plan_id` VARCHAR(64) NOT NULL,
 `subscription_id` VARCHAR(36) NOT NULL, `user_label` VARCHAR(255) NOT NULL, `plan_name` VARCHAR(128) NOT NULL,
 `quota` INTEGER NOT NULL, `started_at` DATETIME(3) NOT NULL, `expires_at` DATETIME(3) NOT NULL,
 `reason` VARCHAR(255) NOT NULL, `operator` VARCHAR(128) NOT NULL, `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 PRIMARY KEY (`id`), UNIQUE INDEX `admin_subscription_grants_subscription_id_key` (`subscription_id`),
 INDEX `admin_subscription_grants_user_id_created_at_idx` (`user_id`, `created_at`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
