"""Desktop interactive works use content/interaction QA, without arcade rules."""
from __future__ import annotations

import asyncio
import re
import time
from ..api.models import GameRuntimeContract, GameSpec, RunPipelineResponse, IterateResponse
from ..services.llm_client import LLMClient
from .code_generation_support import _extract_html
from .pipeline_errors import PipelineExecutionError
from .runtime_isolation import restrict_context_network, network_policy_meta
from .artifact_quality import request_artifact_kind, review_prompt, assess_review, preservation_errors

DESKTOP_BRIEF_MARKER = '请生成桌面浏览器中的可交互创意作品'


def is_interactive_request(request):
    return request_artifact_kind(request) in ('tool', 'science')


def normalize_interactive_request(request):
    if not is_interactive_request(request): return request
    request = request.model_copy(deep=True)
    kind = request_artifact_kind(request)
    request.artifact_kind = kind
    request.platform = 'desktop_web'
    request.prompt_bundle_snapshot = request.prompt_bundle_snapshot.model_copy(deep=True)
    request.prompt_bundle_snapshot.layers = {
        'creation_mode':'interactive_experience', 'source':'desktop-interactive-v1',
        'system_prompt':SYSTEM_PROMPT, 'artifact_kind':kind,
    }
    request.runtime_contract = GameRuntimeContract.model_validate({
        'version':'1.0', 'runtime_profile':'interactive_experience',
        'canvas':{'requires_canvas_2d':False,'orientation':'landscape_first','ui_scale_mode':'responsive'},
        'input':{'required_modes':['pointer','keyboard'],'target':'document','gestures':[]},
        'state':{'required_states':['ready','running','paused'],'restartable':True},
        'mobile_layout':{'orientation':'landscape_first','ui_scale_mode':'responsive'},
        'gameplay':{'requires_scoring':False,'requires_player_entity':False,'requires_terminal_state':False,
            'requires_restart_entry':False,'terminal_state_aliases':[],'primary_goal':'exploration_and_understanding'},
        'metadata':{'creation_mode':'interactive_experience','platform':'desktop_web','artifact_kind':kind},
    })
    # Old intent parsing may classify a biology model as a quiz. The user's
    # original brief, not those inferred game mechanics, drives this mode.
    description = str(getattr(request,'raw_user_input','') or getattr(request.source_spec,'source_description',''))
    request.source_spec = GameSpec.model_validate({
        'game_type':'interactive_experience', 'artifact_kind':kind, 'source_description':description,
        'intent_summary':description.split('\n')[0], 'ui_language':'zh-CN',
        'generation_tier':request.generation_tier or 'standard',
        'rules':{'win_condition':'not_applicable','lose_condition':'not_applicable','scoring':'none'},
        'platform_constraints':{'platform':'desktop_web','input_mode':'pointer_and_keyboard','render_api':'dom_svg_canvas'},
        'difficulty_curve':'none',
    })
    return request


def extract_interactive_document(text: str) -> str:
    code = _extract_html(text)
    # Strip an explanation after a complete document; never invent missing code.
    endings = list(re.finditer(r'</html\s*>', code, re.I))
    return code[:endings[-1].end()] if endings else code


async def validate_interactive_html(code: str) -> dict:
    from .runtime_qa import _runtime_qa_max_concurrency, _runtime_qa_semaphore
    async with _runtime_qa_semaphore(_runtime_qa_max_concurrency()):
        return await _validate_interactive_html(code)


