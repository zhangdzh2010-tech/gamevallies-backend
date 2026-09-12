import {
  inferRequestedOrientationFromText,
  resolveRequestedPlatform,
} from '../src/game/game-runtime.policy';

describe('game runtime platform defaults', () => {
  it('infers landscape for science and desktop briefs', () => {
    expect(inferRequestedOrientationFromText('做一个牛顿第二定律科学演示', '力学')).toBe('landscape');
    expect(inferRequestedOrientationFromText('作品类型：工具。制作计数器', '')).toBe('landscape');
    expect(inferRequestedOrientationFromText('做一个桌面浏览器里的策略游戏', '')).toBe('landscape');
  });

  it('keeps explicit mobile portrait games on the phone path', () => {
    expect(inferRequestedOrientationFromText('做一个太空躲避手机竖屏小游戏', '')).toBe('portrait');
    expect(resolveRequestedPlatform({
      description: '做一个太空躲避手机竖屏小游戏',
      orientation: 'portrait',
    })).toBe('wechat_webview');
  });

  it('selects desktop_web for landscape, science, and Creative Studio PC context', () => {
    expect(resolveRequestedPlatform({
      description: '做一个横屏塔防',
      orientation: 'landscape',
    })).toBe('desktop_web');
    expect(resolveRequestedPlatform({
      description: '作品类型：科学演示。制作单摆实验',
    })).toBe('desktop_web');
    expect(resolveRequestedPlatform({
      description: '请生成桌面浏览器中的可交互创意作品',
      title: 'Creative Studio',
    })).toBe('desktop_web');
  });
});
