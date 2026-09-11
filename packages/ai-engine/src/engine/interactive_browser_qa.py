"""Execute interactive candidates inside the same restricted frame as the player.

This is a deterministic smoke gate, not proof of domain correctness. Keep the
structured semantic review and work-specific acceptance tests in addition to it.
"""
from __future__ import annotations

import re

from .runtime_isolation import network_policy_meta, restrict_context_network
from .rendered_text_evidence import CANVAS_TEXT_PROBE
from .interactive_contract_probes import run_input_contract_probes

# Same per-work storage semantics as the player's workStorageBridge.js. Data is
# ephemeral in QA; neither generated code nor the adapter can read host storage.
STORAGE_BOOTSTRAP = r"""<script>(function(){
function storage(){let data=Object.create(null);return {
 get length(){return Object.keys(data).length},key:i=>Object.keys(data)[i]||null,
 getItem:k=>Object.prototype.hasOwnProperty.call(data,String(k))?data[String(k)]:null,
 setItem(k,v){k=String(k);v=String(v);const next=Object.assign(Object.create(null),data);next[k]=v;
 if(k.length>1024||Object.keys(next).length>200||JSON.stringify(next).length>262144)
 throw new DOMException('Work storage quota exceeded','QuotaExceededError');data=next;},
 removeItem:k=>{delete data[String(k)]},clear:()=>{data=Object.create(null)}
};}
Object.defineProperty(window,'localStorage',{value:storage(),configurable:false});
Object.defineProperty(window,'sessionStorage',{value:storage(),configurable:false});
})();</script>"""

# Observe actual stroked optical angle arcs, not source comments or model claims.
# Only flag a complementary major arc when its *minor* angle exactly matches a
# rendered incident/reflection label. Full circles, gauges, unlabeled art and
# unrelated angles are not treated as failed science.
ANGLE_PROBE = r"""<script>(function(){
const p=CanvasRenderingContext2D.prototype,frames=new Map(),paths=new WeakMap();
const begin=p.beginPath,arc=p.arc,stroke=p.stroke,clear=p.clearRect,fillText=p.fillText,strokeText=p.strokeText;
function frame(c){if(!frames.has(c)&&frames.size<20)frames.set(c,{arcs:[],labels:[]});return frames.get(c);}
p.beginPath=function(){paths.set(this,[]);return begin.apply(this,arguments);};
p.arc=function(x,y,r,start,end,ccw){const a=paths.get(this);if(a&&a.length<20)a.push({start,end,ccw:!!ccw});return arc.apply(this,arguments);};
p.stroke=function(){const f=frame(this);if(f&&arguments.length===0)f.arcs.push(...(paths.get(this)||[]));if(f)f.arcs=f.arcs.slice(-40);return stroke.apply(this,arguments);};
p.clearRect=function(x,y,w,h){if(x===0&&y===0){const f=frame(this);if(f){f.arcs=[];f.labels=[];}}return clear.apply(this,arguments);};
function label(c,t){const f=frame(c),m=String(t).match(/(?:入射角|反射角)\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*°/);
 if(f&&m)f.labels.push({text:String(t).slice(0,80),degrees:Number(m[1])});if(f)f.labels=f.labels.slice(-20);}
p.fillText=function(t){label(this,t);return fillText.apply(this,arguments);};
p.strokeText=function(t){label(this,t);return strokeText.apply(this,arguments);};
Object.defineProperty(window,'__workAngleEvidence',{value:()=>{
 const evidence=[];for(const [c,f] of frames){for(const a of f.arcs){const tau=2*Math.PI;
 const raw=a.ccw?a.start-a.end:a.end-a.start;
 const sweep=raw>=tau?360:((raw%tau+tau)%tau)*180/Math.PI;
 const minor=Math.min(sweep,360-sweep);
 if(sweep<=180||sweep>=359.99)continue;
 const text=f.labels.find(l=>l.degrees>0&&l.degrees<90&&Math.abs(l.degrees-minor)<.1);
 if(text)evidence.push({type:'optical_angle_major_arc',canvas:c.canvas.id,label:text.text,
   expectedDegrees:text.degrees,actualSweepDegrees:Math.round(sweep*1000)/1000,...a});
 }}return evidence.slice(0,10);}});
})();</script>"""

