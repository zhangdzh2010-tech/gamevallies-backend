"""Deterministic, scoped input/output assertions, separate from smoke effects.

Only recognize contracts with observable labels and unambiguous output scope.
Unrecognized controls are not certified by this registry. Add new domain probes
with positive, negative and ambiguous-scope fixtures; never infer acceptance
from a page-wide mutation or model-assigned score.
"""
from __future__ import annotations

import re


def reset_requirement(brief: str) -> str | None:
    """Conservative source-grounded opt-in, not a guess from a Reset label.

    Unknown wording is not certified. A reset that intentionally preserves
    presets remains valid unless the user explicitly asks to restore all state.
    """
    clauses = re.split(r'[。；;\n.!?！？]', brief)
    if any(re.search(r'(?:重置|reset).*?(?:保留|保持|不恢复|retain|keep|preserve)', c, re.I)
           for c in clauses):
        return None
    for clause in clauses:
        match = re.search(r'重置(?:后)?恢复(?:全部|所有|完整)(?:的)?初始状态|'
                          r'reset\s+(?:must\s+)?restore(?:s)?\s+all\s+initial\s+state', clause, re.I)
        if match and not re.search(r'不要|不需要|无需|不必|不应|\b(?:not|never|without)\b', clause, re.I):
            return match.group(0)
    return None


async def run_reset_contract_probe(frame, host, brief: str) -> list[dict]:
    """Observe initial inputs -> real user edits -> reset -> restored inputs.

    This verifies visible form parameters only, not canvas/model/internal state.
    Multiple reset actions or excessive scope are explicitly unverified.
    """
    quote = reset_requirement(brief)
    if not quote:
        return []
    result = {'contract':'reset_all_inputs_v1', 'control':'全局重置',
              'requirementQuote':quote, 'input':[], 'expected':[], 'observed':[],
              'status':'unverified', 'scope':'visible_editable_form_parameters'}
    buttons = frame.locator('button,[role=button],input[type=reset]')
    resets = []
    for index in range(min(await buttons.count(), 40)):
        button = buttons.nth(index)
        if not await button.is_visible() or not await button.is_enabled():
            continue
        label = (await button.get_attribute('aria-label') or await button.inner_text()
                 or await button.get_attribute('value') or '').strip()
        normalized = re.sub(r'^\W+|\W+$', '', label)
        if re.fullmatch(r'重置(?:全部|所有|初态|为初始状态)?|全部重置|恢复(?:全部)?初始(?:状态)?|reset(?: all)?', normalized, re.I):
            resets.append(label)
    if len(resets) != 1:
        return [result | {'reason':'No unique visible global reset action'}]
    selector = 'input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=reset]):not([type=file]):not([type=password]):not([type=radio]),textarea,select'
    inputs = frame.locator(selector)
    if await inputs.count() > 12:
        return [result | {'reason':'Form scope exceeds bounded 12-control probe'}]
    baseline = []
    try:
        for index in range(await inputs.count()):
            control = inputs.nth(index)
            if not await control.is_visible() or not await control.is_enabled() or await control.get_attribute('readonly') is not None:
                continue
            state = await control.evaluate("e=>({id:e.id,name:e.name,tag:e.tagName,type:e.type,value:e.type==='checkbox'?e.checked:e.value})")
            baseline.append({'index':index, **state})
        for item in baseline:
            control = inputs.nth(item['index'])
            if item['type'] == 'checkbox':
                await control.click(timeout=1000)
            elif item['type'] == 'range':
                direction = await control.evaluate("e=>Number(e.value)===Number(e.max||100)?'Home':'End'")
                await control.press(direction, timeout=1000)
            elif item['tag'] == 'SELECT':
                options = await control.locator('option').evaluate_all('es=>es.filter(e=>!e.disabled).map(e=>e.value)')
                alternatives = [v for v in options if v != item['value']]
                if alternatives:
                    await control.select_option(value=alternatives[0], timeout=1000)
            elif item['type'] == 'number':
                value = await control.evaluate("""e=>{const v=Number(e.value||0),s=Number(e.step)||1,
                lo=e.min===''?-1e6:Number(e.min),hi=e.max===''?1e6:Number(e.max);
                return String(Math.max(lo,Math.min(hi,v+s<=hi?v+s:v-s)));}""")
                await control.fill(value, timeout=1000)
                await control.press('Tab', timeout=1000)
            elif item['type'] in ('text','search','email','url','tel','textarea'):
                await control.fill('验收参数', timeout=1000)
                await control.press('Tab', timeout=1000)
        read = "e=>({id:e.id,name:e.name,tag:e.tagName,type:e.type,value:e.type==='checkbox'?e.checked:e.value})"
        for item in baseline:
            result['input'].append(await inputs.nth(item['index']).evaluate(read))
        result['expected'] = [{k:v for k,v in item.items() if k != 'index'} for item in baseline]
        if not baseline or result['input'] == result['expected']:
            return [result | {'reason':'No visible parameter was changed; reset was not exercised'}]
        reset_button = frame.get_by_role('button', name=resets[0], exact=True)
        if await reset_button.count() != 1:
            return [result | {'reason':'Global reset scope changed while editing parameters'}]
        await reset_button.click(timeout=1000)
        for _ in range(5):
            await host.wait_for_timeout(100)
            result['observed'] = [await inputs.nth(item['index']).evaluate(read) for item in baseline]
            if result['observed'] == result['expected']:
                return [result | {'status':'passed'}]
        return [result | {'status':'failed', 'reason':'Reset did not restore initial visible parameter values'}]
    except Exception as exc:
        return [result | {'status':'failed', 'error':type(exc).__name__,
                          'reason':'Candidate controls failed during the bounded reset transition'}]


