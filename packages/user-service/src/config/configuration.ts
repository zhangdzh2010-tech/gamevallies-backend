export default () => ({
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
    origin: process.env.CORS_ORIGIN || '*',
    credentials: process.env.CORS_CREDENTIALS === 'true',
  },
  redis: {
    url: process.env.REDIS_URL,
  },
  wechat: {
    miniappAppId: process.env.WECHAT_MINIAPP_APP_ID,
    miniappAppSecret: process.env.WECHAT_MINIAPP_APP_SECRET,
  },
  app: {
    name: 'PlayForge User Service',
    version: '1.0.0',
  },
});