CONTROLS = 'button,input[type=range],input[type=number],input[type=text],input:not([type]),textarea,select'
DRAWING_PROBE = r"""<script>(function(){
const reports=[],paths=new WeakMap(),moves=new WeakMap(),p=CanvasRenderingContext2D.prototype;
Object.defineProperty(window,'__workDrawingIssues',{value:reports});
const begin=p.beginPath,move=p.moveTo,line=p.lineTo,stroke=p.stroke;
p.beginPath=function(){paths.set(this,[]);moves.set(this,0);return begin.apply(this,arguments)};
function record(ctx,x,y){const a=paths.get(ctx);if(!a||a.length>=4096)return;
 const t=ctx.getTransform();a.push([t.a*x+t.c*y+t.e,t.b*x+t.d*y+t.f]);}
p.moveTo=function(x,y){moves.set(this,(moves.get(this)||0)+1);record(this,x,y);return move.apply(this,arguments)};
p.lineTo=function(x,y){record(this,x,y);return line.apply(this,arguments)};
p.stroke=function(){const a=paths.get(this)||[],w=this.canvas.width,h=this.canvas.height;
 if(arguments.length===0&&(moves.get(this)||0)<=1&&a.length>=32&&w>0&&h>0){const xs=a.map(v=>v[0]),ys=a.map(v=>v[1]);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  // A sampled curve can have every center point in bounds while its stroke
  // is still cut off at an interior peak. Exclude monotonic axes and borders.
  const t=this.getTransform(),strokeRadiusY=this.lineWidth*Math.hypot(t.b,t.d)/2;
  const interiorPeak=ys.slice(1,-1).some((y,i)=>(y<ys[i]&&y<ys[i+2]&&y-strokeRadiusY<-.25)
    ||(y>ys[i]&&y>ys[i+2]&&y+strokeRadiusY>h+.25));
  const strokeClipped=interiorPeak&&maxY-minY>h*.5;
  if(minX>=-4&&maxX<=w+4&&maxX-minX>w*.5&&(minY < -4||maxY>h+4||strokeClipped)&&reports.length<10)
   reports.push({type:'plot_curve_clipped',canvas:this.canvas.id,minY,maxY,height:h,strokeRadiusY,strokeClipped});
 }return stroke.apply(this,arguments);};
})();</script>"""
OUTPUTS = """() => Array.from(document.querySelectorAll('output,[data-work-output],[id*="display" i],[id*="result" i]'))
 .filter(e=>!e.children.length && e.textContent.trim() && !/time|clock|timer|countdown/i.test(e.id))
 .map(e=>({key:e.id||e.tagName+':'+Array.from(document.querySelectorAll('output')).indexOf(e),text:e.textContent.trim()}))"""
SIGNATURE = """() => document.body.innerText
 + Array.from(document.querySelectorAll('canvas')).map(c=>c.toDataURL()).join('')
 + Array.from(document.querySelectorAll('svg')).map(s=>s.outerHTML).join('')
 + document.querySelectorAll('input,textarea,select,dialog[open],[role=dialog]').length"""

