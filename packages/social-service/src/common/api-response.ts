export function ok<T>(data: T, message = 'success') {
  return {
    code: 0,
    message,
    data,
  };
}

export function toPage<T>(
  items: T[],
  page: number,
  limit: number,
  total: number,
) {
  return {
    items,
    hasMore: page * limit < total,
    page,
    limit,
    total,
  };
}
