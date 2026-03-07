export function ok<T>(data: T, message = 'success') {
  return {
    code: 0,
    message,
    data,
  };
}

export function toPage<T>(result: {
  data: T[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    totalPages?: number;
    pages?: number;
  };
}) {
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