LAYOUT = """() => {
 const viewport={width:innerWidth,height:innerHeight};
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
   return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';};
 const secondary=e=>!!e.closest('[data-work-secondary],details:not([open])');
 const innerScroller=e=>{for(let p=e.parentElement;p&&p!==document.body&&p!==document.documentElement;p=p.parentElement){
   const s=getComputedStyle(p),r=p.getBoundingClientRect();
   if(/auto|scroll/.test(s.overflowY)&&p.scrollHeight>p.clientHeight+2
     &&r.top>=0&&r.bottom<=innerHeight+2)return true;
 }return false;};
 const outside=[];
 for(const e of document.querySelectorAll('canvas,svg,button,input:not([type=hidden]),textarea,select')){
   if(!visible(e)||secondary(e)||innerScroller(e))continue;
   const r=e.getBoundingClientRect();
   if(r.top < -2 || r.left < -2 || r.bottom>innerHeight+2 || r.right>innerWidth+2)
     outside.push({tag:e.tagName,id:e.id,label:(e.getAttribute('aria-label')||e.textContent||e.type||'').trim().slice(0,60),
       top:Math.round(r.top),bottom:Math.round(r.bottom)});
 }
 return {...viewport,horizontalOverflow:document.documentElement.scrollWidth>innerWidth+2,
   coreOutsideViewport:outside.slice(0,20),coreOutsideCount:outside.length,
   canvasSizes:Array.from(document.querySelectorAll('canvas')).filter(visible).map(c=>({width:c.width,height:c.height}))};
}"""


async def load_work(host, code: str, *, hidden: bool = False):
    secured = re.sub(r'(<head\b[^>]*>)', lambda m: m[1] + network_policy_meta(), code, count=1, flags=re.I)
    if secured == code:
        secured = network_policy_meta() + code
    # Storage is installed before any candidate code, including pre-head scripts.
    secured = re.sub(r'^(\s*<!doctype[^>]*>)?', lambda m: m[0] + STORAGE_BOOTSTRAP + DRAWING_PROBE + ANGLE_PROBE + CANVAS_TEXT_PROBE, secured, count=1, flags=re.I)
    await host.set_content('<style>html,body{margin:0;width:100%;height:100%}iframe{border:0;width:100%;height:100%}</style><iframe sandbox="allow-scripts"></iframe>')
    frame_element = host.locator('iframe')
    await frame_element.evaluate('(el, hidden)=>{el.style.display=hidden?"none":"block"}', hidden)
    await frame_element.evaluate('(el, html)=>{el.srcdoc=html}', secured)
    await host.wait_for_timeout(150)
    if hidden:
        await frame_element.evaluate('el=>{el.style.display="block"}')
        await host.wait_for_timeout(150)
    frame = host.frames[-1]
    await frame.wait_for_selector('body', state='attached', timeout=3000)
    return frame


