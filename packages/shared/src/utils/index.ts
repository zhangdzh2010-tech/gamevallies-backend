/**
 * Utility Functions for PlayForge
 */

/**
 * Generate URL-friendly slug from title
 * @example
 * generateSlug('Hello World Game') // 'hello-world-game'
 */
export function generateSlug(title: string): string {
  if (!title) return '';
  
  return title
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, '') // Remove special characters
    .replace(/[\s_-]+/g, '-') // Replace spaces and underscores with hyphens
    .replace(/^-+|-+$/g, ''); // Remove leading and trailing hyphens
}

/**
 * Format large numbers in Chinese locale style
 * @example
 * formatNumber(1200) // '1.2万'
 * formatNumber(1500000) // '150万'
 * formatNumber(50) // '50'
 */
export function formatNumber(n: number): string {
  if (n < 10000) {
    return n.toString();
  }

  if (n < 100000000) {
    // 万 (10,000)
    const wanValue = (n / 10000).toFixed(1);
    // Remove trailing .0
    return wanValue.endsWith('.0')
      ? `${Math.floor(n / 10000)}万`
      : `${wanValue}万`;
  }

  // 亿 (100,000,000)
  const yiValue = (n / 100000000).toFixed(1);
  return yiValue.endsWith('.0')
    ? `${Math.floor(n / 100000000)}亿`
    : `${yiValue}亿`;
}

/**
 * Calculate Wilson score for rating
 * Used for ranking games based on likes and dislikes
 * Higher score = better rating with more confidence
 * 
 * @param positive - Number of positive votes/likes
 * @param negative - Number of negative votes/dislikes
 * @returns Wilson score between 0 and 1
 * 
 * @example
 * // Game with 95 likes and 5 dislikes
 * calculateWilsonScore(95, 5) // 0.89...
 * 
 * // Game with 1 like and 0 dislikes
 * calculateWilsonScore(1, 0) // 0.21...
 */
export function calculateWilsonScore(
  positive: number,
  negative: number,
): number {
  // Total votes
  const n = positive + negative;

  // No votes yet, return 0
  if (n === 0) {
    return 0;
  }

  // Proportion of positive votes
  const phat = positive / n;

  // Z-score for 95% confidence interval (1.96)
  const z = 1.96;
  const z_squared = z * z;

  // Wilson score interval (lower bound)
  const numerator =
    phat +
    (z_squared / (2 * n)) -
    z * Math.sqrt((phat * (1 - phat) + (z_squared / (4 * n))) / n);

  const denominator = 1 + z_squared / n;

  const score = numerator / denominator;

  // Clamp between 0 and 1
  return Math.max(0, Math.min(1, score));
}

/**
 * Generate a random ID (useful for temporary IDs before database persistence)
 * @returns Random string ID
 */
export function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Sleep utility for async operations
 * @param ms Milliseconds to sleep
 */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Check if a string is a valid email
 */
export function isValidEmail(email: string): boolean {
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return emailRegex.test(email);
}

/**
 * Check if a string is a valid username
 * (alphanumeric and underscore, 3-20 characters)
 */
export function isValidUsername(username: string): boolean {
  const usernameRegex = /^[a-zA-Z0-9_]{3,20}$/;
  return usernameRegex.test(username);
}

/**
 * Truncate text with ellipsis
 */
export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) {
    return text;
  }
  return text.substring(0, maxLength - 3) + '...';
}
