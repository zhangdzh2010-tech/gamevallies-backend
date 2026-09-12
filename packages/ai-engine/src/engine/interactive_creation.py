"""Desktop interactive works use content/interaction QA, without arcade rules."""
from __future__ import annotations

import asyncio
import re
import time
import hashlib
from ..api.models import GameRuntimeContract, GameSpec, RunPipelineResponse, IterateResponse
from ..services.llm_client import LLMClient
from ..config.settings import settings
from .code_generation_support import _extract_html
from .pipeline_errors import (
    PipelineExecutionError,
    is_provider_transport_failure,
    is_retryable_provider_transport_failure,
)
from ..services.llm_http_evidence import attach_transport_evidence, collect_transport_evidence, transport_error_message
from .artifact_quality import request_artifact_kind, review_prompt, assess_review, preservation_errors, preserve_cosmetic_scripts, interactive_outcome_labels
from .interactive_repair import apply_interactive_patch, repair_prompt
from .review_recovery import (
    REVIEW_INFRASTRUCTURE_RETRY_LIMIT,
    recover_review,
    InvalidReviewEvidence,
)
from .candidate_checkpoint import CandidateCheckpoint
from .interactive_router import RouteDecision, route_interactive_template
from .interactive_telemetry import GenerationTelemetry
from .interactive_families import get_recipe
from .interactive_short_path import (
    FILL_STEP_KEY,
    assemble_short_path_document,
    default_slots,
    extract_fill_payload,
    fill_prompt,
    fill_system_prompt,
    merge_slots,
)
from .interactive_diversity import DiversityLedger, plan_interactive_diversity
from .template_registry import registry as template_registry
from ..services.task_memory import task_memory

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
        'system_prompt':interactive_system_prompt(kind), 'artifact_kind':kind,
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


def ensure_full_path_chrome(html: str, brief: str) -> str:
    """Inject a readable title on MISS/full path when the model omitted one.

    Does not invent interactive controls — missing inputs stay a full-path flake.
    """
    if not html:
        return html
    headline = (brief or '').split('\n')[0].strip()
    headline = re.sub(r'^作品类型[：:].*?(?:。|\.|$)', '', headline).strip() or headline
    headline = headline[:80]
    if not headline:
        return html
    escaped = (
        headline.replace('&', '&amp;').replace('<', '&lt;')
        .replace('>', '&gt;').replace('"', '&quot;')
    )
    has_title_text = bool(re.search(r'<title[^>]*>\s*[^<\s]', html, re.I))
    has_heading = bool(re.search(r'<h1\b', html, re.I))
    if has_title_text and has_heading:
        return html
    if not has_title_text:
        if re.search(r'<title[^>]*>\s*</title>', html, re.I):
            html = re.sub(r'<title[^>]*>\s*</title>', f'<title>{escaped}</title>', html, count=1, flags=re.I)
        elif re.search(r'<head\b', html, re.I):
            html = re.sub(r'(<head[^>]*>)', rf'\1<title>{escaped}</title>', html, count=1, flags=re.I)
    if not has_heading and re.search(r'<body\b', html, re.I):
        html = re.sub(r'(<body[^>]*>)', rf'\1<h1 id="work-title">{escaped}</h1>', html, count=1, flags=re.I)
    return html


def _review_infrastructure_retry_backoff_s() -> float:
    return 1.0


async def _review_interactive_artifact(request_review, kind, brief, code, *, progress_cb=None, attempt=1):
    """Structured review with bounded infra retries after playable QA. Never invents scores."""
    parse = lambda raw: assess_review(raw, kind, brief=brief, code=code)
    validate = lambda parsed: [] if parsed['review_ran'] else parsed['issues']
    last_exc: Exception | None = None
    for retry in range(REVIEW_INFRASTRUCTURE_RETRY_LIMIT + 1):
        try:
            return await recover_review(request_review, parse, validate)
        except InvalidReviewEvidence:
            raise
        except Exception as exc:
            last_exc = exc
            if retry >= REVIEW_INFRASTRUCTURE_RETRY_LIMIT:
                raise
            if progress_cb:
                progress_cb(
                    'code_review', 95, '正在重试结构化审核，保留已通过运行检查的候选',
                    {'attempt': attempt, 'reviewRetry': True,
                     'reviewRetryAttempt': retry + 1,
                     'failureFamily': 'review_infrastructure' if not is_provider_transport_failure(exc)
                     else 'provider_transport'},
                )
            await asyncio.sleep(_review_infrastructure_retry_backoff_s())
    raise last_exc or RuntimeError('assessment unavailable')


