import { sanitizeUserIdea } from '../src/common/sanitize-idea';

describe('sanitizeUserIdea', () => {
  it('returns empty string for null / undefined', () => {
    expect(sanitizeUserIdea(null)).toBe('');
    expect(sanitizeUserIdea(undefined)).toBe('');
  });

  it('is a no-op for clean user ideas', () => {
    expect(sanitizeUserIdea('一只猫在跳房子')).toBe('一只猫在跳房子');
  });

  it('strips the 请把这条想法整理成... instruction tail', () => {
    const raw = '一只猫在跳房子\n请把这条想法整理成 GameSpec';
    expect(sanitizeUserIdea(raw)).toBe('一只猫在跳房子');
  });

  it('strips English spec-field scaffolding from the tail', () => {
    const raw =
      '一只猫在跳房子\nGame Type: casual\nCore Mechanic: tap to jump';
    expect(sanitizeUserIdea(raw)).toBe('一只猫在跳房子');
  });

  it('strips the 原始想法: prefix', () => {
    expect(sanitizeUserIdea('原始想法: 跳房子游戏')).toBe('跳房子游戏');
  });

  it('strips combined prefix + tail', () => {
    const raw =
      '用户想法：打砖块但是砖块会还手\nGame Type: arcade\nWin Condition: survive 60s';
    expect(sanitizeUserIdea(raw)).toBe('打砖块但是砖块会还手');
  });

  it('strips the full legacy expand-prompt template that leaked into sessions', () => {
    const raw = [
      '原始想法：以一个废弃的古堡为背景，内部藏着各种厉鬼',
      '',
      '请把这条想法整理成一个适合移动端小游戏生成的确认稿，并至少覆盖这些要素：',
      'Game Type: 根据原始想法确定游戏方向',
      'Core Mechanic: 提炼玩家最常执行的核心动作',
      'Theme: 保留原始想法里的题材、场景或情绪',
      'Input Method: 采用适合手机的点击、滑动或拖拽操作',
      'Win Condition: 明确玩家这一局如何过关或获胜',
      'Difficulty Ramp: 说明难度如何逐步提升',
      'Scoring / Rewards: 补充积分、连击、奖励或解锁节奏',
      'Visual Direction: 给出匹配题材的视觉风格',
      'Special Rules or Reference Inspiration: 仅在确有帮助时补充',
    ].join('\n');
    const sanitized = sanitizeUserIdea(raw);
    // The user's idea is kept as prose (H.5.1 legacy behavior handles the
    // single-string case where the first line is `原始想法：<idea>`), but
    // every engineering-language line must be stripped.
    expect(sanitized).not.toMatch(/Game Type/);
    expect(sanitized).not.toMatch(/Core Mechanic/);
    expect(sanitized).not.toMatch(/请把这条想法整理成/);
    expect(sanitized).not.toMatch(/原始想法/);
    expect(sanitized).not.toMatch(/Visual Direction/);
    expect(sanitized).not.toMatch(/Special Rules/);
    expect(sanitized).toContain('以一个废弃的古堡为背景');
  });

  it('drops Chinese label lines interleaved with real prose', () => {
    const raw = [
      '我们做一款消除小游戏。',
      '游戏类型：益智解谜',
      '核心玩法：三消',
      '整体氛围温馨可爱。',
    ].join('\n');
    expect(sanitizeUserIdea(raw)).toContain('我们做一款消除小游戏');
    expect(sanitizeUserIdea(raw)).toContain('整体氛围温馨可爱');
    expect(sanitizeUserIdea(raw)).not.toMatch(/游戏类型/);
    expect(sanitizeUserIdea(raw)).not.toMatch(/核心玩法/);
  });

  it('preserves mid-sentence colons that are not label prefixes', () => {
    const raw =
      'Reward feel: every successful merge triggers a soft chime and a gentle camera zoom.';
    expect(sanitizeUserIdea(raw)).toBe(raw);
  });

  it('is idempotent', () => {
    const raw = '原始想法：跳房子游戏\nGame Type: casual\n很轻松很治愈。';
    const once = sanitizeUserIdea(raw);
    const twice = sanitizeUserIdea(once);
    expect(twice).toBe(once);
  });
});
