/** Canonical payment env keys and leftover zltokens-pack aliases. */

export const PAYMENT_ENV_ALIASES: Record<string, readonly string[]> = {
  WECHAT_PAY_MERCHANT_ID: ['WECHAT_MCH_ID'],
  WECHAT_PAY_API_V3_KEY: ['WECHAT_API_V3_KEY'],
  WECHAT_PAY_SERIAL_NO: ['WECHAT_CERT_SERIAL'],
  WECHAT_H5_APP_ID: ['WECHAT_APP_ID'],
  WECHAT_MINIAPP_APP_ID: ['WECHAT_APP_ID'],
};

export function firstNonEmpty(
  ...values: Array<string | undefined | null>
): string | undefined {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return undefined;
}

export function applyPaymentEnvAliases(
  env: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
  for (const [canonical, aliases] of Object.entries(PAYMENT_ENV_ALIASES)) {
    if (firstNonEmpty(env[canonical])) {
      continue;
    }

    const aliasValue = firstNonEmpty(...aliases.map((alias) => env[alias]));
    if (aliasValue) {
      env[canonical] = aliasValue;
    }
  }

  return env;
}

export function resolvePaymentEnv(
  getter: (key: string) => string | undefined,
  key: string,
): string | undefined {
  const direct = firstNonEmpty(getter(key));
  if (direct) {
    return direct;
  }

  for (const alias of PAYMENT_ENV_ALIASES[key] || []) {
    const value = firstNonEmpty(getter(alias));
    if (value) {
      return value;
    }
  }

  return undefined;
}
