# 支付宝前端对接说明

更新时间：2026-04-05

## 1. 当前结论

前端不需要直接对接支付宝开放平台，也不需要自己做签名。

前端只需要调用 GameVallies 后端的订阅接口：

1. 获取套餐列表
2. 创建支付宝订单
3. 跳转到后端返回的 `payUrl`
4. 支付完成后回到业务页面
5. 轮询订单状态和订阅状态

当前后端对前端暴露的支付宝支付能力是：

- H5 支付：`provider=alipay_wap`
- PC 网页支付：`provider=alipay_page`

移动端 H5 推荐使用：

- `provider=alipay_wap`

## 2. 当前线上状态

截至 2026-04-05，后端支付宝代码已经上线，但**生产环境已临时切回 mock 安全态**。

原因是这轮真实联调发现生产验签公钥配置有误，存在“伪造回调被验过”的风险，因此真实支付入口已被主动回退，等待后端修正 `ALIPAY_PUBLIC_KEY` 后再恢复真实收款。

所以前端现在可以先完成接口对接，但**不要把真实支付宝支付入口正式放量**，等后端确认“已切回 `ALIPAY_MODE=real` 且通过复测”再打开。

## 3. 前端需要调用的接口

### 3.1 获取套餐列表

`GET /api/v1/subscription/plans`

是否鉴权：

- 不需要

用途：

- 拉取可购买套餐

示例响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "plans": [
      {
        "id": "plan_monthly_basic",
        "name": "休闲卡",
        "price": 2900,
        "priceDisplay": "29.0",
        "currency": "CNY",
        "period": "monthly",
        "periodLabel": "月",
        "quota": 5,
        "quotaLabel": "5次/月"
      }
    ],
    "subscriberCount": 3
  }
}
```

说明：

- `price` 单位是分
- 前端展示可以优先用 `priceDisplay`

### 3.2 创建支付宝订单

`POST /api/v1/subscription/order?provider=alipay_wap&returnUrl=<页面回跳地址>`

是否鉴权：

- 需要
- `Authorization: Bearer <token>`

Query 参数：

- `provider`
  - H5 填 `alipay_wap`
  - PC 填 `alipay_page`
- `returnUrl`
  - 可选但强烈建议传
  - 支付完成后支付宝会跳回这个页面

Body：

```json
{
  "planId": "plan_monthly_basic",
  "gameId": "optional-game-id"
}
```

字段说明：

- `planId`：必填，套餐 ID
- `gameId`：可选，如果是“解锁某个游戏”型购买可以带上

示例响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "orderId": "order_20260405133018_a0f8a78e",
    "payment": {
      "provider": "alipay",
      "flow": "wap",
      "payUrl": "https://openapi.alipay.com/gateway.do?..."
    }
  }
}
```

字段说明：

- `orderId`：后续查询订单状态要用
- `payment.provider`：固定为 `alipay`
- `payment.flow`
  - H5 时是 `wap`
  - PC 时是 `page`
- `payment.payUrl`
  - 前端直接跳转
  - 不需要再做二次拼接

### 3.3 查询订单状态

`GET /api/v1/subscription/orders/:id`

是否鉴权：

- 需要

用途：

- 支付完成后轮询订单是否变成 `paid`

示例响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "orderId": "order_20260405133018_a0f8a78e",
    "status": "pending",
    "provider": "alipay_wap",
    "planId": "plan_monthly_basic",
    "planName": "休闲卡",
    "amount": 2900,
    "currency": "CNY",
    "gameIdToUnlock": null,
    "paidAt": null,
    "expiresAt": "2026-04-05T15:30:18.338Z",
    "quotaRemaining": 5,
    "subscriptionActive": false
  }
}
```

前端重点关注：

- `status`
- `paidAt`
- `subscriptionActive`

订单状态建议按下面理解：

- `pending`：待支付
- `paid`：已支付成功
- `canceled`：已取消或过期

### 3.4 查询当前订阅状态

`GET /api/v1/subscription/status`

是否鉴权：

- 需要

用途：

- 支付成功后刷新前端会员态、额度和权益

示例响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "active": true,
    "planId": "plan_monthly_basic",
    "planName": "休闲卡",
    "expiresAt": "2026-05-05T13:30:55.000Z",
    "usedThisPeriod": 0,
    "quotaThisPeriod": 5,
    "autoRenew": false
  }
}
```

## 4. 前端标准对接流程

推荐主流程：

1. 调 `GET /subscription/plans`
2. 用户选择套餐
3. 调 `POST /subscription/order?provider=alipay_wap&returnUrl=...`
4. 保存 `orderId`
5. `window.location.href = payment.payUrl`
6. 支付完成后回跳到 `returnUrl`
7. 在回跳页读取本地保存的 `orderId`
8. 轮询 `GET /subscription/orders/:id`
9. 一旦订单变成 `paid`，再调 `GET /subscription/status`
10. 刷新前端会员状态、额度和 UI