def rgb_output(text: str) -> tuple[int, int, int] | None:
    labelled = re.findall(r'(?<![A-Za-z])([RGB])\s*[:：]?\s*(\d{1,3})(?!\d)', text, re.I)
    channels = {name.upper(): int(value) for name, value in labelled}
    if len(labelled) == 3 and set(channels) == {'R', 'G', 'B'}:
        return channels['R'], channels['G'], channels['B']
    grouped = re.findall(r'\bRGB\s*[:：]?\s*\(?\s*(\d{1,3})\s*[,， ]\s*(\d{1,3})\s*[,， ]\s*(\d{1,3})', text, re.I)
    return tuple(map(int, grouped[0])) if len(grouped) == 1 else None


async def run_input_contract_probes(frame, host) -> list[dict]:
    results = []
    inputs = frame.locator('input[type=text],input:not([type])')
    for index in range(min(await inputs.count(), 40)):
        control = inputs.nth(index)
        if not await control.is_visible() or not await control.is_enabled():
            continue
        if await control.get_attribute('readonly') is not None:
            continue
        hint = await control.evaluate("e=>[e.getAttribute('aria-label'),e.id,e.placeholder,...Array.from(e.labels||[]).map(l=>l.textContent)].join(' ')")
        initial = await control.input_value()
        if not re.search(r'hex|十六进制|16进制', hint, re.I) or not re.fullmatch(r'#?[0-9a-f]{6}', initial, re.I):
            continue
        # The nearest ancestor with exactly this one text field and a single
        # readable RGB result is the assertion scope. Never match another card.
        scope = control.locator('..')
        before = None
        for _ in range(6):
            if await scope.locator('input[type=text],input:not([type])').count() != 1:
                break
            before = rgb_output(await scope.inner_text())
            if before is not None:
                break
            scope = scope.locator('..')
        if before is None:
            results.append({'contract':'hex_rgb_v1','control':hint.strip()[:80],
                'status':'unverified','reason':'No unambiguous labelled RGB output scope'})
            continue
        value, expected = ('00FF00', (0,255,0)) if initial.lstrip('#').upper() == 'FF0000' else ('FF0000', (255,0,0))
        value = ('#' if initial.startswith('#') else '') + value
        observed = None
        error = None
        try:
            await control.fill(value, timeout=2000)
            await control.press('Tab', timeout=2000)
            # Allow bounded debounce/render latency, while comparing actual output
            # with a server-computed oracle rather than unrelated DOM mutation.
            for _ in range(10):
                await host.wait_for_timeout(100)
                observed = rgb_output(await scope.inner_text())
                if observed == expected:
                    break
            await control.fill(initial, timeout=2000)
            await control.press('Tab', timeout=2000)
        except Exception as exc:
            # A control detaching or timing out is candidate evidence, not
            # optional QA-infrastructure failure that could accept this work.
            error = type(exc).__name__
        results.append({'contract':'hex_rgb_v1','control':hint.strip()[:80],
            'input':value,'expected':list(expected),'observed':list(observed) if observed else None,
            'error':error,'status':'passed' if observed == expected and not error else 'failed'})
    return results
