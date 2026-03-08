-- Gamevallies MySQL 8.0 Initialization Script
-- This runs on first container start

-- 设置字符集
ALTER DATABASE gamevallies CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 授权
GRANT ALL PRIVILEGES ON gamevallies.* TO 'gamevallies'@'%';
FLUSH PRIVILEGES;
