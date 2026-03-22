# GameVallies 计费系统 & 分享转发 — 前后端对接文档

> 版本：v1.0  
> 日期：2026-03-21  
> 状态：待后端确认

---

## 一、核心业务规则

| 规则 | 说明 |
|------|------|
| **免费额度** | 新用户注册赠送 N 次免费创建游戏额度，N 由后端配置传入 |
| **付费限制范围** | **仅限用户自己创建的游戏**。浏览和试玩他人发布的游戏不受影响 |
| **额度耗尽后** | 仍可正常创建游戏，但创建的游戏**标记为不可试玩**，试玩按钮显示锁定状态 |
| **付费转化** | 锁定游戏点击试玩时，弹出订阅引导弹窗（Paywall），勾起付费欲望 |
| **订阅生效** | 支付成功后，立即解锁当前锁定的游戏 + 按订阅套餐配额扣减 |
| **试玩后分享** | 用户试玩自己（或他人）游戏后，可一键分享/转发给好友 |

---

## 二、用户核心流程

### 2.1 创建游戏流程

```
用户点击「创建游戏」
  → 检查登录（已有逻辑，不变）
  → 正常进入 AI 创作流程（不限流，无论是否有额度）
  → 调用 POST /api/v1/games/generate
  → 后端返回 canPlay / quotaRemaining
     ├── canPlay = true → 试玩按钮可用（有免费额度 或 订阅有效）
     └── canPlay = false → 试玩按钮锁定 🔒 + "订阅后解锁试玩"
         → 点击试玩 → 弹出 PaywallPopup
```

### 2.2 订阅付费流程

```
用户选择套餐
  → POST /api/v1/subscription/order → 获取微信支付参数
  → wx.requestPayment（唤起微信支付）
  → 支付成功回调
  → 刷新配额 GET /api/v1/users/quota
  → 自动解锁当前锁定的游戏（canPlay → true）
  → 更新 UI 状态
```

### 2.3 试玩后分享流程

```
用户试玩游戏结束（点击关闭/返回）
  → 弹出分享引导面板（半屏弹窗）
     ├── 「分享给好友」 → wx.shareAppMessage（转发到聊天）
     ├── 「生成海报」 → canvas 绘制海报图保存到相册
     └── 「继续创作」 → 跳转到创建页
  → 分享文案默认值：「我创作了一部非常有意思的游戏，大家一起来玩吧」
  → 分享卡片携带 gameId 参数，点击可直达游戏详情页
```

---

## 三、后端 API 接口协议

### 3.1 获取用户配额信息

```
GET /api/v1/users/quota
Authorization: Bearer {token}
```

**Response:**

