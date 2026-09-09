// Offline production-artifact QA. Network is blocked; originals are not edited.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const [kind, input, output] = process.argv.slice(2);
const front = path.resolve(__dirname, '../../gamevallies-front');
const sandbox = {};
vm.runInNewContext(fs.readFileSync(path.join(front, 'src/utils/workStorageBridge.js'), 'utf8').replace(/^export /gm, '') + '\nthis.render = withWorkStorage;', sandbox);
const checks = [];
async function check(name, fn) { try { await fn(); checks.push({ name, passed: true }); } catch (e) { checks.push({ name, passed: false, error: e.message }); } }
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, serviceWorkers: 'block' });
    await context.route('**/*', route => route.abort());
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.dismiss());
    await page.setContent('<style>html,body{margin:0}iframe{border:0;width:100vw;height:100vh}</style><iframe sandbox="allow-scripts"></iframe>');
    await page.locator('iframe').evaluate((el, html) => { el.srcdoc = html; }, sandbox.render(fs.readFileSync(input, 'utf8'), {}, 'offline-qa'));
    await page.waitForTimeout(300);
    const frame = page.frames().find(f => f !== page.mainFrame());
    if (kind === 'tea-timer') {
      await check('三种预设计时正确', async () => {
        for (const [seconds, display] of [[120, '02:00'], [180, '03:00'], [90, '01:30']]) {
          await frame.locator(`[data-seconds="${seconds}"]`).click();
          assert.equal(await frame.locator('#timeDisplay').innerText(), display);
        }
      });
      await check('暂停继续及重置有效', async () => {
        await frame.locator('#startPauseBtn').click(); await page.waitForTimeout(1400);
        await frame.locator('#startPauseBtn').click();
        const paused = await frame.locator('#timeDisplay').innerText();
        await page.waitForTimeout(1300); assert.equal(await frame.locator('#timeDisplay').innerText(), paused);
        await frame.locator('#startPauseBtn').click(); await page.waitForTimeout(1200);
        assert.notEqual(await frame.locator('#timeDisplay').innerText(), paused);
        await frame.locator('#resetBtn').click(); assert.equal(await frame.locator('#timeDisplay').innerText(), '01:30');
      });
      await check('恢复渲染后补足真实经过时间', async () => {
        await frame.locator('#startPauseBtn').click(); await page.waitForTimeout(250);
        await frame.evaluate(() => { const end = performance.now() + 3100; while (performance.now() < end) {} });
        await page.waitForTimeout(200);
        const value = await frame.locator('#timeDisplay').innerText();
        const [m, s] = value.split(':').map(Number);
        assert.ok(m * 60 + s <= 87, `渲染停顿约 3 秒后应不超过 01:27，实际 ${value}`);
        await frame.locator('#resetBtn').click();
      });
      await frame.locator('#resetBtn').click();
      await check('30秒倒计时结束且不循环', async () => {
        await frame.locator('#customSeconds').fill('30'); await frame.locator('#applyCustomBtn').click();
        await frame.locator('#startPauseBtn').click(); await page.waitForTimeout(31500);
        assert.equal(await frame.locator('#timeDisplay').innerText(), '出汤');
        assert.equal(await frame.locator('#startPauseBtn').isDisabled(), true);
      });
    } else if (kind === 'lens-room') {
      await frame.evaluate(() => {
        window.__qaArcs = [];
        const p = CanvasRenderingContext2D.prototype;
        const arc = p.arc;
        p.arc = function (...args) { window.__qaArcs.push(args); return arc.apply(this, args); };
      });
      await check('预设与重置同步更新角度和画布', async () => {
        const images = [];
        for (const angle of [15, 30, 60]) {
          await frame.locator(`[data-angle="${angle}"]`).click();
          assert.equal(await frame.locator('#angleValue').innerText(), angle + '°');
          images.push(await frame.locator('canvas').evaluate(c => c.toDataURL()));
        }
        assert.equal(new Set(images).size, 3);
        await frame.locator('#resetBtn').click(); assert.equal(await frame.locator('#angleValue').innerText(), '45°');
      });
      await check('角弧表示法线与光线之间的小角', async () => {
        await frame.evaluate(() => { window.__qaArcs = []; });
        await frame.locator('[data-angle="30"]').click();
        const arcs = await frame.evaluate(() => window.__qaArcs.filter(a => a[2] === 45));
        assert.equal(arcs.length, 2);
        for (const a of arcs) {
          const sweep = ((a[5] ? a[3]-a[4] : a[4]-a[3]) % (2*Math.PI) + 2*Math.PI) % (2*Math.PI);
          assert.ok(Math.abs(sweep - Math.PI/6) < 1e-6, `30°角实际绘制 ${Math.round(sweep*180/Math.PI)}°圆弧`);
        }
      });
      await check('0度预设边界可以通过键盘到达', async () => {
        await frame.locator('#angleSlider').focus(); await page.keyboard.press('Home');
        assert.equal(await frame.locator('#angleValue').innerText(), '0°');
        await frame.locator('#resetBtn').click();
      });
    } else if (kind === 'color-prism') {
      await check('八种预设RGB、HEX和色块同步',async()=>{
        for(const [r,g,b] of [[255,0,0],[0,255,0],[0,0,255],[255,255,255],[0,255,255],[255,0,255],[255,255,0],[0,0,0]]){
          await frame.locator(`[data-r="${r}"][data-g="${g}"][data-b="${b}"]`).click();
          await page.waitForTimeout(400); // Read the settled color after the intentional CSS transition.
          assert.equal(await frame.locator('#rgbValue').innerText(),[r,g,b].join(','));
          assert.equal(await frame.locator('#hexValue').innerText(),'#'+[r,g,b].map(n=>n.toString(16).padStart(2,'0')).join('').toUpperCase());
          assert.equal(await frame.locator('#colorPreview').evaluate(el=>getComputedStyle(el).backgroundColor),`rgb(${r}, ${g}, ${b})`);
        }
      });
      await check('数字越界校正且键盘滑块与重置同步',async()=>{
        await frame.locator('#redInput').fill('300');await frame.locator('#redInput').press('Enter');assert.equal(await frame.locator('#redSlider').inputValue(),'255');
        await frame.locator('#redInput').fill('-2');await frame.locator('#redInput').press('Enter');assert.equal(await frame.locator('#redSlider').inputValue(),'0');
        await frame.locator('#redSlider').focus();await page.keyboard.press('ArrowRight');assert.equal(await frame.locator('#redInput').inputValue(),'1');
        await frame.locator('#resetButton').click();assert.equal(await frame.locator('#hexValue').innerText(),'#FFFFFF');
      });
      await check('合成舞台圆形完整位于画布内',async()=>{
        await frame.evaluate(()=>{window.__qaArcs=[];const p=CanvasRenderingContext2D.prototype,old=p.arc;p.arc=function(...a){window.__qaArcs.push(a);return old.apply(this,a);};});
        await frame.locator('#resetButton').click();
        const arcs=await frame.evaluate(()=>window.__qaArcs);assert.ok(arcs.length>0);
        assert.ok(arcs.every(([x,y,r])=>x-r>=0&&y-r>=0),'合成舞台圆心高度为画布16%，半径达22%，顶部被裁切');
      });
    } else if (kind === 'packing-list') {
      await check('天数场景及覆盖确认有效', async()=>{
        await frame.locator('#daysSelect').selectOption('7');await frame.locator('#sceneSelect').selectOption('beach');
        await frame.locator('#generateBtn').click();assert.equal(await frame.locator('#resetConfirmBox').isVisible(),true);
        await frame.locator('#confirmGenerateBtn').click();
        assert.equal(await frame.locator('.qty-value').first().innerText(),'4');
        assert.ok((await frame.locator('#listContainer').innerText()).includes('泳衣'));
      });
      await check('增减数量和删除更新总计',async()=>{
        const before=Number(await frame.locator('#totalQuantity').innerText());
        await frame.locator('.inc').first().click();assert.equal(Number(await frame.locator('#totalQuantity').innerText()),before+1);
        await frame.locator('.dec').first().click();assert.equal(Number(await frame.locator('#totalQuantity').innerText()),before);
        const count=Number(await frame.locator('#totalItems').innerText());
        await frame.locator('.delete-btn').first().click();assert.equal(Number(await frame.locator('#totalItems').innerText()),count-1);
      });
      await check('筛选不改变清单总数和已装总数',async()=>{
        await frame.locator('.item-check').first().check();
        const count=await frame.locator('#totalItems').innerText();
        await frame.locator('#filterUnpackedBtn').click();
        assert.equal(await frame.locator('#totalItems').innerText(),count);
        assert.equal(await frame.locator('#packedCount').innerText(),'1');
      });
      await frame.locator('#filterAllBtn').click();
      await check('新增物品按纯文本处理',async()=>{
        await frame.locator('#newItemName').fill('<b data-qa-inert="yes">物品</b>');await frame.locator('#addItemBtn').click();
        assert.equal(await frame.locator('[data-qa-inert]').count(),0,'物品名称未经转义被插入innerHTML');
      });
    } else if (kind === 'spring-scale') {
      await check('胡克定律数值与预设正确', async()=>{
        assert.ok((await frame.locator('#xDisplay').innerText()).includes('20.0 cm'));
        await frame.locator('#preset8').click();assert.ok((await frame.locator('#xDisplay').innerText()).includes('40.0 cm'));
        await frame.locator('#kSlider').evaluate(el=>{el.value='40';el.dispatchEvent(new Event('input',{bubbles:true}));});
        assert.ok((await frame.locator('#xDisplay').innerText()).includes('20.0 cm'));
        await frame.locator('#forceSlider').focus();await page.keyboard.press('Home');assert.ok((await frame.locator('#xDisplay').innerText()).includes('0.0 cm'));
        await frame.locator('#resetBtn').click();assert.ok((await frame.locator('#xDisplay').innerText()).includes('20.0 cm'));
      });
      await check('滑块方向键可以调整参数',async()=>{
        await frame.locator('#forceSlider').focus();await page.keyboard.press('ArrowRight');
        assert.equal(await frame.locator('#forceSlider').inputValue(),'4.1');
      });
    } else if (kind === 'reading-marker') {
      await check('中英文数字及非空段落计数正确', async () => {
        await frame.locator('#textInput').fill('你好 world 123\n\n   \n第二段');
        for (const [id, value] of [['chineseCount','5'],['englishWords','1'],['digitCount','3'],['paragraphCount','2']]) assert.equal(await frame.locator('#'+id).innerText(),value);
      });
      await check('HTML按纯文本处理', async () => {
        await frame.locator('#textInput').fill('<img src=x onerror="window.__injected=1">');
        assert.equal(await frame.locator('img').count(),0); assert.equal(await frame.evaluate(()=>window.__injected),undefined);
      });
      await check('清空提供沙箱内可用的确认界面', async () => {
        let dialogs=0;page.on('dialog',()=>dialogs++);await frame.locator('#clearBtn').click();await page.waitForTimeout(100);
        assert.ok(dialogs>0||await frame.locator('dialog[open],[role="dialog"]').count()>0,'confirm被沙箱禁用，清空按钮无效');
      });
      await check('重置后计数归零',async()=>{
        await frame.locator('#resetBtn').click();
        for(const id of ['chineseCount','englishWords','digitCount','totalChars','nonWhitespaceChars','paragraphCount'])assert.equal(await frame.locator('#'+id).innerText(),'0');
      });
    } else if (kind === 'pendulum-gallery') {
      await check('地球1米周期约2.01秒，四倍摆长周期翻倍', async () => {
        await frame.locator('#earthPreset').click(); assert.equal(await frame.locator('#periodDisplay').innerText(), 'T = 2.01 s');
        await frame.locator('#lengthSlider').focus(); await page.keyboard.press('End');
        assert.equal(await frame.locator('#periodDisplay').innerText(), 'T = 4.01 s');
        await frame.locator('#moonPreset').click(); assert.equal(await frame.locator('#periodDisplay').innerText(), 'T = 4.94 s');
      });
      await check('开始后摆球移动，暂停冻结，重置恢复', async () => {
        await frame.locator('#resetBtn').click();
        const initial = await frame.locator('canvas').evaluate(c => c.toDataURL());
        await frame.locator('#startBtn').click(); await page.waitForTimeout(500);
        assert.notEqual(await frame.locator('canvas').evaluate(c => c.toDataURL()), initial);
        await frame.locator('#pauseBtn').click();
        const paused = await frame.locator('canvas').evaluate(c => c.toDataURL());
        await page.waitForTimeout(300); assert.equal(await frame.locator('canvas').evaluate(c => c.toDataURL()), paused);
        await frame.locator('#resetBtn').click(); assert.equal(await frame.locator('canvas').evaluate(c => c.toDataURL()), initial);
      });
    } else if (kind === 'ratio-desk') {
      await check('16:9宽1920得到高1080', async () => {
        await frame.locator('[data-ratio="16:9"]').click(); await frame.locator('#base-width-btn').click();
        await frame.locator('#base-value-input').fill('1920'); await frame.locator('#compute-btn').click();
        assert.equal(await frame.locator('#display-width').innerText(), '1920'); assert.equal(await frame.locator('#display-height').innerText(), '1080');
      });
      await check('9:16高1920得到宽1080', async () => {
        await frame.locator('[data-ratio="9:16"]').click(); await frame.locator('#base-height-btn').click();
        assert.equal(await frame.locator('#display-width').innerText(), '1080'); assert.equal(await frame.locator('#display-height').innerText(), '1920');
      });
      await check('零负数空值被拒绝', async () => {
        for (const value of ['0', '-1', '']) {
          await frame.locator('#base-value-input').fill(value); await frame.locator('#compute-btn').click();
          assert.equal(await frame.locator('#display-width').innerText(), '—');
          assert.ok((await frame.locator('#error-summary').innerText()).length > 0);
        }
      });
      await check('交换比例与计算同步', async () => {
        await frame.locator('#base-value-input').fill('1920'); await frame.locator('#swap-btn').click();
        assert.equal(await frame.locator('#ratio-width-input').inputValue(), '16');
        assert.equal(await frame.locator('#ratio-height-input').inputValue(), '9');
        assert.equal(await frame.locator('#display-width').innerText(), '3413');
      });
      await check('重置恢复初次显示的1920×1080', async () => {
        await frame.locator('#reset-btn').click();
        assert.equal(await frame.locator('#display-width').innerText(), '1920'); assert.equal(await frame.locator('#display-height').innerText(), '1080');
      });
    } else if (kind === 'wave-duet') {
      await frame.locator('#startPauseBtn').click();
      await check('0、90、180度合成振幅正确', async () => {
        for (const [phase, value] of [[0, '2.000'], [90, '1.414'], [180, '0.000']]) {
          await frame.locator('#preset' + phase).click();
          assert.ok((await frame.locator('#compositeAmpDisplay').innerText()).includes(value));
        }
      });
      await check('暂停冻结画面且恢复后继续运动', async () => {
        await frame.locator('#preset90').click();
        const before = await frame.locator('canvas').evaluate(c => c.toDataURL());
        await page.waitForTimeout(250); assert.equal(await frame.locator('canvas').evaluate(c => c.toDataURL()), before);
        await frame.locator('#startPauseBtn').click(); await page.waitForTimeout(250);
        assert.notEqual(await frame.locator('canvas').evaluate(c => c.toDataURL()), before);
        await frame.locator('#startPauseBtn').click();
      });
      await check('最大振幅时合成曲线不被画布裁掉', async () => {
        await frame.evaluate(() => {
          window.__qaPoints = []; const p = CanvasRenderingContext2D.prototype;
          const old = p.lineTo; p.lineTo = function(x, y) { window.__qaPoints.push([x, y]); return old.call(this, x, y); };
        });
        await frame.locator('#ampSlider').focus(); await page.keyboard.press('End');
        await frame.evaluate(() => { window.__qaPoints = []; }); await frame.locator('#preset0').click();
        const info = await frame.evaluate(() => ({ points: window.__qaPoints, height: document.querySelector('canvas').height }));
        assert.ok(info.points.every(([x, y]) => y >= 0 && y <= info.height), 'A=2、同相合成振幅4m超出固定±2.8m坐标范围，曲线被截断');
      });
    } else if (kind === 'week-plan') {
      const count = async () => Number(await frame.locator('#totalCount').innerText());
      await check('空白事项不能添加', async () => {
        const before = await count(); await frame.locator('#taskInput').fill('   '); await frame.locator('#addBtn').click(); assert.equal(await count(), before);
      });
      await check('新增完成筛选删除保持数量一致且不执行HTML', async () => {
        const before = await count();
        const text = '<img src=x onerror="window.__injected=1">';
        await frame.locator('#taskInput').fill(text); await frame.locator('#addBtn').click();
        assert.equal(await count(), before + 1);
        const item = frame.locator('.task-item').filter({ hasText: text });
        assert.equal(await item.locator('img').count(), 0);
        assert.equal(await frame.evaluate(() => window.__injected), undefined);
        const completed = Number(await frame.locator('#completedCount').innerText());
        await item.locator('.complete-btn').click(); assert.equal(Number(await frame.locator('#completedCount').innerText()), completed + 1);
        await frame.locator('[data-filter="uncompleted"]').click(); assert.equal(await item.count(), 0);
        await frame.locator('[data-filter="all"]').click(); await item.locator('.delete-btn').click(); assert.equal(await count(), before);
      });
      await check('编辑在正式作品沙箱中可交互', async () => {
        let dialogs = 0; page.on('dialog', () => dialogs++);
        await frame.locator('.edit-btn').first().click(); await page.waitForTimeout(100);
        assert.ok(dialogs > 0 || await frame.locator('dialog[open], [role="dialog"]').count() > 0, '原生 prompt 被 allow-scripts 沙箱禁止，点击编辑没有输入界面');
      });
      await check('清空操作在沙箱内有可操作确认', async () => {
        let dialogs = 0; page.on('dialog', () => dialogs++);
        await frame.locator('#clearAllBtn').click(); await page.waitForTimeout(100);
        assert.ok(dialogs > 0 || await frame.locator('dialog[open], [role="dialog"]').count() > 0, '原生 confirm 被沙箱禁止，清空无法执行');
      });
    } else { checks.push({ name: '核心交互验收', passed: false, error: '尚未实现此作品的专属断言，不计为通过' }); }
    await check('无浏览器运行错误', () => assert.deepEqual(errors, []));
    fs.mkdirSync(output, { recursive: true });
    await page.screenshot({ path: path.join(output, 'interactions.png') });
    fs.writeFileSync(path.join(output, 'interactions.json'), JSON.stringify({ kind, checks, errors }, null, 2));
    console.log(JSON.stringify({ kind, checks, errors }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