async def validate_interactive_html(code: str, *, brief: str = '') -> dict:
    from .runtime_qa import _runtime_qa_max_concurrency, _runtime_qa_semaphore
    wait_started = time.perf_counter()
    semaphore = _runtime_qa_semaphore(_runtime_qa_max_concurrency())
    async with semaphore:
        queue_wait_ms = (time.perf_counter() - wait_started) * 1000
        report = await _validate_interactive_html(code, brief=brief)
        report['queue_wait_ms'] = round(queue_wait_ms, 1)
        return report


async def _validate_interactive_html(code: str, *, brief: str = '') -> dict:
    """Execute supplied HTML in a network-isolated Chromium context."""
    has_end = bool(re.search(r'</html\s*>\s*$', code, re.I))
    has_script = bool(re.search(r'<script\b|\son(?:click|input|change|submit|keydown|keyup)\s*=', code, re.I))
    if not has_end or not has_script:
        shape = f'bytes={len(code.encode())}, complete_html={has_end}, executable_script={has_script}'
        return {'passed':False,'issues':['输出必须是包含交互脚本的完整 HTML 文档。'+shape]}
    if len(code.encode()) > 300000:
        return {'passed':False,'issues':['作品超过 300 KB，请精简内联代码。']}
    from .interactive_browser_qa import browser_report
    return await browser_report(code, brief=brief)


SYSTEM_PROMPT = '''你是桌面交互作品工程师。根据用户的原始创意，输出单个可离线运行的完整 HTML，只有代码，不要 Markdown。
用户的创意是最高内容约束；做模型、实验、讲解或可视化时，不要套用问答、关卡、积分、生命或输赢机制。
优先桌面浏览器 1366×768 至 1920×1080，响应式布局，DOM/SVG/Canvas 均可。鼠标和键盘能操作所有核心功能。
作品也会嵌入约1000×460的体验框，核心图形与主要操作须紧凑可见，说明可折叠。Canvas不要仅在脚本执行时测量一次尺寸：容器可能尚未布局或隐藏，应使用ResizeObserver配合requestAnimationFrame在可见尺寸变化时更新内在像素尺寸并重绘，避免观察循环；不能只依赖window resize。
页面必须有标题、简明说明、可见且有标签的交互控件。暂停、重播、重置或参数调整必须真正改变作品状态。
若使用固定步长推进动画或模型，必须累积帧间时间并保留余量，不能对小于步长的每帧 elapsed 单独向下取整而使模拟永不前进。
科学作品必须说明模型、公式、单位、参数有效范围、简化假设和适用限制。不要把示意动画当成实验数据。
科学参数必须作用于计算模型，避免仅改变显示数字。不能编造测量结果。
所有代码和素材内联，不访问网络，不使用 iframe、弹窗、外部库、eval 或动态 Function。保持实现精炼但完整。
正式播放环境是iframe sandbox="allow-scripts"，没有allow-modals或同源权限。不能调用alert/confirm/prompt；编辑、确认与错误提示必须使用页面内控件。宿主仅提供作品隔离的localStorage/sessionStorage接口。
长清单使用有界的内部滚动区域，主操作和汇总留在1000×600首屏内；非核心说明可用details折叠或标记data-work-secondary。不能隐藏核心按钮绕过首屏约束。
输入内容一律按纯文本处理，不把用户字符串直接拼进innerHTML。筛选只改变可见条目，不能悄悄改变总计口径；重置恢复完整初态。
表单控件必须保留方向键/Home/End默认操作。倒计时使用绝对截止时间与暂停余量，后台恢复补足真实经过时间；不要用钳制后的动画dt当计时时钟。
绘图须为所有参数边界组合预留坐标、线宽与标注空间；例如双波振幅均为A时合成最大值为2A，坐标范围必须额外留边，不能把峰值中心线直接贴在画布边缘。角弧采用正确方向与最小夹角，曲线峰值和摆球不能越出画布。重置前取消旧动画回调，避免重复循环。
多列清单必须给正文保留可阅读的最小宽度；窄桌面改为分组、切换日期或响应式少列布局，不能把中文任务挤成一字一行。编辑状态的保存按钮与提示文字须一致，并提供取消编辑入口。
光学反射中，入射箭头指向镜面交点，反射箭头离开交点；不能把两者都画成从镜面发出的光。Canvas中从-π/2-θ到-π/2的小弧使用顺时针方向(false)，不要反画成2π-θ大弧。
为不同系统的字体度量留出布局余量，根字号增大12.5%时1000×600核心区域仍完整；优先缩小主图、压缩空白或响应式重排，不缩小字体或隐藏主操作。
不要依赖宿主提供游戏 runtime、积分回调或 game_over 消息。遵从用户选择的方向和内容。'''

