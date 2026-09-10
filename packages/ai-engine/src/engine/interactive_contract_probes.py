"""Deterministic, scoped input/output assertions, separate from smoke effects.

Only recognize contracts with observable labels and unambiguous output scope.
Unrecognized controls are not certified by this registry. Add new domain probes
with positive, negative and ambiguous-scope fixtures; never infer acceptance
from a page-wide mutation or model-assigned score.
"""
from __future__ import annotations

import re


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