async def _validate_interactive_html(code: str) -> dict:
    """Execute supplied HTML in a network-isolated Chromium context."""
    has_end = bool(re.search(r'</html\s*>\s*$', code, re.I))
    has_script = bool(re.search(r'<script\b|\son(?:click|input|change|submit|keydown|keyup)\s*=', code, re.I))
    if not has_end or not has_script:
        shape = f'bytes={len(code.encode())}, complete_html={has_end}, executable_script={has_script}'
        return {'passed':False,'issues':['输出必须是包含交互脚本的完整 HTML 文档。'+shape]}
    if len(code.encode()) > 300000:
        return {'passed':False,'issues':['作品超过 300 KB，请精简内联代码。']}
    from playwright.async_api import async_playwright
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            context = await browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
            await restrict_context_network(context)
            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)[:500]))
            secured = re.sub(r'(<head\b[^>]*>)', lambda m:m.group(1)+network_policy_meta(), code, count=1,flags=re.I)
            if secured == code: secured = network_policy_meta()+code
            await page.set_content(secured, wait_until='domcontentloaded',timeout=15000)
            await page.wait_for_timeout(300)
            if not (await page.locator('body').inner_text()).strip(): errors.append('页面缺少可读标题或操作说明。')
            signature = "() => document.body.innerText + Array.from(document.querySelectorAll('canvas')).map(c=>c.toDataURL()).join('') + Array.from(document.querySelectorAll('svg')).map(s=>s.outerHTML).join('')"
            changed = False
            motion_checks = []
            controls = page.locator('button, input[type=range], input[type=number], input[type=text], input:not([type]), textarea, select')
            exercised = 0
            for index in range(min(await controls.count(), 8)):
                control = controls.nth(index)
                if not await control.is_visible() or not await control.is_enabled(): continue
                before = await page.evaluate(signature)
                tag = await control.evaluate('(e)=>e.tagName')
                input_type = await control.get_attribute('type') if tag == 'INPUT' else None
                if tag == 'INPUT' and input_type == 'range':
                    await control.focus()
                    await control.press('ArrowRight')
                    await control.press('ArrowRight')
                elif tag in ('INPUT', 'TEXTAREA'):
                    if await control.get_attribute('readonly') is not None: continue
                    if input_type == 'number':
                        value = await control.evaluate('''e => {
                            const current = Number(e.value || 0), step = Number(e.step) || 1;
                            const min = e.min === '' ? -1e6 : Number(e.min);
                            const max = e.max === '' ? 1e6 : Number(e.max);
                            return String(Math.max(min, Math.min(max, current + step <= max ? current + step : current - step)));
                        }''')
                    else:
                        value = '验收样例'
                    await control.fill(value)
                    await control.press('Tab')
                elif tag == 'SELECT':
                    if await control.locator('option').count() < 2: continue
                    await control.select_option(index=1)
                else:
                    label = (await control.inner_text()).strip()
                    starts_motion = bool(re.search(r'开始|启动|运行|播放|演示|继续|\b(?:start|play|run|resume)\b', label, re.I)
                        and re.search(r'\b(?:requestAnimationFrame|setInterval)\s*\(', code))
                    await control.click(timeout=2000)
                    if starts_motion:
                        # A slider label or a Start -> Pause label is not proof
                        # that a simulation advances. Sample after the click's
                        # immediate UI changes, then require continuing output.
                        await page.wait_for_timeout(100)
                        motion_before = await page.evaluate(signature)
                        await page.wait_for_timeout(750)
                        advances = motion_before != await page.evaluate(signature)
                        motion_checks.append({'control':label,'advances':advances})
                        if not advances:
                            errors.append(f'点击启动控件「{label}」后，动画/模拟时间/作品内容未持续变化。检查 requestAnimationFrame 时间累加；不要对每帧小于固定步长的 elapsed 单独取整为零。')
                exercised += 1
                await page.wait_for_timeout(100)
                changed = changed or before != await page.evaluate(signature)
            if not exercised or not changed: errors.append('未检测到可操作且能改变作品内容的交互控件。')
            viewports=[]
            for width,height in [(1366,768),(1920,1080)]:
                await page.set_viewport_size({'width':width,'height':height})
                overflow = await page.evaluate('() => document.documentElement.scrollWidth > innerWidth + 2')
                viewports.append({'width':width,'height':height,'horizontalOverflow':overflow})
                if overflow: errors.append(f'{width}×{height} 桌面视口出现横向溢出。')
            return {'ran':True,'passed':not errors,'issues':errors,'js_errors':errors,
                'interaction_performed':bool(exercised),'dom_changed_after_input':changed,
                'controlsExercised':exercised,'contentChanged':changed,'motionChecks':motion_checks,'viewports':viewports}
        finally:
            await browser.close()


SYSTEM_PROMPT = '''你是桌面交互作品工程师。根据用户的原始创意，输出单个可离线运行的完整 HTML，只有代码，不要 Markdown。
用户的创意是最高内容约束；做模型、实验、讲解或可视化时，不要套用问答、关卡、积分、生命或输赢机制。
优先桌面浏览器 1366×768 至 1920×1080，响应式布局，DOM/SVG/Canvas 均可。鼠标和键盘能操作所有核心功能。
页面必须有标题、简明说明、可见且有标签的交互控件。暂停、重播、重置或参数调整必须真正改变作品状态。
若使用固定步长推进动画或模型，必须累积帧间时间并保留余量，不能对小于步长的每帧 elapsed 单独向下取整而使模拟永不前进。
科学作品必须说明模型、公式、单位、参数有效范围、简化假设和适用限制。不要把示意动画当成实验数据。
科学参数必须作用于计算模型，避免仅改变显示数字。不能编造测量结果。
所有代码和素材内联，不访问网络，不使用 iframe、弹窗、外部库、eval 或动态 Function。保持实现精炼但完整。
不要依赖宿主提供游戏 runtime、积分回调或 game_over 消息。遵从用户选择的方向和内容。'''