## 5. H5 前端接入示例

```ts
type CreateOrderResponse = {
  code: number;
  message: string;
  data: {
    orderId: string;
    payment: {
      provider: 'alipay';
      flow: 'wap' | 'page';
      payUrl: string;
    };
  };
};

export async function createAlipayWapOrder(token: string, planId: string) {
  const returnUrl = encodeURIComponent(`${window.location.origin}/billing/result`);
  const resp = await fetch(
    `/api/v1/subscription/order?provider=alipay_wap&returnUrl=${returnUrl}`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ planId }),
    },
  );

  if (!resp.ok) {
    throw new Error(`create order failed: ${resp.status}`);
  }

  const json = (await resp.json()) as CreateOrderResponse;
  const orderId = json.data.orderId;
  const payUrl = json.data.payment.payUrl;

  localStorage.setItem('pendingSubscriptionOrderId', orderId);
  window.location.href = payUrl;
}
```

回跳页示例：

```ts
export async function waitOrderPaid(token: string) {
  const orderId = localStorage.getItem('pendingSubscriptionOrderId');
  if (!orderId) return false;

  const startedAt = Date.now();
  const timeoutMs = 60_000;

  while (Date.now() - startedAt < timeoutMs) {
    const resp = await fetch(`/api/v1/subscription/orders/${orderId}`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });

    if (resp.ok) {
      const json = await resp.json();
      const status = json?.data?.status;
      if (status === 'paid') {
        await fetch('/api/v1/subscription/status', {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });
        localStorage.removeItem('pendingSubscriptionOrderId');
        return true;
      }

      if (status === 'canceled') {
        return false;
      }
    }

    await new Promise((resolve) => setTimeout(resolve, 3000));
  }

  return false;
}
```

## 6. 推荐的前端交互

### 6.1 H5

H5 推荐：

- 用户点“立即开通”
- 创建订单
- 直接跳 `payment.payUrl`
- 回跳后展示“支付结果确认中”
- 轮询订单状态

### 6.2 PC 网页

PC 推荐：

- `provider=alipay_page`
- 新开页或当前页跳转
- 支付完成后回到 `returnUrl`
- 同样轮询订单状态

## 7. 前端不要做的事

前端不要：

- 直接调用支付宝开放平台 API
- 自己拼签名参数
- 自己调用回调接口 `/api/v1/subscription/alipay/notify`
- 把“支付宝支付成功页跳回”直接等同于“订单已支付”

原因：

- 支付结果最终以**后端订单状态**为准
- 只有后端异步通知和验签通过后，订单才算真正支付成功

## 8. 错误处理建议

### 8.1 创建订单失败

前端提示：

- “创建支付订单失败，请稍后重试”

### 8.2 回跳后订单仍是 pending

前端提示：

- “正在确认支付结果，请稍候”

建议：

- 至少轮询 60 秒
- 每 3 秒轮询一次

### 8.3 订单 canceled

前端提示：

- “订单已取消或已过期，请重新发起支付”

### 8.4 未登录

表现：

- 下单接口返回 `401`

前端处理：

- 先拉起登录，再创建订单

## 9. 当前推荐给前端的实际参数

H5：

```text
POST /api/v1/subscription/order?provider=alipay_wap&returnUrl=https://你的前端域名/billing/result
```

PC：

```text
POST /api/v1/subscription/order?provider=alipay_page&returnUrl=https://你的前端域名/billing/result
```

## 10. 联调完成标准

前端联调完成时，至少要满足：

1. 套餐列表能正常展示
2. 能成功创建支付宝订单
3. 页面能成功跳转到 `payUrl`
4. 回跳后能轮询订单状态
5. 订单变成 `paid` 后，订阅状态能刷新为 `active=true`

## 11. 后端对应代码位置

- 控制器入口：[D:\Project\gamevallies\gamevallies-backend\packages\user-service\src\billing\billing.controller.ts](D:\Project\gamevallies\gamevallies-backend\packages\user-service\src\billing\billing.controller.ts)
- 订单主逻辑：[D:\Project\gamevallies\gamevallies-backend\packages\user-service\src\billing\billing.service.ts](D:\Project\gamevallies\gamevallies-backend\packages\user-service\src\billing\billing.service.ts)
- 支付宝签名与回调解析：[D:\Project\gamevallies\gamevallies-backend\packages\user-service\src\billing\alipay-pay.service.ts](D:\Project\gamevallies\gamevallies-backend\packages\user-service\src\billing\alipay-pay.service.ts)
