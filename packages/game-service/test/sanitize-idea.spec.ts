import { sanitizeUserIdea } from '../src/common/sanitize-idea';

// Creation sessions no longer expose AI-expanded prompts. The sanitizer is
// retained for legacy persisted descriptions that may contain old prompt
// scaffolding, echoed ideas, or placeholder label values.

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

  it('strips the 原始想法: prefix', () => {
    expect(sanitizeUserIdea('原始想法: 跳房子游戏')).toBe('跳房子游戏');
  });

  it('strips legacy tail scaffolding where label values are placeholders', () => {
    const raw = [
      '原始想法：打砖块但是砖块会还手',
      '',
      'Game Type: determine based on the original idea',
      'Core Mechanic: describe the main repeated player action',
    ].join('\n');
    const sanitized = sanitizeUserIdea(raw);
    expect(sanitized).not.toMatch(/Game Type/);
    expect(sanitized).not.toMatch(/Core Mechanic/);
    expect(sanitized).not.toMatch(/原始想法/);
    expect(sanitized).toContain('打砖块但是砖块会还手');
  });

  it('preserves structured labels with concrete legacy content', () => {
    const raw = [
      '做一款糖果工厂主题的竖屏手机三消小游戏。',
      '',
      '核心玩法：8×8 糖果棋盘，玩家用手指拖动交换相邻糖果形成三连消除。',
      '胜利条件：每关在限定步数内达到目标分数。',
      '失败条件：步数耗尽仍未达成目标。',
      '画风：糖果色系卡通渲染。',
      '操作方式：单指拖动相邻糖果完成交换。',
      '手机适配：竖屏 9:16 全屏。',
    ].join('\n');
    const sanitized = sanitizeUserIdea(raw);
    expect(sanitized).toContain('核心玩法：8×8 糖果棋盘');
    expect(sanitized).toContain('胜利条件：每关在限定步数内达到目标分数');
    expect(sanitized).toContain('画风：糖果色系卡通渲染');
    expect(sanitized).toContain('手机适配：竖屏 9:16 全屏');
    // All structured lines should survive intact.
    expect(sanitized).toBe(raw);
  });

  it('strips the full legacy prompt template that leaked into sessions', () => {
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
    expect(sanitized).not.toMatch(/Game Type/);
    expect(sanitized).not.toMatch(/Core Mechanic/);
    expect(sanitized).not.toMatch(/请把这条想法整理成/);
    expect(sanitized).not.toMatch(/原始想法/);
    expect(sanitized).not.toMatch(/Visual Direction/);
    expect(sanitized).not.toMatch(/Special Rules/);
    expect(sanitized).toContain('以一个废弃的古堡为背景');
  });

  it('drops Chinese label lines whose values are placeholder directives', () => {
    const raw = [
      '我们做一款消除小游戏。',
      '游戏类型：根据原始想法确定',
      '核心玩法：保留原始想法',
      '整体氛围温馨可爱。',
    ].join('\n');
    const sanitized = sanitizeUserIdea(raw);
    expect(sanitized).toContain('我们做一款消除小游戏');
    expect(sanitized).toContain('整体氛围温馨可爱');
    expect(sanitized).not.toMatch(/根据原始想法/);
    expect(sanitized).not.toMatch(/保留原始想法/);
  });

  it('keeps Chinese label lines that carry real content', () => {
    const raw = [
      '我们做一款消除小游戏。',
      '游戏类型：益智解谜',
      '核心玩法：三消，8x8 棋盘，交换相邻方块形成 3 连消除',
      '整体氛围温馨可爱。',
    ].join('\n');
    const sanitized = sanitizeUserIdea(raw);
    expect(sanitized).toContain('游戏类型：益智解谜');
    expect(sanitized).toContain('核心玩法：三消');
    expect(sanitized).toContain('我们做一款消除小游戏');
    expect(sanitized).toContain('整体氛围温馨可爱');
  });

  it('preserves mid-sentence colons that are not label prefixes', () => {
    const raw =
      'Reward feel: every successful merge triggers a soft chime and a gentle camera zoom.';
    expect(sanitizeUserIdea(raw)).toBe(raw);
  });

  it('is idempotent', () => {
    const raw =
      '原始想法：跳房子游戏\nGame Type: determine based on the original idea\n很轻松很治愈。';
    const once = sanitizeUserIdea(raw);
    const twice = sanitizeUserIdea(once);
    expect(twice).toBe(once);
  });
});
