export const GROWTH_CONFIG_CATEGORY = 'growth';

export const GROWTH_PROMO_CONFIG_KEYS = {
  enabled: 'growth.app_promo.enabled',
  wechatMode: 'growth.app_promo.wechat_mode',
  createCompleteEnabled: 'growth.app_promo.scene_create_complete_enabled',
  createCompleteCooldownHours: 'growth.app_promo.scene_create_complete_cooldown_hours',
  createCompleteMaxImpressions30d: 'growth.app_promo.scene_create_complete_max_impressions_30d',
  playNudgeEnabled: 'growth.app_promo.scene_play_nudge_enabled',
  playNudgeMinSessions: 'growth.app_promo.play_nudge_min_sessions',
  playNudgeMinSeconds: 'growth.app_promo.play_nudge_min_seconds',
  playNudgeCooldownHours: 'growth.app_promo.scene_play_nudge_cooldown_hours',
  playNudgeMaxImpressions30d: 'growth.app_promo.scene_play_nudge_max_impressions_30d',
  universalUrl: 'growth.app_promo.universal_url',
  copyJson: 'growth.app_promo.copy_json',
} as const;

export const DEFAULT_APP_PROMO_COPY = {
  createComplete: {
    title: '下载 App，继续管理你的作品',
    body: '继续编辑、优化、发布和管理你的游戏。',
    primaryCta: '下载 App',
    secondaryCta: '继续使用 H5',
  },
  playNudge: {
    title: '在 App 中体验更完整',
    body: '更流畅地试玩、收藏和长期管理你的游戏体验。',
    primaryCta: '下载 App',
    secondaryCta: '继续试玩',
  },
  wechatGuide: {
    title: '请在浏览器中打开',
    body: '当前环境不支持直接下载，请先在浏览器中打开页面。',
  },
};

export const DEFAULT_APP_PROMO_CONFIG = {
  enabled: true,
  wechatMode: 'guide_to_browser',
  scenes: {
    createComplete: {
      enabled: true,
      cooldownHours: 168,
      maxImpressions30d: 1,
    },
    playNudge: {
      enabled: true,
      minSessions: 3,
      minSeconds: 180,
      cooldownHours: 72,
      maxImpressions30d: 3,
    },
  },
  links: {
    universalUrl: '',
    iosUrl: '',
    androidUrl: '',
  },
  copy: DEFAULT_APP_PROMO_COPY,
};
