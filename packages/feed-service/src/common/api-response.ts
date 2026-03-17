export function ok<T>(data: T, message = 'success') {
  return {
    code: 0,
    message,
    data,
  };
}

// 重载: 兼容 feed 和 social 两种调用方式
export function toPage<T>(result: {
  data: T[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    pages?: number;
    totalPages?: number;
  };
}): { items: T[]; hasMore: boolean; page: number; limit: number; total: number };

export function toPage<T>(
  items: T[],
  page: number,
  limit: number,
  total: number,
): { items: T[]; hasMore: boolean; page: number; limit: number; total: number };

export function toPage<T>(...args: any[]): {
  items: T[];
  hasMore: boolean;
  page: number;
  limit: number;
  total: number;
} {
  if (args.length === 1) {
    // feed-service 风格: toPage({ data, pagination })
    const result = args[0];
    const totalPages =
      result.pagination.totalPages ??
      result.pagination.pages ??
      Math.ceil(result.pagination.total / Math.max(result.pagination.limit, 1));
    return {
      items: result.data,
      hasMore: result.pagination.page < totalPages,
      page: result.pagination.page,
      limit: result.pagination.limit,
      total: result.pagination.total,
    };
  }
  // social-service 风格: toPage(items, page, limit, total)
  const [items, page, limit, total] = args;
  return {
    items,
    hasMore: page * limit < total,
    page,
    limit,
    total,
  };
}
