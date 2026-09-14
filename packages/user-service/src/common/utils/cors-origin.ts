/** Parse CORS_ORIGIN / CORS_ORIGINS into the value Nest `enableCors` expects. */

export function parseCorsOriginList(value?: string | null): string[] {
  if (!value) {
    return [];
  }

  const trimmed = value.trim();
  if (!trimmed) {
    return [];
  }

  if (trimmed.startsWith('[')) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      if (Array.isArray(parsed)) {
        return parsed.map((item) => String(item).trim()).filter(Boolean);
      }
    } catch {
      // Fall through to comma-separated parsing.
    }
  }

  return trimmed.split(',').map((item) => item.trim()).filter(Boolean);
}

export function resolveCorsOrigin(
  corsOrigin = process.env.CORS_ORIGIN,
  corsOrigins = process.env.CORS_ORIGINS,
): string | string[] {
  const unique: string[] = [];
  for (const origin of [
    ...parseCorsOriginList(corsOrigins),
    ...parseCorsOriginList(corsOrigin),
  ]) {
    if (!unique.includes(origin)) {
      unique.push(origin);
    }
  }

  if (unique.length === 0 || unique.includes('*')) {
    return '*';
  }

  return unique.length === 1 ? unique[0] : unique;
}

export function allowedBrowserOrigins(
  extra: Array<string | undefined | null> = [],
): string[] {
  const origins: string[] = [];
  for (const value of [
    ...parseCorsOriginList(process.env.CORS_ORIGINS),
    ...parseCorsOriginList(process.env.CORS_ORIGIN),
    ...extra,
  ]) {
    if (!value || value === '*') {
      continue;
    }

    try {
      const origin = new URL(value).origin;
      if (!origins.includes(origin)) {
        origins.push(origin);
      }
    } catch {
      // Ignore invalid entries; callers still have a canonical fallback.
    }
  }

  return origins;
}