```json
{
  "code": 0,
  "data": {
    "freeQuota": 3,
    "totalFreeQuota": 5,
    "subscription": {
      "active": false,
      "planId": null,
      "planName": null,
      "expiresAt": null,
      "usedThisPeriod": 0,
      "quotaThisPeriod": 0
    }
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `freeQuota` | number | 剩余免费创建次数 |
| `totalFreeQuota` | number | 总免费次数（前端展示 "已用 X / 总 Y 次"） |
| `subscription.active` | boolean | 是否有活跃订阅 |
| `subscription.planId` | string \| null | 当前订阅套餐 ID |
| `subscription.planName` | string \| null | 当前订阅套餐名称 |
| `subscription.expiresAt` | string \| null | 订阅到期时间 (ISO 8601) |
| `subscription.usedThisPeriod` | number | 本计费周期已使用次数 |
| `subscription.quotaThisPeriod` | number | 本计费周期总次数 |

---

### 3.2 获取订阅套餐列表

```
GET /api/v1/subscription/plans
```

**Response:**

```json
{
  "code": 0,
  "data": {
    "plans": [
      {
        "id": "plan_monthly_basic",
        "name": "基础月卡",
        "price": 990,
        "priceDisplay": "9.9",
        "currency": "CNY",
        "period": "monthly",
        "periodLabel": "月",
        "quota": 10,
        "quotaLabel": "10次/月",
        "features": ["每月10次创建", "AI迭代优化", "优先生成"],
        "recommended": false,
        "badge": null
      },
      {
        "id": "plan_monthly_pro",
        "name": "专业月卡",
        "price": 1990,
        "priceDisplay": "19.9",
        "currency": "CNY",
        "period": "monthly",
        "periodLabel": "月",
        "quota": 30,
        "quotaLabel": "30次/月",
        "features": ["每月30次创建", "无限AI迭代", "优先生成", "专属客服"],
        "recommended": true,
        "badge": "推荐"
      }
    ],
    "subscriberCount": 1234
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 套餐唯一标识 |
| `name` | string | 套餐名称 |
| `price` | number | 价格，单位：**分**（990 = 9.90元） |
| `priceDisplay` | string | 前端展示用价格字符串 |
| `period` | string | 周期类型：`monthly` / `yearly` |
| `periodLabel` | string | 周期文案：`月` / `年` |
| `quota` | number | 每周期可创建游戏次数 |
| `quotaLabel` | string | 次数文案：`10次/月` |
| `features` | string[] | 套餐特性列表（前端逐行展示） |
| `recommended` | boolean | 是否为推荐套餐（前端高亮） |
| `badge` | string \| null | 角标文案（如 "推荐"、"热门"） |
| `subscriberCount` | number | 总订阅人数（前端展示 "已有 X 人订阅"） |

---

### 3.3 创建订阅订单（调起微信支付）

```
POST /api/v1/subscription/order
Authorization: Bearer {token}
Content-Type: application/json
```

**Request:**

```json
{
  "planId": "plan_monthly_pro"
}
```

**Response:**

```json
{
  "code": 0,
  "data": {
    "orderId": "order_20260321_xxxxx",
    "payment": {
      "timeStamp": "1742534400",
      "nonceStr": "abc123def456",
      "package": "prepay_id=wx21xxxxxxxxxx",
      "signType": "RSA",
      "paySign": "xxxxxxxxxxxxxxxx"
    }
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `orderId` | string | 订单 ID（前端用于查询支付状态） |
| `payment` | object | 微信支付参数，**直接透传给 `wx.requestPayment`** |
| `payment.timeStamp` | string | 时间戳 |
| `payment.nonceStr` | string | 随机字符串 |
| `payment.package` | string | 预支付交易会话标识 |
| `payment.signType` | string | 签名方式 |
| `payment.paySign` | string | 签名 |

> **后端处理**：创建订单时，后端需要记录当前待解锁的 gameId（如果有的话），以便支付成功后自动关联解锁。

---

### 3.4 查询订阅状态

```
GET /api/v1/subscription/status
Authorization: Bearer {token}
```

**Response:**

```json
{
  "code": 0,
  "data": {
    "active": true,
    "planId": "plan_monthly_pro",
    "planName": "专业月卡",
    "expiresAt": "2026-04-21T00:00:00Z",
    "usedThisPeriod": 3,
    "quotaThisPeriod": 30,
    "autoRenew": true
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `active` | boolean | 订阅是否有效 |
| `planId` | string | 当前套餐 ID |
| `planName` | string | 当前套餐名称 |
| `expiresAt` | string | 到期时间 (ISO 8601) |
| `usedThisPeriod` | number | 本周期已用次数 |
| `quotaThisPeriod` | number | 本周期总次数 |
| `autoRenew` | boolean | 是否自动续费 |

---

### 3.5 创建游戏接口（已有，需扩展返回字段）

```
POST /api/v1/games/generate（已有接口）
```

**Response 新增字段：**

```json
{
  "code": 0,
  "data": {
    "gameId": "game_xxx",
    "title": "xxx",
    "gameUrl": "xxx",
    "emoji": "🎮",
    "description": "xxx",
    "tags": ["xxx"],
    "canPlay": true,
    "quotaRemaining": 2,
    "requireSubscription": false
  }
}
```

| 新增字段 | 类型 | 说明 |
|---------|------|------|
| `canPlay` | boolean | **是否可试玩**。后端根据用户免费额度 / 订阅状态判断 |
| `quotaRemaining` | number | 创建后剩余额度（免费 + 订阅总和） |
| `requireSubscription` | boolean | 是否需要订阅才能试玩（`canPlay=false` 时为 `true`） |

> **后端处理逻辑**：
> - 创建游戏时，后端自动扣减额度（优先免费额度，其次订阅额度）
> - 如果用户既无免费额度也无订阅 → 仍允许创建，但 `canPlay = false`
> - 扣减成功 → `canPlay = true`

---

### 3.6 解锁游戏（支付成功后调用）

```
POST /api/v1/games/{gameId}/unlock
Authorization: Bearer {token}
```

**Response:**

```json
{
  "code": 0,
  "data": {
    "unlocked": true,
    "canPlay": true,
    "quotaRemaining": 29
  }
}
```

> **说明**：支付成功后前端调用此接口解锁指定游戏。后端验证用户是否有有效订阅，如有则扣减一次订阅额度并解锁。  
> **注意**：如果后端在 3.3 创建订单时已自动关联 gameId，支付回调中自动解锁，则此接口可省略。

---

## 四、前端改动清单

### 4.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/services/subscription.js` | 封装订阅相关 API（4个接口） |
| `src/stores/quotaStore.js` | Zustand store，管理用户配额状态 |
| `src/components/common/PaywallPopup.jsx` | 订阅引导弹窗组件 |
| `src/components/common/PaywallPopup.scss` | 弹窗样式 |
| `src/components/common/SharePanel.jsx` | 分享引导面板组件 |
| `src/components/common/SharePanel.scss` | 分享面板样式 |
| `src/pages/subscription/index.jsx` | 订阅套餐详情页 |
| `src/pages/subscription/index.scss` | 订阅页样式 |

### 4.2 修改文件

| 文件 | 改动内容 |
|------|---------|
| `src/app.config.js` | 注册 `/pages/subscription/index` 路由 |
| `src/utils/authNavigation.js` | `openCreatePageWithAuth` 不限流，创建不限 |
| `src/pages/create/index.jsx` | 创建成功后读取 `canPlay`，若为 `false` 则试玩按钮显示锁定 |
| `src/stores/gamePlayer.js` | `openGame` 增加参数 `canPlay`，不可玩时拦截并弹出 Paywall |
| `src/pages/game/detail/index.jsx` | 试玩按钮增加付费检查；试玩结束后弹出分享面板 |
| `src/pages/index/index.jsx` | 首页 GameCard 试玩回调增加付费检查（仅自己创建的游戏） |
| `src/pages/profile/index.jsx` | 个人页展示额度信息 + 订阅状态 + 管理入口 |
| `src/components/common/GameCard.jsx` | 试玩回调透传 `canPlay` 和 `isOwnGame` 参数 |

---

## 五、关键交互设计

### 5.1 PaywallPopup 订阅弹窗

```
┌──────────────────────────────┐
│          ✨ 解锁无限创作        │
│                              │
│    你的免费额度已用完           │
│    订阅后可继续创作和试玩       │
│                              │
│  ┌────────────────────────┐  │
│  │    基础月卡              │  │
│  │    ¥9.9/月              │  │
│  │    10次/月               │  │
│  │    · 每月10次创建         │  │
│  │    · AI迭代优化           │  │
│  │    · 优先生成             │  │
│  │       [ 立即订阅 ]        │  │
│  └────────────────────────┘  │
│                              │
│  ┌─ 推荐 ──────────────┐     │
│  │    专业月卡              │  │
│  │    ¥19.9/月             │  │
│  │    30次/月              │  │
│  │    · 每月30次创建         │  │
│  │    · 无限AI迭代           │  │
│  │    · 优先生成             │  │
│  │    · 专属客服             │  │
│  │  [ 立即订阅 ] ← 主色按钮  │  │
│  └────────────────────────┘  │
│                              │
│       已有 1,234 人订阅        │
└──────────────────────────────┘
```

- **触发时机**：用户点击自己创建的锁定游戏的「试玩」按钮
- **弹窗形式**：从底部弹出，半屏高度，可下拉关闭
- **支付流程**：选择套餐 → `wx.requestPayment` → 成功后关闭弹窗 + 刷新额度 + 解锁游戏
- **关闭按钮**：右上角 × 号 + 遮罩点击关闭

### 5.2 SharePanel 分享面板

```
┌──────────────────────────────┐
│        🎉 游戏创作完成！       │
│                              │
│    ┌────────────────────┐    │
│    │   [游戏缩略图]       │    │
│    │   游戏标题           │    │
│    └────────────────────┘    │
│                              │
│    ┌────────────────────┐    │
│    │  📤  分享给好友      │    │
│    └────────────────────┘    │
│    ┌────────────────────┐    │
│    │  🖼  生成海报       │    │
│    └────────────────────┘    │
│    ┌────────────────────┐    │
│    │  ✏️  继续创作       │    │
│    └────────────────────┘    │
│                              │
└──────────────────────────────┘
```

- **触发时机**：用户从试玩页面返回后弹出
- **默认分享文案**：`"我创作了一部非常有意思的游戏，大家一起来玩吧"`
- **分享卡片**：携带 `gameId` 参数，好友点击直达游戏详情页
- **生成海报**：Canvas 绘制包含游戏名称、二维码、小程序码的图片，保存到相册
- **面板形式**：从底部弹出，ActionSheet 风格

---

## 六、前端状态管理

### quotaStore（Zustand）

```javascript
// src/stores/quotaStore.js 状态结构
{
  // 数据
  freeQuota: 0,
  totalFreeQuota: 0,
  subscription: {
    active: false,
    planId: null,
    planName: null,
    expiresAt: null,
    usedThisPeriod: 0,
    quotaThisPeriod: 0,
  },
  plans: [],           // 套餐列表
  subscriberCount: 0,  // 订阅人数
  
  // UI 状态
  loading: false,
  showPaywall: false,
  pendingGameId: null, // 待解锁的 gameId

  // Actions
  fetchQuota: () => {},           // GET /users/quota
  fetchPlans: () => {},           // GET /subscription/plans
  openPaywall: (gameId) => {},    // 打开付费墙
  closePaywall: () => {},         // 关闭付费墙
  subscribe: (planId) => {},      // 创建订单 + 调起支付
  refreshAfterPayment: () => {},  // 支付成功后刷新
  updateAfterCreate: (canPlay, quotaRemaining) => {}, // 创建后更新
}
```

### 缓存策略

| 场景 | 操作 |
|------|------|
| 用户登录成功 | 立即拉取 `fetchQuota()` + `fetchPlans()` |
| 支付成功 | `refreshAfterPayment()` 立即刷新 |
| 创建游戏响应 | `updateAfterCreate(canPlay, quotaRemaining)` 增量更新 |
| 页面 `onShow` | 按 TTL（5分钟）条件刷新 `fetchQuota()` |
| Token 失效/退出登录 | 清空所有 quotaStore 状态 |

---

## 七、微信支付对接要点

### 7.1 前端调用流程

```javascript
// 1. 用户选择套餐，调起支付
const order = await subscriptionService.createOrder(planId);

// 2. 唤起微信支付
await Taro.requestPayment({
  timeStamp: order.payment.timeStamp,
  nonceStr: order.payment.nonceStr,
  package: order.payment.package,
  signType: order.payment.signType,
  paySign: order.payment.paySign,
});

// 3. 支付成功，刷新状态
await quotaStore.refreshAfterPayment();
Taro.showToast({ title: '订阅成功', icon: 'success' });
```

### 7.2 后端需要实现

1. **微信支付下单**：调用微信 JSAPI 下单接口，返回预支付参数
2. **支付回调**：接收微信支付结果通知，更新订单状态、订阅状态
3. **自动解锁**：支付成功后，如果订单关联了 gameId，自动设置 `canPlay = true`
4. **订阅到期处理**：定时任务检查到期订阅，到期后 `active = false`，已创建游戏不受影响（只影响后续创建时的额度扣减）

---

## 八、分享转发 — 技术实现

### 8.1 分享配置（app.config.js 或页面级别）

```javascript
// 页面 onShareAppMessage
onShareAppMessage() {
  return {
    title: '我创作了一部非常有意思的游戏，大家一起来玩吧',
    path: `/pages/game/detail/index?id=${this.gameId}`,
    imageUrl: this.gameCover || '',
  };
}

// 页面 onShareTimeline（朋友圈）
onShareTimeline() {
  return {
    title: '我创作了一部非常有意思的游戏，大家一起来玩吧',
    path: `/pages/game/detail/index?id=${this.gameId}`,
    imageUrl: this.gameCover || '',
  };
}
```

### 8.2 生成海报

- 使用 Taro Canvas API 绘制
- 海报内容：游戏名称 + 小程序码 + 默认分享文案
- 调用 `Taro.saveImageToPhotosAlbum` 保存到相册
- 需要后端提供小程序码生成接口（或使用 `wxacode.getUnlimited`）

---

## 九、后端数据模型建议

```sql
-- 用户配额表
CREATE TABLE user_quotas (
  user_id         VARCHAR(64) PRIMARY KEY,
  free_quota      INT DEFAULT 0,        -- 剩余免费次数
  total_free_quota INT DEFAULT 0,       -- 总免费次数
  updated_at      TIMESTAMP
);

-- 订阅套餐表
CREATE TABLE subscription_plans (
  id              VARCHAR(64) PRIMARY KEY,
  name            VARCHAR(128),
  price           INT,                   -- 单位：分
  period          ENUM('monthly','yearly'),
  quota           INT,                   -- 每周期次数
  features        JSON,                  -- 特性列表
  recommended     BOOLEAN DEFAULT FALSE,
  sort_order      INT DEFAULT 0,
  active          BOOLEAN DEFAULT TRUE
);

-- 用户订阅表
CREATE TABLE user_subscriptions (
  id              VARCHAR(64) PRIMARY KEY,
  user_id         VARCHAR(64),
  plan_id         VARCHAR(64),
  status          ENUM('active','expired','cancelled'),
  started_at      TIMESTAMP,
  expires_at      TIMESTAMP,
  used_this_period INT DEFAULT 0,
  quota_this_period INT,
  auto_renew      BOOLEAN DEFAULT FALSE,
  created_at      TIMESTAMP
);

-- 订阅订单表
CREATE TABLE subscription_orders (
  id              VARCHAR(64) PRIMARY KEY,
  user_id         VARCHAR(64),
  plan_id         VARCHAR(64),
  amount          INT,                   -- 单位：分
  status          ENUM('pending','paid','failed','refunded'),
  payment_id      VARCHAR(128),          -- 微信支付单号
  game_id_to_unlock VARCHAR(64),         -- 待解锁的游戏ID（可选）
  created_at      TIMESTAMP,
  paid_at         TIMESTAMP
);

-- 游戏表（新增字段）
ALTER TABLE games ADD COLUMN can_play BOOLEAN DEFAULT TRUE;
ALTER TABLE games ADD COLUMN require_subscription BOOLEAN DEFAULT FALSE;
```

---

## 十、待确认事项

| # | 问题 | 默认方案 |
|---|------|---------|
| 1 | 付费限制是否仅限自己创建的游戏？ | ✅ 已确认：仅限自己创建的 |
| 2 | 无额度时是否仍允许创建？ | ✅ 已确认：允许创建但锁定试玩 |
| 3 | 分享转发默认文案？ | ✅ "我创作了一部非常有意思的游戏，大家一起来玩吧" |
| 4 | 海报是否需要小程序码？ | 需后端提供 `wxacode.getUnlimited` 接口，或前端用 QRCode 库生成 |
| 5 | 订阅到期后已解锁的游戏是否重新锁定？ | 建议不锁定，只影响后续创建 |
| 6 | 是否支持自动续费（委托代扣）？ | 暂不支持，后续可加 |
| 7 | 新用户免费额度默认值？ | 由后端配置，建议 3~5 次 |

---

## 十一、实施优先级

| 优先级 | 功能 | 依赖 |
|--------|------|------|
| P0 | 后端 5 个 API 接口 | 无 |
| P0 | 前端 quotaStore + subscription service | 后端 API |
| P0 | 创建游戏后 canPlay 判断 + 锁定状态 | 后端 API |
| P0 | PaywallPopup 订阅弹窗 | 后端 API + 微信支付 |
| P1 | SharePanel 分享面板 | 无（纯前端） |
| P1 | 个人页额度展示 | 后端 API |
| P2 | 生成海报 | 后端小程序码接口 |
| P2 | 订阅管理页 | 后端 API |