SCIENCE_RUNTIME_GUIDANCE = '''
科学演示运行契约（验收不会放宽）：
动态模型（单摆、轨道、波动、种群、流动等）在点击开始/播放后必须从可见的非平衡初态持续推进。默认不要停在θ=0、零振幅或静止平衡；不要把拖拽释放当作唯一启动方式。
开始、暂停、重置必须是可见且可区分的独立控件，名称写在按钮文本或aria-label中，不要只用图标。
摆长、重力、振幅、初角等参数须为带标签的range或number，并立即更新公式读数（如T=2π√(L/g)）；参数必须进入运动方程，不能只改标签。
连续动画用requestAnimationFrame或setInterval，按真实经过时间累积并重绘Canvas/SVG或更新可见状态，不能只改按钮文案或只靠CSS。
首屏用紧凑结构：说明/公式放details或短行，画布 max-width:100% 且 max-height:min(38vh,240px)，开始/暂停/重置与参数用flex-wrap留在画布下方。body padding不超过8px。根字号增大12.5%后1000×600核心图形与控件仍须完整可见；只能缩小主图、压缩空白或响应式重排，禁止缩小字号或隐藏主操作。'''


def interactive_system_prompt(kind: str) -> str:
    return SYSTEM_PROMPT + (SCIENCE_RUNTIME_GUIDANCE if kind == 'science' else '')


def science_runtime_repair_guidance(issues: list[str]) -> str:
    text = '\n'.join(issues)
    hints = []
    if re.search(r'未持续变化|时间累加|非平衡', text):
        hints.append(
            '动态模型须从可见非平衡初态启动：给摆球/轨道/波源一个默认位移或初速度，'
            '点击开始后用累积帧时间推进并重绘。不要依赖拖拽才开始，不要停在θ=0。'
            '开始/暂停/重置分开标注（文本或aria-label）。L/g等参数必须进入方程并更新公式读数。')
    if re.search(r'核心图形/控件不完整|字号容差|横向溢出', text):
        hints.append(
            '压缩说明与主图，开始/暂停/重置和参数控件留在1000×600首屏；次要公式假设可折叠。'
            '画布使用 max-width:100% 与 max-height:min(38vh,240px)，flex-wrap 排列控件，body padding≤8px。'
            '不要缩小字号或隐藏核心控件。')
    return '\n'.join(hints)


PREVIEW_LAYOUT_COMPACT_MARKERS = ('字号容差', '核心图形/控件不完整', '横向溢出')
PREVIEW_LAYOUT_COMPACT_STYLE = (
    '<style data-work-layout-compact="true">'
    'html,body{box-sizing:border-box;max-width:100%;overflow-x:hidden}'
    'body{margin:0 !important;padding:8px !important}'
    'h1,h2,h3,p{margin:4px 0 !important}'
    'canvas,svg{display:block !important;max-width:100% !important;'
    'max-height:min(38vh,240px) !important;width:auto !important;height:auto !important}'
    'button,input,select,label,output{display:inline-block !important;max-width:100%;'
    'margin:4px 6px !important;padding:6px 8px !important;vertical-align:middle}'
    'form,.toolbar,[data-work-controls]{display:flex !important;flex-wrap:wrap !important;'
    'gap:6px;align-items:center}'
    '</style>'
)


