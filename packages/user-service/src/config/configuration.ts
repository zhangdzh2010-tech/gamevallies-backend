import { applyPaymentEnvAliases } from '../billing/payment-env';
import { resolveCorsOrigin } from '../common/utils/cors-origin';

export default () => {
  // ConfigModule loads .env files first, then this factory. Copy leftover
  // zltokens-pack names onto the canonical WECHAT_PAY_* / app-id keys.
  applyPaymentEnvAliases();

  return {
    node_env: process.env.NODE_ENV || 'development',
    port: parseInt(process.env.PORT || '3001', 10),
    database: {
      url: process.env.DATABASE_URL,
    },
    jwt: {
      secret: process.env.JWT_SECRET,
      refresh_secret: process.env.JWT_REFRESH_SECRET,
      expires_in: process.env.JWT_EXPIRES_IN || '15m',
      refresh_expires_in: process.env.JWT_REFRESH_EXPIRES_IN || '7d',
    },
    cors: {
      origin: resolveCorsOrigin(),
      credentials: true,
    },
    redis: {
      url: process.env.REDIS_URL,
    },
    wechat: {
      miniappAppId: process.env.WECHAT_MINIAPP_APP_ID,
      miniappAppSecret: process.env.WECHAT_MINIAPP_APP_SECRET,
      h5AppId: process.env.WECHAT_H5_APP_ID,
      h5AppSecret: process.env.WECHAT_H5_APP_SECRET,
      h5OauthScope: process.env.WECHAT_H5_OAUTH_SCOPE || 'snsapi_base',
    },
    app: {
      name: 'PlayForge User Service',
      version: '1.0.0',
    },
  };
};