async def run_interactive(request, progress_cb=None):
    started = time.time()
    deadline = started + int(request.timeout_s or 1800)
    original = request.source_spec.source_description
    iterate = hasattr(request, 'current_code')
    feedback = request.iteration_intent.feedback if iterate else ''
    source_code = request.current_code if iterate else (getattr(request, 'source_code', '') or '')
    feedback = feedback or (original if source_code else '')
    kind = request_artifact_kind(request)
    prompt = original + (f'\n\n修改要求：{feedback}\n仅修改明确要求的部分，保留未要求改变的行为、模型和参数。\n当前作品：\n{source_code}' if source_code else '')
    client = LLMClient()
    issues=[]
    code=''
    for attempt in range(1, 3):
        if progress_cb: progress_cb('logic_generate',60,'正在实现桌面互动作品',{'attempt':attempt,'maxAttempts':2,'runtimeProfile':'interactive_experience'})
        remaining = max(1, int(deadline-time.time()))
        text = await client.complete_with_truncation_retry(
            max_tokens=8192, system=SYSTEM_PROMPT,
            messages=[{'role':'user','content':prompt + ('\n\n请修复以下运行检查问题，返回完整作品：\n'+'\n'.join(issues) if issues else '')}],
            step_key='iterate.element_change' if iterate else 'code_generate.full', stage='logic_generate',
            request_timeout_s=remaining, overall_timeout_s=remaining, response_size_hint='full_document',
            allow_provider_fallback=True,
            context_scope='request', compression_policy='code_generation',
            truncation_retry_attempts=1, truncation_retry_max_tokens=16384,
            timeout_retry_attempts=0,
        )
        code = extract_interactive_document(text)
        if progress_cb: progress_cb('runtime_simulation_qa',90,'正在检查桌面显示与交互',{'attempt':attempt})
        try:
            report = await asyncio.wait_for(validate_interactive_html(code), timeout=min(60,max(1,deadline-time.time())))
        except Exception as exc:
            raise PipelineExecutionError(f'Desktop runtime QA unavailable: {type(exc).__name__}', stage='runtime_simulation_qa',failure_family='runtime_infrastructure') from exc
        preserved = preservation_errors(source_code, code, feedback)
        report['issues'] = list(report.get('issues', [])) + preserved
        report['passed'] = bool(report['passed'] and not preserved)
        assessment = None
        if report['passed']:
            if progress_cb: progress_cb('code_review',94,'正在按作品类型检查功能与内容',{'artifactKind':kind,'attempt':attempt})
            remaining = max(1, int(deadline-time.time()))
            try:
                raw_review = await client.complete_with_truncation_retry(
                    max_tokens=2048, system='独立审核，严格遵循分类评分规则，只返回JSON。',
                    messages=[{'role':'user','content':review_prompt(kind, original, code, report)}],
                    step_key='code_review', stage='code_review', prefer_fast=True,
                    request_timeout_s=min(120,remaining), overall_timeout_s=min(120,remaining),
                    response_size_hint='small', allow_provider_fallback=True,
                    context_scope='request', compression_policy='code_review',
                    truncation_retry_attempts=1, truncation_retry_max_tokens=3072, timeout_retry_attempts=0,
                )
                assessment = assess_review(raw_review, kind)
            except Exception as exc:
                raise PipelineExecutionError('Artifact review unavailable: '+type(exc).__name__,
                    stage='code_review', failure_family='review_infrastructure') from exc
            if not assessment['passed']:
                report['issues'] += assessment['issues']
        if report['passed'] and assessment and assessment['passed']:
            common = dict(html_code=code,game_spec=request.source_spec,generation_time_ms=int((time.time()-started)*1000),
                qa_retries=attempt-1,pipeline_version='v2',runtime_profile='interactive_experience',runtime_qa_report=report,
                quality_score=assessment['score'],quality_breakdown=assessment | {'runtime_checks':report})
            if iterate: return IterateResponse(**common, changes=[feedback],iteration_type='element_change')
            return RunPipelineResponse(**common, game_id=request.game_id, strategy='llm_interactive',qa_passed=True,code_size_bytes=len(code.encode()))
        issues=report['issues']
        prompt += '\n\n上一次候选代码：\n'+code
        if progress_cb: progress_cb('logic_generate',66,'正在修复桌面交互检查发现的问题',{'attempt':attempt,'issues':issues})
    raise PipelineExecutionError('Desktop interaction validation failed: '+'; '.join(issues),
        stage='code_review' if assessment and not assessment['passed'] else 'runtime_simulation_qa',
        retry_count=1,failure_family='artifact_quality' if assessment and not assessment['passed'] else 'interactive_validation')