async def browser_report(code: str) -> dict:
    from playwright.async_api import async_playwright
    issues, js_errors, sandbox_errors, layout_issues = [], [], [], []
    def layout_issue(message):
        issues.append(message)
        layout_issues.append(message)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            # Playwright 1.43's service_workers='block' injects an unguarded
            # navigator.serviceWorker.register override into opaque frames,
            # producing its own TypeError. Opaque srcdoc cannot register a SW;
            # CSP worker-src 'none' and route denial remain enforced as well.
            context = await browser.new_context(viewport={'width':1440,'height':900})
            await restrict_context_network(context)
            host = await context.new_page()
            host.on('pageerror', lambda error: js_errors.append(str(error)[:500]))

            def on_console(message):
                if re.search(r'Ignored call to.*(?:alert|prompt|confirm)|allow-modals', message.text, re.I):
                    sandbox_errors.append('正式沙箱禁止原生 alert/prompt/confirm，请使用页面内确认或编辑界面。')
            host.on('console', on_console)
            viewports, angle_evidence, text_evidence = [], [], []
            for width, height in [(1000,460),(1000,600),(1366,768),(1920,1080)]:
                await host.set_viewport_size({'width':width,'height':height})
                frame = await load_work(host, code)
                layout = await frame.evaluate(LAYOUT)
                frame = await load_work(host, code, hidden=True)
                layout['hiddenReveal'] = await frame.evaluate(LAYOUT)
                angle_evidence.extend(await frame.evaluate('()=>window.__workAngleEvidence()'))
                if height >= 600:
                    text_evidence.extend(await frame.evaluate('()=>window.__workTextEvidence()'))
                viewports.append(layout)
                for mode, result in [('visible', layout), ('hidden-reveal', layout['hiddenReveal'])]:
                    if result['horizontalOverflow']:
                        layout_issue(f'{width}×{height} ({mode}) 桌面视口出现横向溢出。')
                    # 460px is diagnostic; the product acceptance contract is 600px.
                    if width == 1000 and height == 600 and result['coreOutsideCount']:
                        labels = ', '.join(x['id'] or x['label'] or x['tag'] for x in result['coreOutsideViewport'][:6])
                        layout_issue(f'1000×600 ({mode}) 核心图形/控件不完整：{labels}。压缩主布局；长列表可在有界容器内滚动，次要说明可折叠。')
                    if any(c['width'] <= 0 or c['height'] <= 0 for c in result['canvasSizes']):
                        issues.append(f'{width}×{height} ({mode}) 可见Canvas像素尺寸为零。')
                if (width, height) == (1000, 600):
                    # Linux production and macOS/Windows fallback fonts have
                    # different metrics. A small root-font stress catches layouts
                    # that only pass with controls on the viewport's last pixel.
                    await frame.evaluate("()=>document.documentElement.style.setProperty('font-size',parseFloat(getComputedStyle(document.documentElement).fontSize)*1.125+'px','important')")
                    await host.wait_for_timeout(150)
                    layout['fontStress'] = await frame.evaluate(LAYOUT)
                    if layout['fontStress']['coreOutsideCount'] or layout['fontStress']['horizontalOverflow']:
                        layout_issue('1000×600 字号容差检查（根字号+12.5%）核心图形/控件溢出。为字体差异留余量，缩小主图或使用响应式布局；不要缩小文字或隐藏核心控件。')

            await host.set_viewport_size({'width':1440,'height':900})
            frame = await load_work(host, code)
            initial_outputs = await frame.evaluate(OUTPUTS)
            if not (await frame.locator('body').inner_text()).strip():
                issues.append('页面缺少可读标题或操作说明。')
            controls = frame.locator(CONTROLS)
            total = await controls.count()
            changed, exercised = False, 0
            motion_checks, control_checks = [], []
            navigation_path = []
            navigation_states = [[]]
            navigation_replays = 0
            coverage_truncated = total > 40

            async def restore_controls():
                restored = await load_work(host, code)
                for saved_index, saved_label in navigation_path:
                    try:
                        tab = restored.locator(CONTROLS).nth(saved_index)
                        identity = await tab.get_attribute('id') or (await tab.inner_text()).strip()
                        if await tab.get_attribute('role') != 'tab' or identity != saved_label:
                            raise ValueError('navigation identity changed')
                        await tab.click(timeout=2000)
                    except Exception:
                        issues.append('无法重放标签页导航，后续控件覆盖不完整。')
                        navigation_path.clear()
                        restored = await load_work(host, code)
                        break
                return restored, restored.locator(CONTROLS)

            for index in range(min(total, 40)):
                if index >= await controls.count():
                    break
                control = controls.nth(index)
                if not await control.is_visible():
                    previous_path = list(navigation_path)
                    for alternative in navigation_states:
                        if alternative == previous_path:
                            continue
                        if navigation_replays >= 40:
                            coverage_truncated = True
                            issues.append('标签页导航重放达到预算，控件覆盖未完成。')
                            break
                        navigation_replays += 1
                        navigation_path[:] = alternative
                        frame, controls = await restore_controls()
                        if index >= await controls.count():
                            continue
                        control = controls.nth(index)
                        if await control.is_visible():
                            break
                    if index >= await controls.count():
                        continue
                    control = controls.nth(index)
                if not await control.is_visible() or not await control.is_enabled():
                    continue
                clicked_button = False
                navigation = False
                try:
                    before = await frame.evaluate(SIGNATURE)
                    tag = await control.evaluate('e=>e.tagName')
                    input_type = await control.get_attribute('type') if tag == 'INPUT' else None
                    label = (await control.get_attribute('aria-label') or await control.get_attribute('id') or await control.inner_text()).strip()[:80]
                    if tag == 'INPUT' and input_type == 'range':
                        previous = await control.input_value()
                        direction = await control.evaluate("e=>Number(e.value)<Number(e.max||100)?'ArrowRight':'ArrowLeft'")
                        await control.press(direction, timeout=2000)
                        current = await control.input_value()
                        can_change = await control.evaluate("e=>Number(e.max||100)>Number(e.min||0)")
                        if can_change and previous == current:
                            issues.append(f'滑块「{label}」方向键未改变值；不要拦截表单控件的默认键盘操作。')
                        # Exercise real keyboard boundary input; leave max set so
                        # subsequent presets also test combined extreme values.
                        for key in ('Home','End'):
                            await control.press(key, timeout=2000)
                            await host.wait_for_timeout(50)
                    elif tag in ('INPUT','TEXTAREA'):
                        if await control.get_attribute('readonly') is not None:
                            continue
                        if input_type == 'number':
                            value = await control.evaluate("""e=>{const v=Number(e.value||0),step=Number(e.step)||1;
                            const min=e.min===''?-1e6:Number(e.min),max=e.max===''?1e6:Number(e.max);
                            return String(Math.max(min,Math.min(max,v+step<=max?v+step:v-step)));}""")
                        else:
                            value = '<b data-work-qa-probe="true">验收样例</b>'
                        await control.fill(value, timeout=2000)
                        await control.press('Tab', timeout=2000)
                    elif tag == 'SELECT':
                        if await control.locator('option').count() < 2:
                            continue
                        await control.select_option(index=1, timeout=2000)
                    else:
                        clicked_button = True
                        text = (await control.inner_text()).strip()
                        navigation = await control.get_attribute('role') == 'tab'
                        identity = await control.get_attribute('id') or text
                        stops_or_resets = bool(re.search(r'^\W*(?:重置|暂停|停止|清空|取消|reset\b|pause\b|stop\b|clear\b|cancel\b)', text, re.I))
                        motion = bool(not navigation and not stops_or_resets and re.search(r'开始|启动|运行|播放|演示|继续|\b(?:start|play|run|resume)\b', text, re.I)
                            and re.search(r'\b(?:requestAnimationFrame|setInterval)\s*\(', code))
                        await control.click(timeout=2000)
                        if navigation:
                            navigation_path.append((index, identity))
                            if navigation_path not in navigation_states:
                                navigation_states.append(list(navigation_path))
                        if re.fullmatch(r'重置(?:初态)?|恢复初始|reset', text, re.I):
                            await host.wait_for_timeout(100)
                            reset_outputs = {x['key']:x['text'] for x in await frame.evaluate(OUTPUTS)}
                            # A nonempty initial numeric/result output must not
                            # become a placeholder after reset. Do not require a
                            # timer to forget the user's currently selected preset.
                            emptied = [x['key'] for x in initial_outputs if x['text'] not in ('—','--','-')
                                       and reset_outputs.get(x['key'],'') in ('','—','--','-')]
                            if emptied:
                                issues.append('重置未恢复初始计算结果，输出变空：'+', '.join(emptied[:6])+'。重置后重新计算并渲染。')
                        if motion:
                            await host.wait_for_timeout(100)
                            motion_before = await frame.evaluate(SIGNATURE)
                            advances, elapsed = False, 0
                            for _ in range(22):
                                await host.wait_for_timeout(100)
                                elapsed += 100
                                if motion_before != await frame.evaluate(SIGNATURE):
                                    advances = True
                                    break
                            motion_checks.append({'control':text,'advances':advances,'observedAfterMs':elapsed,'observationBudgetMs':2200})
                            if not advances:
                                issues.append(f'点击启动控件「{text}」后，动画/模拟时间/作品内容未持续变化。检查时间累加与真实经过时间。')
                    exercised += 1
                    await host.wait_for_timeout(100)
                    angle_evidence.extend(await frame.evaluate('()=>window.__workAngleEvidence()'))
                    text_evidence.extend(await frame.evaluate('()=>window.__workTextEvidence()'))
                    effect = before != await frame.evaluate(SIGNATURE)
                    changed = changed or effect
                    control_checks.append({'control':label,'contentChanged':effect})
                    # Buttons commonly open editors, delete rows or replace
                    # controls. Test the next control from a clean document so
                    # one stateful action cannot obscure or detach another.
                    if clicked_button and not navigation and index + 1 < min(total, 40):
                        frame, controls = await restore_controls()
                except Exception as exc:
                    # A broken candidate control is not QA infrastructure failure.
                    issues.append(f'第{index+1}个控件执行失败：{type(exc).__name__}。')
                    # Recover the harness after a modal/DOM mutation so one
                    # failure cannot cascade into every remaining control.
                    frame, controls = await restore_controls()
            if await frame.locator('[data-work-qa-probe]').count():
                issues.append('用户文本被解释为HTML；动态名称/内容必须用textContent或安全转义后渲染。')
            drawing_issues = await frame.evaluate('()=>window.__workDrawingIssues || []')
            # Rendered metrics are independent evidence for the semantic review.
            # Overlap can be intentional artwork, so it is not auto-failed as a
            # generic geometry rule; the reviewer checks purpose/readability.
            text_evidence = list({str(item):item for item in text_evidence}.values())[:20]
            if angle_evidence:
                first = angle_evidence[0]
                issues.append(f'光学角弧与标签不一致：标注{first["expectedDegrees"]}°但存在{first["actualSweepDegrees"]}°大弧。'
                    '检查Canvas arc的起止角与counterclockwise；入射角/反射角应画法线与光线之间的小夹角。')
            if drawing_issues:
                issues.append('参数边界下存在连续曲线超出Canvas高度而被裁切；按最大合成值调整坐标范围或留出绘图边距。')
            if not exercised or not changed:
                issues.append('未检测到可操作且能改变作品内容的交互控件。')
            # Smoke coverage and semantic assertions have separate evidence.
            # Start from clean state so previous edits cannot mask a dead input.
            frame = await load_work(host, code)
            contract_checks = await run_input_contract_probes(frame, host)
            for check in contract_checks:
                if check['status'] == 'failed':
                    issues.append(f'输入输出契约失败 {check["contract"]}「{check["control"]}」：'
                        f'输入{check["input"]}，预期{check["expected"]}，实际{check["observed"]}。')
            issues = list(dict.fromkeys(issues + js_errors + sandbox_errors))
            return {'ran':True,'passed':not issues,'issues':issues,'js_errors':list(dict.fromkeys(js_errors)),
                'layoutIssues':list(dict.fromkeys(layout_issues)),
                'sandbox':'allow-scripts','sandboxViolations':list(dict.fromkeys(sandbox_errors)),
                'interaction_performed':bool(exercised),'dom_changed_after_input':changed,
                'controlsExercised':exercised,'controlsDiscovered':total,'controlChecks':control_checks,
                'controlCoverageTruncated':coverage_truncated,'contentChanged':changed,
                'navigationStates':len(navigation_states),'navigationReplays':navigation_replays,
                'contractChecks':contract_checks,
                'drawingIssues':drawing_issues,'angleEvidence':angle_evidence[:10],
                'canvasTextEvidence':text_evidence,
                'motionChecks':motion_checks,'viewports':viewports}
        finally:
            await browser.close()
