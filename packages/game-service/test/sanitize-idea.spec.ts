import { sanitizeUserIdea } from '../src/common/sanitize-idea';

describe('sanitizeUserIdea (H.5.1)', () => {
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
});
