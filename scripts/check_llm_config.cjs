// Real admin assets with synthetic API fixtures. No production requests.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const assets = path.resolve(__dirname, '../packages/game-service/src/admin');
  const output = path.resolve(__dirname, '../tmp/llm-config-qa');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const html = fs.readFileSync(path.join(assets, 'admin-panel.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
    await page.route('**/*', route => route.request().url() === 'http://llm-admin.test/' ? route.fulfill({ contentType: 'text/html', body: html }) : route.abort());
    await page.goto('http://llm-admin.test/');
    await page.addStyleTag({ content: fs.readFileSync(path.join(assets, 'admin-panel.css'), 'utf8') });
    for (const name of ['state', 'core', 'ops', 'llm']) await page.addScriptTag({ content: fs.readFileSync(path.join(assets, `admin-panel-${name}.js`), 'utf8') });
    await page.evaluate(async () => {
      document.getElementById('loginOverlay').style.display = 'none'; document.getElementById('app').style.display = 'block';
      document.querySelectorAll('.page').forEach(node => node.classList.remove('active')); document.getElementById('page-llm').classList.add('active');
      const provider = { id: 'provider', name: '创作服务', providerType: 'openai_compatible', baseUrl: 'https://provider.example/v1', enabled: true, apiKeySet: true, region: 'cn_shanghai', regionTargetId: 'target', updatedAt: '2026-09-09T00:00:00.000Z' };
      window.fixture = {
        providers: [provider], models: ['a', 'b'].map((id, index) => ({ id, providerId: provider.id, provider, name: index ? '备用模型' : '创作模型', modelId: index ? 'backup-pro' : 'creative-pro', enabled: true, configurationVersion: 1, capabilityFlags: {}, latestTest: null })),
        stages: [['generate', '作品生成'], ['modify', '作品修改'], ['assist', '辅助处理']].map(([id, name]) => ({ id, name, description: '内部步骤由系统统一映射', status: 'legacy', binding: null, legacy: [{ stepKey: 'legacy.step', model: 'original-model' }] })),
        regions: [{ id: 'target', executionRegion: 'cn_shanghai', displayName: '中国区域' }],
      };
      window.calls = []; window.messages = []; window.testResolvers = {};
      toast = message => window.messages.push(message);
      confirmDialog = async () => false;
      api = async (url, options = {}) => {
        window.calls.push({ url, options });
        if (url.endsWith('/test')) return new Promise(resolve => { window.testResolvers[url.split('/')[3]] = resolve; });
        if (url === '/llm/providers' && !options.method) return structuredClone(window.fixture.providers);
        if (url === '/llm/models' && !options.method) return structuredClone(window.fixture.models);
        if (url.startsWith('/llm/business-bindings?')) return structuredClone(window.fixture.stages);
        if (url.startsWith('/cloud/')) return window.fixture.regions;
        if (url.startsWith('/llm/models/') && options.method === 'PUT') {
          const id = url.split('/')[3]; const model = window.fixture.models.find(row => row.id === id);
          Object.assign(model, options.body, { configurationVersion: model.configurationVersion + 1, latestTest: null });
          return { model };
        }
        if (url === '/llm/business-bindings' && options.method === 'PUT') {
          window.fixture.stages.forEach(stage => { stage.status = 'configured'; stage.legacy = []; stage.binding = { ...options.body.bindings.find(row => row.stage === stage.id), revision: 1 }; });
          return { saved: true };
        }
        throw new Error('Unexpected request ' + url);
      };
      await loadLlmGateway();
    });
    assert.equal(await page.getByRole('tab').count(), 3);
    await page.getByRole('button', { name: '添加 Provider', exact: true }).click();
    assert.equal(await page.locator('#llm3Form input:visible').count(), 4); // name, URL, Key, enabled
    assert.equal(await page.locator('#llm3Form select:visible').count(), 1); // protocol, no model
    assert.doesNotMatch(await page.locator('#llm3Form').innerText(), /主模型|快模型|能力标注/);
    await page.evaluate(() => closeLlmConfigSheet());
    await page.screenshot({ path: path.join(output, 'providers.png'), fullPage: true });
    await page.getByRole('tab', { name: '2. Model 配置与测试' }).click();
    await page.screenshot({ path: path.join(output, 'models.png'), fullPage: true });
    // Tests are per model, even on one provider, and duplicate clicks are ignored.
    await page.evaluate(() => { window.pendingA = testLlmModel('a'); window.pendingB = testLlmModel('b'); testLlmModel('a'); });
    assert.equal(await page.evaluate(() => window.calls.filter(call => call.url.endsWith('/test')).length), 2);
    assert.equal(await page.evaluate(() => window.calls.filter(call => call.url.endsWith('/test')).every(call => call.options.retry === false)), true);
    await page.evaluate(() => { openLlmModel('a'); window.originalApp = document.getElementById('llm3App'); window.originalScroll = window.scrollY; });
    await page.locator('#llm3Name').fill('未保存的模型名称');
    await page.evaluate(() => {
      window.testResolvers.a({ success: true, model: 'creative-pro', modelConfigVersion: 1, providerUpdatedAt: window.fixture.providers[0].updatedAt, latencyMs: 100, testedAt: '2026-09-09T00:01:00Z' });
      window.testResolvers.b({ success: false, model: 'backup-pro', modelConfigVersion: 1, providerUpdatedAt: window.fixture.providers[0].updatedAt, latencyMs: 60000, httpStatus: 504, testedAt: '2026-09-09T00:01:00Z' });
    });
    await page.evaluate(() => Promise.all([window.pendingA, window.pendingB]));
    assert.equal(await page.locator('#llm3Name').inputValue(), '未保存的模型名称');
    assert.equal(await page.evaluate(() => document.getElementById('llm3App') === window.originalApp && window.scrollY === window.originalScroll), true);
    await page.evaluate(() => closeLlmConfigSheet());
    assert.match(await page.locator('#llm3Test-a').innerText(), /连通测试成功/);
    assert.match(await page.locator('#llm3Test-b').innerText(), /504/);
    // Editing while a test is in flight invalidates that old result.
    await page.evaluate(() => { window.pendingA = testLlmModel('a'); openLlmModel('a'); });
    await page.locator('#llm3Name').fill('更新后的模型');
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.waitForFunction(() => !document.getElementById('llm3Sheet').classList.contains('active'));
    await page.evaluate(() => window.testResolvers.a({ success: true, modelConfigVersion: 1 }));
    await page.evaluate(() => window.pendingA);
    assert.equal(await page.evaluate(() => llmConfigModels.find(model => model.id === 'a').latestTest), null);
    await page.getByRole('tab', { name: '3. 业务环节配置' }).click();
    assert.equal(await page.locator('.llm-business-cards article').count(), 3);
    assert.equal(await page.locator('.llm-business-cards select').count(), 6);
    for (const stage of ['generate', 'modify', 'assist']) {
      await page.locator('#llm3Primary-' + stage).selectOption('a');
      await page.locator('#llm3Fallback-' + stage).selectOption('b');
    }
    await page.getByRole('button', { name: '保存业务配置' }).click();
    assert.equal(await page.evaluate(() => window.calls.filter(call => call.url === '/llm/business-bindings').length), 0);
    await page.evaluate(() => { confirmDialog = async () => true; });
    await page.getByRole('button', { name: '保存业务配置' }).click();
    await page.waitForFunction(() => llmConfigStages.every(stage => stage.status === 'configured'));
    assert.equal(await page.evaluate(() => window.calls.filter(call => call.url === '/llm/business-bindings').length), 1);
    await page.screenshot({ path: path.join(output, 'business.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log('PASS: three modules; connection-only form; per-model tests and dedup; drafts preserved; stale results invalidated; three stages/same-provider backup; legacy confirmation; single write; mobile layout');
    console.log(output);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
