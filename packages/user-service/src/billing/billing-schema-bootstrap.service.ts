import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

const BILLING_SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS user_quotas (
    id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    total_free_quota INT NOT NULL DEFAULT 5,
    used_free_quota INT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY user_quotas_user_id_key (user_id),
    CONSTRAINT user_quotas_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE
  )`,
  `CREATE TABLE IF NOT EXISTS subscription_plans (
    id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    description VARCHAR(255) NULL,
    price INT NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'CNY',
    period ENUM('monthly', 'yearly') NOT NULL,
    quota INT NOT NULL,
    features JSON NOT NULL,
    recommended BOOLEAN NOT NULL DEFAULT FALSE,
    badge VARCHAR(32) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id)
  )`,
  `CREATE TABLE IF NOT EXISTS user_subscriptions (
    id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    plan_id VARCHAR(64) NOT NULL,
    status ENUM('active', 'expired', 'cancelled') NOT NULL DEFAULT 'active',
    started_at DATETIME(3) NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    used_this_period INT NOT NULL DEFAULT 0,
    quota_this_period INT NOT NULL,
    auto_renew BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY user_subscriptions_user_id_status_expires_at_idx (user_id, status, expires_at),
    CONSTRAINT user_subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT user_subscriptions_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES subscription_plans(id) ON DELETE RESTRICT ON UPDATE CASCADE
  )`,
  `CREATE TABLE IF NOT EXISTS subscription_orders (
    id VARCHAR(64) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    plan_id VARCHAR(64) NOT NULL,
    amount INT NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'CNY',
    status ENUM('pending', 'paid', 'failed', 'refunded', 'canceled') NOT NULL DEFAULT 'pending',
    provider VARCHAR(32) NOT NULL DEFAULT 'wechat_pay',
    out_trade_no VARCHAR(64) NOT NULL,
    payment_id VARCHAR(128) NULL,
    prepay_id VARCHAR(128) NULL,
    game_id_to_unlock VARCHAR(36) NULL,
    description VARCHAR(255) NULL,
    payment_payload JSON NULL,
    payment_response JSON NULL,
    paid_at DATETIME(3) NULL,
    expires_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY subscription_orders_out_trade_no_key (out_trade_no),
    KEY subscription_orders_user_id_status_created_at_idx (user_id, status, created_at),
    CONSTRAINT subscription_orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT subscription_orders_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES subscription_plans(id) ON DELETE RESTRICT ON UPDATE CASCADE
  )`,
  `ALTER TABLE games
    ADD COLUMN IF NOT EXISTS can_play BOOLEAN NOT NULL DEFAULT TRUE`,
  `ALTER TABLE games
    ADD COLUMN IF NOT EXISTS require_subscription BOOLEAN NOT NULL DEFAULT FALSE`,
  `ALTER TABLE games
    ADD COLUMN IF NOT EXISTS access_grant_source ENUM('none', 'free_quota', 'subscription_quota', 'subscription_unlock') NOT NULL DEFAULT 'none'`,
  `ALTER TABLE games
    ADD COLUMN IF NOT EXISTS access_grant_subscription_id VARCHAR(36) NULL`,
];

@Injectable()
export class BillingSchemaBootstrapService implements OnModuleInit {
  private readonly logger = new Logger(BillingSchemaBootstrapService.name);

  constructor(private readonly prisma: PrismaService) {}

  async onModuleInit(): Promise<void> {
    try {
      for (const statement of BILLING_SCHEMA_STATEMENTS) {
        await this.prisma.$executeRawUnsafe(statement);
      }
      this.logger.log('Billing schema bootstrap finished');
    } catch (error) {
      this.logger.error(`Billing schema bootstrap failed: ${error.message}`);
    }
  }
}
