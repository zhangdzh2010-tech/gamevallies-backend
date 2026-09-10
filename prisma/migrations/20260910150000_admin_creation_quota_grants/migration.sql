CREATE TABLE `admin_creation_quota_grants` (
 `id` VARCHAR(36) NOT NULL,
 `user_id` VARCHAR(36) NOT NULL,
 `user_label` VARCHAR(255) NOT NULL,
 `amount` INTEGER NOT NULL,
 `total_before` INTEGER NOT NULL,
 `total_after` INTEGER NOT NULL,
 `used_at_grant` INTEGER NOT NULL,
 `reason` VARCHAR(255) NOT NULL,
 `operator` VARCHAR(128) NOT NULL,
 `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 PRIMARY KEY (`id`),
 INDEX `admin_creation_quota_grants_user_id_created_at_idx` (`user_id`, `created_at`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