def should_apply_preview_layout_compact(report: dict) -> bool:
    issues = list(report.get('layoutIssues') or []) + list(report.get('issues') or [])
    return any(any(marker in issue for marker in PREVIEW_LAYOUT_COMPACT_MARKERS) for issue in issues)


def apply_preview_layout_compact(html: str) -> str:
    """Shrink the main graphic and spacing for 1000x600 + font-stress. Never shrink type."""
    if not html or 'data-work-layout-compact' in html:
        return html
    if re.search(r'</head\s*>', html, re.I):
        return re.sub(r'</head\s*>', PREVIEW_LAYOUT_COMPACT_STYLE + '</head>', html, count=1, flags=re.I)
    if re.search(r'<body\b', html, re.I):
        return re.sub(r'(<body\b[^>]*>)', r'\1' + PREVIEW_LAYOUT_COMPACT_STYLE, html, count=1, flags=re.I)
    return PREVIEW_LAYOUT_COMPACT_STYLE + html


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
    issues=[feedback] if source_code else []
    code=source_code
    report={'passed':False,'issues':[]}
    assessment=None
    candidate_history=[]
    checkpoint=None
    full_generations=0
    patch_calls=0
    qa_attempts=0
    terminal_failure_family = None
    telemetry = GenerationTelemetry()
    variation_seed = str(
        getattr(getattr(request, 'source_spec', None), 'variation_seed', None)
        or getattr(request, 'game_id', '')
        or ''
    )
    route = (
        RouteDecision(route='MISS', reason='iterate_preserves_source')
        if iterate else
        route_interactive_template(kind, original, variation_seed=variation_seed)
    )
    telemetry.set_route(route)
    diversity_plan = None
    assembled_slots = None
    diversity_ledger = DiversityLedger()

    async def persist_route_meta(extra=None):
        try:
            await task_memory.update_meta(
                getattr(request, 'task_id', None),
                template_route=telemetry.template_route,
                template_family=telemetry.family_id or '',
                template_recipe=telemetry.recipe_id or '',
                template_subject=telemetry.subject or '',
                **(extra or {}),
            )
            await task_memory.append_decision(
                getattr(request, 'task_id', None),
                f"template_route={telemetry.template_route} family={telemetry.family_id} recipe={telemetry.recipe_id}",
            )
        except Exception:
            pass

    await persist_route_meta()

    async def generate_full_document():
        nonlocal full_generations
        full_generations += 1
        remaining = max(1, int(deadline-time.time()))
        repair_hint = science_runtime_repair_guidance(issues) if issues else ''
        user_content = prompt + ('\n\n请修复以下运行检查问题，返回完整作品：\n'+'\n'.join(issues)
            + (('\n'+repair_hint) if repair_hint else '') if issues else '')
        telemetry.start('logic_generate')
        try:
            text = await client.complete_with_truncation_retry(
                max_tokens=8192, system=interactive_system_prompt(kind),
                messages=[{'role':'user','content':user_content}],
                step_key='iterate.element_change' if iterate else 'code_generate.full', stage='logic_generate',
                request_timeout_s=remaining, overall_timeout_s=remaining, response_size_hint='full_document',
                allow_provider_fallback=True,
                context_scope='request', compression_policy='code_generation',
                truncation_retry_attempts=1, truncation_retry_max_tokens=16384,
                timeout_retry_attempts=0, provider_retry_attempts=2,
                provider_retry_on_timeout_errors=True,
                provider_retry_base_delay_s=2, provider_retry_max_delay_s=8,
            )
        except Exception as exc:
            telemetry.record('logic_generate', prompt=user_content, step_key='code_generate.full')
            if is_provider_transport_failure(exc) or is_retryable_provider_transport_failure(exc):
                raise PipelineExecutionError(
                    f'Full LLM generation failed: {exc}',
                    stage='logic_generate', failure_family='provider_transport',
                ) from exc
            raise
        telemetry.record(
            'logic_generate', prompt=interactive_system_prompt(kind) + user_content,
            completion=text or '', step_key='iterate.element_change' if iterate else 'code_generate.full',
        )
        return ensure_full_path_chrome(extract_interactive_document(text), original)

    async def generate_short_path_document():
        nonlocal route, diversity_plan, assembled_slots
        recipe = get_recipe(route.recipe_id or '')
        if recipe is None:
            telemetry.template_route = 'MISS'
            telemetry.short_path = False
            telemetry.route_reason = 'missing_recipe'
            return await generate_full_document()
        plan = plan_interactive_diversity(
            family_id=recipe.family_id,
            recipe_id=recipe.id,
            title=recipe.title,
            formula=recipe.formula,
            variation_seed=variation_seed,
            generation_tier=str(getattr(request, 'generation_tier', '') or 'standard'),
            summary=original.split('\n')[0],
            ledger=diversity_ledger,
        )
        diversity_plan = plan
        telemetry.diversity_action = plan.action
        if plan.fallback_to_full and settings.INTERACTIVE_DIVERSITY_FALLBACK_ENABLED:
            telemetry.template_route = 'MISS'
            telemetry.short_path = False
            telemetry.route_reason = 'diversity_fallback'
            route = RouteDecision(route='MISS', reason='diversity_fallback', family_id=recipe.family_id, recipe_id=recipe.id, subject=recipe.subject)
            return await generate_full_document()
        slots = default_slots(recipe, plan, original)
        user_content = fill_prompt(kind=kind, brief=original, recipe=recipe, plan=plan, route=route)
        system = fill_system_prompt(kind)
        max_tokens = (
            settings.INTERACTIVE_SHORT_PATH_HIT_MAX_TOKENS if route.route == 'HIT'
            else settings.INTERACTIVE_SHORT_PATH_SOFT_MAX_TOKENS
        )
        remaining = max(1, int(deadline-time.time()))
        telemetry.start('logic_generate')
        try:
            text = await client.complete_with_truncation_retry(
                max_tokens=max_tokens, system=system,
                messages=[{'role':'user','content':user_content}],
                step_key=FILL_STEP_KEY, stage='logic_generate',
                request_timeout_s=remaining, overall_timeout_s=remaining,
                response_size_hint='medium_structured',
                allow_provider_fallback=True,
                context_scope='request', compression_policy='code_generation',
                truncation_retry_attempts=1, truncation_retry_max_tokens=max(max_tokens, 4096),
                timeout_retry_attempts=0, provider_retry_attempts=2,
                provider_retry_on_timeout_errors=True,
                provider_retry_base_delay_s=2, provider_retry_max_delay_s=8,
            )
        except Exception as exc:
            telemetry.record('logic_generate', prompt=user_content, step_key=FILL_STEP_KEY)
            if is_provider_transport_failure(exc) or is_retryable_provider_transport_failure(exc):
                raise PipelineExecutionError(
                    f'Full LLM generation failed: {exc}',
                    stage='logic_generate', failure_family='provider_transport',
                ) from exc
            raise
        telemetry.record('logic_generate', prompt=system + user_content, completion=text or '', step_key=FILL_STEP_KEY)
        assembled_slots = merge_slots(slots, extract_fill_payload(text or ''))
        return assemble_short_path_document(recipe=recipe, slots=assembled_slots, plan=plan)

    async def generate_document():
        nonlocal full_generations
        use_short = (
            settings.INTERACTIVE_SHORT_PATH_ENABLED
            and route.uses_short_path
            and not iterate
            and not issues
        )
        if use_short:
            return await generate_short_path_document()
        return await generate_full_document()

    for attempt in range(1, 3):
        if progress_cb: progress_cb('logic_generate',60,'正在实现桌面互动作品',{'attempt':attempt,'maxAttempts':2,'runtimeProfile':'interactive_experience'})
        remaining = max(1, int(deadline-time.time()))
        local_repair = bool(code and re.search(r'</html\s*>\s*$', code, re.I))
        layout_only = False
        if local_repair:
            # Protocol correction does not consume a semantic repair attempt.
            # Every batch is applied atomically to this same retained source.
            candidate = None
            repair_issues = list(issues)
            # Only browser-classified layout defects authorize a style-only edit.
            # Any additional semantic/runtime defect retains the general repair path.
            layout_only = bool(report.get('ran') and report.get('layoutIssues')
                and set(issues) == set(report['layoutIssues']))
            for correction in range(2):
                patch_calls += 1
                remaining = max(1, int(deadline-time.time()))
                text = await client.complete_with_truncation_retry(
                    max_tokens=4096, system='修复现有交互作品，只输出精确替换补丁JSON，不重写整个作品。',
                    messages=[{'role':'user','content':repair_prompt(
                        original + ('\n修改要求：' + feedback if feedback else ''),code,repair_issues,
                        layout_only=layout_only,
                        extra_guidance=science_runtime_repair_guidance(repair_issues))}],
                    step_key='iterate.element_change' if source_code and attempt == 1 else 'quality_gate.patch_fix', stage='logic_generate',
                    request_timeout_s=remaining, overall_timeout_s=remaining,
                    response_size_hint='large_patch', allow_provider_fallback=True,
                    context_scope='request', compression_policy='iteration_rewrite',
                    truncation_retry_attempts=1, truncation_retry_max_tokens=8192,
                    timeout_retry_attempts=0, provider_retry_on_timeout_errors=False,
                )
                try:
                    candidate = apply_interactive_patch(code, text, layout_only=layout_only)
                    break
                except ValueError as exc:
                    repair_issues = list(issues) + [str(exc)]
                    candidate_history.append({'artifact_type':'interactive_repair_protocol_report',
                        'content_type':'application/json','payload':{
                            'reason':str(exc),'originalIssues':list(issues),
                            'sourceSha256':hashlib.sha256(code.encode()).hexdigest()},
                        'metadata':{'attempt':attempt,'correctionAttempt':correction+1}})
                    if progress_cb: progress_cb('logic_generate',66,'正在纠正补丁协议，原候选保持不变',
                        {'attempt':attempt,'correctionAttempt':correction+1,'failureFamily':'repair_protocol'})
            if candidate is None:
                if source_code or layout_only:
                    # Neither an edit request nor a layout-only repair authorizes
                    # a full rewrite when its bounded patch protocol is exhausted.
                    issues = repair_issues
                    terminal_failure_family = 'repair_protocol'
                    report = dict(report, passed=False, issues=issues, generationAttempts={
                        'fullGenerationCalls':full_generations,'patchCalls':patch_calls,'qaAttempts':qa_attempts})
                    break
                # Creation has no user-owned source to preserve. Reuse its
                # existing second candidate budget for one full regeneration,
                # guided by the actual QA issues, not just the patch error.
                if progress_cb: progress_cb('logic_generate',66,'补丁协议未恢复，正在重新生成并重新验收',
                    {'attempt':attempt,'failureFamily':'repair_protocol','fullGenerationCalls':full_generations})
                candidate = await generate_document()
                local_repair = False
        else:
            candidate = await generate_document()
        code = preserve_cosmetic_scripts(source_code, candidate, feedback)
        if progress_cb: progress_cb('runtime_simulation_qa',90,'正在检查桌面显示与交互',{'attempt':attempt})
        qa_attempts += 1
        telemetry.start('runtime_simulation_qa')
        try:
            report = await asyncio.wait_for(validate_interactive_html(code, brief=original + ('\n' + feedback if feedback else '')), timeout=min(60,max(1,deadline-time.time())))
        except Exception as exc:
            tier = str(getattr(request, 'generation_tier', '') or 'standard').lower()
            if not settings.RUNTIME_QA_REQUIRED and tier != 'showcase':
                report = {'ran':False, 'passed':True, 'issues':[], 'softFailed':True,
                    'unavailableReason':type(exc).__name__}
                if progress_cb: progress_cb('runtime_simulation_qa',92,
                    '桌面运行检查暂不可用，保留候选并继续内容审核',
                    {'attempt':attempt,'softFailed':True,'unavailableReason':type(exc).__name__})
            else:
                raise PipelineExecutionError(f'Desktop runtime QA unavailable: {type(exc).__name__}',
                    stage='runtime_simulation_qa',failure_family='runtime_infrastructure',
                    artifacts=candidate_history + [{'artifact_type':'failed_interactive_candidate',
                        'content_type':'text/html','payload':code,'metadata':{'attempt':attempt,'stage':'runtime_simulation_qa'}}]) from exc
        telemetry.record(
            'runtime_simulation_qa',
            queue_wait_ms=float(report.get('queue_wait_ms') or 0),
            step_key='runtime_simulation_qa',
        )
        if (not report.get('passed') and should_apply_preview_layout_compact(report)
                and 'data-work-layout-compact' not in code):
            compacted = apply_preview_layout_compact(code)
            if compacted != code:
                qa_attempts += 1
                try:
                    compact_report = await asyncio.wait_for(
                        validate_interactive_html(
                            compacted, brief=original + ('\n' + feedback if feedback else '')),
                        timeout=min(60, max(1, deadline - time.time())))
                except Exception:
                    compact_report = None
                if compact_report is not None and (
                    compact_report.get('passed')
                    or len(compact_report.get('layoutIssues') or []) < len(report.get('layoutIssues') or [])
                ):
                    compact_report['layoutCompactApplied'] = True
                    code = compacted
                    report = compact_report
                    candidate_history.append({
                        'artifact_type':'interactive_candidate','content_type':'text/html',
                        'payload':code,'metadata':{'attempt':attempt,'operation':'layout_compact',
                                                 'runtimePassed':report['passed']}})
        preserved = preservation_errors(source_code, code, feedback)
        if source_code and code == source_code:
            preserved.append('修改没有产生有效变更，请在保留约束内落实用户要求。')
        report['issues'] = list(report.get('issues', [])) + preserved
        report['passed'] = bool(report['passed'] and not preserved)
        assessment = None
        if report['passed']:
            if progress_cb: progress_cb('code_review',94,'正在按作品类型检查功能与内容',{'artifactKind':kind,'attempt':attempt})
            remaining = max(1, int(deadline-time.time()))
            assessment_brief = original + ("\n修改要求：" + feedback if feedback else "")
            async def request_review(correction):
                remaining = max(1, int(deadline-time.time()))
                review_user = review_prompt(kind, assessment_brief, code, report) + ('\n\n' + correction if correction else '')
                telemetry.start('code_review')
                text = await client.complete_with_truncation_retry(
                    max_tokens=2048, system='独立审核，严格遵循分类评分规则，只返回JSON。',
                    messages=[{'role':'user','content':review_user}],
                    step_key='code_review', stage='code_review', prefer_fast=True,
                    request_timeout_s=min(120,remaining), overall_timeout_s=min(120,remaining),
                    response_size_hint='medium_structured', allow_provider_fallback=True,
                    context_scope='request', compression_policy='code_review',
                    truncation_retry_attempts=1, truncation_retry_max_tokens=3072, timeout_retry_attempts=0,
                )
                telemetry.record('code_review', prompt=review_user, completion=text or '', step_key='code_review')
                return text
            try:
                verified = await _review_interactive_artifact(
                    request_review,
                    kind,
                    assessment_brief,
                    code,
                    progress_cb=progress_cb,
                    attempt=attempt,
                )
                assessment = verified.assessment | {'reviewRequests':verified.requests,
                    'sourceSha256':hashlib.sha256(code.encode()).hexdigest()}
            except Exception as exc:
                family = 'review_evidence' if isinstance(exc, InvalidReviewEvidence) else (
                    'provider_transport' if is_provider_transport_failure(exc) else 'review_infrastructure'
                )
                if family == 'provider_transport':
                    # Playable QA already passed; keep this as review infra so a
                    # later catalog seed check can still retry, not as a creative miss.
                    family = 'review_infrastructure'
                evidence = collect_transport_evidence(exc)
                message = (
                    'Artifact assessment failed: '+str(exc) if isinstance(exc, InvalidReviewEvidence)
                    else 'Artifact review unavailable: '+transport_error_message(evidence, type(exc).__name__)
                )
                failure = PipelineExecutionError(
                    message,
                    stage='code_review', failure_family=family,
                    artifacts=candidate_history + [
                        {'artifact_type':'failed_interactive_candidate','content_type':'text/html',
                         'payload':code,'metadata':{'attempt':attempt,'stage':'code_review'}},
                        {'artifact_type':'interactive_validation_report','content_type':'application/json',
                         'payload':{'runtime':report,'reviewErrors':getattr(exc,'errors',[type(exc).__name__]),
                                    **({'transportEvidence': evidence} if evidence else {})},
                         'metadata':{'attempt':attempt,'stage':'code_review'}},
                    ])
                attach_transport_evidence(failure, evidence)
                raise failure from exc
            if not assessment['passed']:
                report['issues'] += assessment['issues']
        report['generationAttempts']={'fullGenerationCalls':full_generations,'patchCalls':patch_calls,'qaAttempts':qa_attempts}
        candidate_history.append({'artifact_type':'interactive_candidate','content_type':'text/html',
            'payload':code,'metadata':{'attempt':attempt,'operation':'patch' if local_repair else 'full_generation',
                                     'repairScope':'style' if layout_only else 'document',
                                     'runtimePassed':report['passed'],'reviewPassed':bool(assessment and assessment['passed']),
                                     'sourceSha256':hashlib.sha256(code.encode()).hexdigest()}})
        regressions = checkpoint.regression_errors(report, assessment, kind) if checkpoint else []
        if regressions:
            rejected_issues = list(report['issues'])
            code, report, assessment = checkpoint.code, dict(checkpoint.runtime), checkpoint.assessment
            issues = list(report['issues']) + regressions + rejected_issues
            report['issues'] = issues
            report['generationAttempts']={'fullGenerationCalls':full_generations,'patchCalls':patch_calls,'qaAttempts':qa_attempts}
            candidate_history[-1]['metadata']['discardedRegression'] = True
            if progress_cb: progress_cb('logic_generate',66,'正在保留已验证版本并修正回归',{'attempt':attempt,'issues':issues})
            continue
        checkpoint = CandidateCheckpoint.capture(code, report, assessment)
        if report['passed'] and assessment and assessment['passed']:
            labels = interactive_outcome_labels(assessment, report)
            efficiency = telemetry.to_dict()
            if diversity_plan is not None:
                efficiency['diversity'] = diversity_plan.to_dict()
            labels = labels | telemetry.yield_fields() | {
                'generation_efficiency': efficiency,
            }
            if labels.get('seed_worthy') and telemetry.family_id and telemetry.recipe_id:
                template_registry.observe(
                    family_id=telemetry.family_id,
                    recipe_id=telemetry.recipe_id,
                    subject=telemetry.subject or '',
                    template_route=telemetry.template_route,
                    fingerprint=(diversity_plan.fingerprint if diversity_plan else ''),
                    slots=assembled_slots or {},
                    seed_worthy=True,
                )
                if settings.INTERACTIVE_TEMPLATE_AUTO_PROMOTE and diversity_plan is not None:
                    template_registry.promote_to_production(
                        diversity_plan.fingerprint, allow_auto=True,
                    )
            await persist_route_meta(telemetry.yield_fields())
            common = dict(html_code=code,game_spec=request.source_spec,generation_time_ms=int((time.time()-started)*1000),
                qa_retries=attempt-1,pipeline_version='v2',runtime_profile='interactive_experience',runtime_qa_report=report,
                quality_score=assessment['score'],quality_breakdown=assessment | {'runtime_checks':report} | labels)
            if iterate: return IterateResponse(**common, changes=[feedback],iteration_type='element_change')
            return RunPipelineResponse(**common, game_id=request.game_id, strategy='llm_interactive',qa_passed=True,code_size_bytes=len(code.encode()))
        issues=report['issues']
        # Only one current candidate is sent to the repair model. Accumulating
        # previous complete documents increases cost and encourages regressions.
        if progress_cb: progress_cb('logic_generate',66,'正在修复桌面交互检查发现的问题',{'attempt':attempt,'issues':issues})
    raise PipelineExecutionError('Desktop interaction validation failed: '+'; '.join(issues),
        stage='code_review' if assessment and not assessment['passed'] else 'runtime_simulation_qa',
        retry_count=1,failure_family=terminal_failure_family or ('artifact_quality' if assessment and not assessment['passed'] else 'interactive_validation'),
        # Use the existing author-scoped artifact channel, not progress messages.
        # Without the rejected candidate a failed desktop run cannot be replayed
        # locally, forcing another paid generation just to diagnose the failure.
        artifacts=candidate_history + [
            {'artifact_type':'failed_interactive_candidate','content_type':'text/html',
             'payload':code,'metadata':{'attempt':attempt,'runtimeProfile':'interactive_experience'}},
            {'artifact_type':'interactive_validation_report','content_type':'application/json',
             'payload':{'runtime':report,'assessment':assessment,'attempt':attempt},
             'metadata':{'runtimeProfile':'interactive_experience'}},
        ])
