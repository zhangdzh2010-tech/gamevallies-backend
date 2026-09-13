"""L0 DesktopRuntimeShell — shared engine skeleton for science/tool short path.

Templates are skeleton-only: canvas, rAF, input, start/pause/reset, and a
fixed-step accumulator that guarantees simulated time advances after start.
Never a whole-page reskin. Family plugins supply the model; L3 slots supply
presentation copy and visual accents.
"""
from __future__ import annotations

import re
from typing import Any

SHELL_VERSION = "desktop-runtime-v1"
CONVERTER_SHELL_VERSION = "converter-runtime-v1"
# Exact CSS selector kept in the assembled source so layout repair search hits.
CONVERTER_CONTROL_SEARCH = "#convertBtn,#resetBtn"

# Leftover-accumulator contract: never floor a sub-step dt to zero.
# Python mirror of the in-page loop so unit tests can prove the guarantee
# without a browser.
FIXED_STEP_S = 1.0 / 60.0
MAX_FRAME_DT_S = 0.05


def accumulate_sim_time(
    frame_elapsed_s: list[float],
    *,
    running: bool = True,
    leftover: float = 0.0,
    sim_time: float = 0.0,
    fixed_step_s: float = FIXED_STEP_S,
) -> tuple[float, float, int]:
    """Advance sim time with a leftover accumulator.

    Returns (sim_time, leftover, steps_taken). After start, any positive
    elapsed total that reaches one fixed step must increment sim_time.
    Sub-step frames are retained in leftover instead of being discarded.
    """
    steps = 0
    acc = leftover
    if not running:
        return sim_time, acc, 0
    for raw in frame_elapsed_s:
        dt = min(MAX_FRAME_DT_S, max(0.0, float(raw)))
        acc += dt
        while acc + 1e-15 >= fixed_step_s:
            acc -= fixed_step_s
            sim_time += fixed_step_s
            steps += 1
    return sim_time, acc, steps


def shell_time_advance_contract_errors(html: str) -> list[str]:
    """Static contract: assembled documents must keep the accumulator shell."""
    errors: list[str] = []
    if 'data-work-shell="' + SHELL_VERSION + '"' not in html and "data-work-shell='desktop-runtime-v1'" not in html:
        errors.append("missing DesktopRuntimeShell marker")
    if "requestAnimationFrame" not in html:
        errors.append("missing rAF loop")
    if not re.search(r'id=["\']btn-start["\']', html):
        errors.append("missing start control")
    if not re.search(r'id=["\']btn-pause["\']', html):
        errors.append("missing pause control")
    if not re.search(r'id=["\']btn-reset["\']', html):
        errors.append("missing reset control")
    canvas_el = re.search(r"<canvas\b[^>]*\bid=['\"]work-canvas['\"]", html, re.I)
    if "work-canvas" not in html or not canvas_el:
        errors.append("missing canvas")
    ctx_call = re.search(r"\.getContext\s*\(", html)
    if canvas_el and ctx_call and ctx_call.start() < canvas_el.start():
        errors.append("getContext used before canvas element exists")
    if "WorkRuntime" not in html:
        errors.append("missing WorkRuntime state machine")
    if "acc +=" not in html and "acc+=" not in html:
        errors.append("missing leftover time accumulator")
    if "Math.floor" in html and re.search(r"Math\.floor\([^)]*elapsed", html):
        errors.append("floors per-frame elapsed instead of accumulating leftover")
    if not re.search(r'data-work-family-script\s*=', html):
        errors.append("family plugin shares the runtime script; isolate it so a plugin syntax error cannot kill Start/simTime")
    if not re.search(r'id=["\']work-sim-time["\']|data-work-sim-time', html):
        errors.append("missing sim-time output that Start must keep updating")
    return errors


def converter_shell_contract_errors(html: str) -> list[str]:
    """Static contract: converter tools keep operable convert/reset on the first screen."""
    errors: list[str] = []
    if f'data-work-shell="{CONVERTER_SHELL_VERSION}"' not in html:
        errors.append("missing converter shell marker")
    if not re.search(r'id=["\']convertBtn["\']', html):
        errors.append("missing convertBtn")
    if not re.search(r'id=["\']resetBtn["\']', html):
        errors.append("missing resetBtn")
    if not re.search(r'id=["\']valueInput["\']', html):
        errors.append("missing valueInput")
    if not re.search(r'id=["\']fromUnit["\']', html) or not re.search(r'id=["\']toUnit["\']', html):
        errors.append("missing unit selectors")
    if not re.search(r'<output\b[^>]*id=["\']convertResult["\']', html, re.I):
        errors.append("missing convertResult output")
    if CONVERTER_CONTROL_SEARCH not in html:
        errors.append("missing convertBtn/resetBtn layout search target")
    if re.search(r"\balert\s*\(", html):
        errors.append("uses alert")
    return errors


def render_shell(
    *,
    title: str,
    summary: str,
    formula: str,
    assumptions: str,
    limits: str,
    param_controls_html: str,
    family_script: str,
    visual_css: str = "",
    family_id: str = "",
    recipe_id: str = "",
    subject: str = "",
    visual_pack_id: str = "",
    variation_seed: str = "",
    readout_html: str = "",
) -> str:
    """Assemble a complete offline HTML document around the shared shell."""
    css = visual_css or _DEFAULT_VISUAL_CSS
    readout = readout_html or '<output id="work-readout" data-work-readout></output>'
    replacements = {
        "{{TITLE}}": _escape(title),
        "{{SUMMARY}}": _escape(summary),
        "{{FORMULA}}": _escape(formula),
        "{{ASSUMPTIONS}}": _escape(assumptions),
        "{{LIMITS}}": _escape(limits),
        "{{PARAM_CONTROLS}}": param_controls_html,
        "{{FAMILY_SCRIPT}}": family_script,
        "{{VISUAL_CSS}}": css,
        "{{FAMILY_ID}}": _escape(family_id),
        "{{RECIPE_ID}}": _escape(recipe_id),
        "{{SUBJECT}}": _escape(subject),
        "{{VISUAL_PACK_ID}}": _escape(visual_pack_id),
        "{{VARIATION_SEED}}": _escape(variation_seed),
        "{{SHELL_VERSION}}": SHELL_VERSION,
        "{{READOUT}}": readout,
    }
    html = _SHELL_HTML
    for token, value in replacements.items():
        html = html.replace(token, str(value))
    return html


def render_converter_shell(
    *,
    title: str,
    summary: str,
    formula: str,
    assumptions: str,
    limits: str,
    converter_controls_html: str,
    family_script: str,
    visual_css: str = "",
    family_id: str = "",
    recipe_id: str = "",
    subject: str = "",
    visual_pack_id: str = "",
    variation_seed: str = "",
) -> str:
    """Compact tool shell: convert/reset stay on the 1000×600 first screen."""
    css = (visual_css or _DEFAULT_VISUAL_CSS) + _CONVERTER_LAYOUT_CSS
    replacements = {
        "{{TITLE}}": _escape(title),
        "{{SUMMARY}}": _escape(summary),
        "{{FORMULA}}": _escape(formula),
        "{{ASSUMPTIONS}}": _escape(assumptions),
        "{{LIMITS}}": _escape(limits),
        "{{CONVERTER_CONTROLS}}": converter_controls_html,
        "{{FAMILY_SCRIPT}}": family_script,
        "{{VISUAL_CSS}}": css,
        "{{FAMILY_ID}}": _escape(family_id),
        "{{RECIPE_ID}}": _escape(recipe_id),
        "{{SUBJECT}}": _escape(subject),
        "{{VISUAL_PACK_ID}}": _escape(visual_pack_id),
        "{{VARIATION_SEED}}": _escape(variation_seed),
        "{{SHELL_VERSION}}": CONVERTER_SHELL_VERSION,
    }
    html = _CONVERTER_SHELL_HTML
    for token, value in replacements.items():
        html = html.replace(token, str(value))
    return html


def _escape(value: Any) -> str:
    text = str(value or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_DEFAULT_VISUAL_CSS = """
html,body{box-sizing:border-box;max-width:100%;overflow-x:hidden}
body{margin:0;padding:8px;font:16px/1.4 'Helvetica Neue',Arial,sans-serif;background:#f8fafc;color:#0f172a}
h1{margin:4px 0;font-size:1.25rem}
p{margin:4px 0}
canvas{display:block;max-width:100%;max-height:min(38vh,240px);width:100%;height:auto;background:#0f172a;border-radius:8px}
form[data-work-controls]{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px}
button,input,label,output{margin:4px 6px;padding:6px 8px;vertical-align:middle}
details{margin:4px 0}
"""

# Keep the selector literal identical to CONVERTER_CONTROL_SEARCH.
_CONVERTER_LAYOUT_CSS = """
#convertBtn,#resetBtn{display:inline-flex;align-items:center;margin:4px 6px;padding:6px 10px;max-width:100%;flex:0 1 auto}
[data-work-converter-controls]{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px}
#valueInput,#fromUnit,#toUnit,#convertResult{max-width:100%;margin:4px 6px;padding:6px 8px}
#convertHint{margin:4px 0}
"""

_CONVERTER_SHELL_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title>
<style data-work-shell-style="true" data-work-converter-style="true">{{VISUAL_CSS}}</style>
</head>
<body data-work-shell="{{SHELL_VERSION}}" data-family="{{FAMILY_ID}}" data-recipe="{{RECIPE_ID}}" data-subject="{{SUBJECT}}" data-visual-pack="{{VISUAL_PACK_ID}}" data-variation-seed="{{VARIATION_SEED}}">
<h1 id="work-title">{{TITLE}}</h1>
<p id="work-summary">{{SUMMARY}}</p>
<form data-work-controls data-work-converter-controls onsubmit="return false">
{{CONVERTER_CONTROLS}}
</form>
<details id="work-model"><summary>换算关系与限制</summary>
<p id="work-formula">{{FORMULA}}</p>
<p id="work-assumptions">{{ASSUMPTIONS}}</p>
<p id="work-limits">{{LIMITS}}</p>
</details>
<script>
(function(){
  {{FAMILY_SCRIPT}}
  var valueInput = document.getElementById('valueInput');
  var fromUnit = document.getElementById('fromUnit');
  var toUnit = document.getElementById('toUnit');
  var result = document.getElementById('convertResult');
  var hint = document.getElementById('convertHint');
  var convertBtn = document.getElementById('convertBtn');
  var resetBtn = document.getElementById('resetBtn');
  function show(){
    var rawText = valueInput ? String(valueInput.value || '').trim() : '';
    var raw = Number(rawText);
    var converted = window.WorkFamily && WorkFamily.convert
      ? WorkFamily.convert(raw, fromUnit && fromUnit.value, toUnit && toUnit.value)
      : null;
    if (!rawText || converted == null || !isFinite(raw)){
      if (hint){ hint.hidden = false; hint.textContent = '请输入有效数字'; }
      if (result) result.textContent = '无效输入';
      return;
    }
    if (hint){ hint.hidden = true; hint.textContent = ''; }
    if (result) result.textContent = WorkFamily.format(converted, fromUnit.value, toUnit.value);
  }
  function reset(){
    var initial = window.WorkFamily && WorkFamily.reset ? WorkFamily.reset() : null;
    if (valueInput) valueInput.value = initial ? initial.value : (WorkFamily && WorkFamily.initialValue) || '1';
    if (fromUnit) fromUnit.value = initial ? initial.from : (WorkFamily && WorkFamily.initialFrom) || 'm';
    if (toUnit) toUnit.value = initial ? initial.to : (WorkFamily && WorkFamily.initialTo) || 'ft';
    show();
  }
  if (convertBtn) convertBtn.addEventListener('click', show);
  if (resetBtn) resetBtn.addEventListener('click', reset);
  if (valueInput) valueInput.addEventListener('input', show);
  if (fromUnit) fromUnit.addEventListener('change', show);
  if (toUnit) toUnit.addEventListener('change', show);
  reset();
})();
</script>
</body>
</html>
"""


_SHELL_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title>
<style data-work-shell-style="true">{{VISUAL_CSS}}</style>
</head>
<body data-work-shell="{{SHELL_VERSION}}" data-family="{{FAMILY_ID}}" data-recipe="{{RECIPE_ID}}" data-subject="{{SUBJECT}}" data-visual-pack="{{VISUAL_PACK_ID}}" data-variation-seed="{{VARIATION_SEED}}">
<h1 id="work-title">{{TITLE}}</h1>
<p id="work-summary">{{SUMMARY}}</p>
<details id="work-model"><summary>模型、公式与假设</summary>
<p id="work-formula">{{FORMULA}}</p>
<p id="work-assumptions">{{ASSUMPTIONS}}</p>
<p id="work-limits">{{LIMITS}}</p>
</details>
<canvas id="work-canvas" width="720" height="220"></canvas>
<form data-work-controls onsubmit="return false">
<button type="button" id="btn-start">开始</button>
<button type="button" id="btn-pause">暂停</button>
<button type="button" id="btn-reset">重置</button>
{{PARAM_CONTROLS}}
{{READOUT}}
<output id="work-sim-time" data-work-sim-time data-work-output hidden aria-hidden="true">0.000</output>
</form>
<script data-work-family-script="true">
{{FAMILY_SCRIPT}}
</script>
<script data-work-runtime-script="true">
(function(){
  var FIXED = 1/60;
  var MAX_DT = 0.05;
  function ensureCanvas(){
    var el = document.getElementById('work-canvas');
    if (el && typeof el.getContext === 'function') return el;
    el = document.createElement('canvas');
    el.id = 'work-canvas';
    el.width = 720;
    el.height = 220;
    el.setAttribute('data-work-canvas-repaired','true');
    var mount = document.querySelector('form[data-work-controls]') || document.body;
    if (mount && mount.parentNode) mount.parentNode.insertBefore(el, mount);
    else document.body.appendChild(el);
    return el;
  }
  var canvas = ensureCanvas();
  var ctx = null;
  try { ctx = canvas ? canvas.getContext('2d') : null; } catch (err) { ctx = null; }
  var running = false;
  var acc = 0;
  var last = 0;
  var simTime = 0;
  var raf = 0;
  function publishTime(){
    var clock = document.getElementById('work-sim-time');
    if (clock) clock.textContent = simTime.toFixed(3);
  }
  function paint(){
    publishTime();
    if (ctx && window.WorkFamily && WorkFamily.draw) WorkFamily.draw(ctx, canvas, simTime);
  }
  function resize(){
    if (!canvas) return;
    var rect = canvas.getBoundingClientRect();
    var w = Math.max(160, Math.floor(rect.width || 720));
    var h = Math.max(100, Math.floor(rect.height || 220));
    if (canvas.width !== w || canvas.height !== h){
      canvas.width = w;
      canvas.height = h;
    }
    if (!ctx){
      try { ctx = canvas.getContext('2d'); } catch (err) { ctx = null; }
    }
  }
  function frame(now){
    if (!last) last = now;
    var raw = (now - last) / 1000;
    last = now;
    if (running){
      acc += Math.min(MAX_DT, Math.max(0, raw));
      while (acc + 1e-12 >= FIXED){
        acc -= FIXED;
        simTime += FIXED;
        if (window.WorkFamily && WorkFamily.step) WorkFamily.step(FIXED, simTime);
      }
    }
    paint();
    raf = requestAnimationFrame(frame);
  }
  window.WorkRuntime = {
    start: function(){
      running = true;
      if (window.WorkFamily && WorkFamily.onStart) WorkFamily.onStart();
      publishTime();
    },
    pause: function(){ running = false; },
    reset: function(){
      running = false; acc = 0; last = 0; simTime = 0;
      if (window.WorkFamily && WorkFamily.reset) WorkFamily.reset();
      paint();
    },
    isRunning: function(){ return running; },
    simTime: function(){ return simTime; },
    applyParams: function(){
      if (window.WorkFamily && WorkFamily.applyParams) WorkFamily.applyParams();
      paint();
    }
  };
  var startBtn = document.getElementById('btn-start');
  var pauseBtn = document.getElementById('btn-pause');
  var resetBtn = document.getElementById('btn-reset');
  if (startBtn) startBtn.addEventListener('click', function(){ WorkRuntime.start(); });
  if (pauseBtn) pauseBtn.addEventListener('click', function(){ WorkRuntime.pause(); });
  if (resetBtn) resetBtn.addEventListener('click', function(){ WorkRuntime.reset(); });
  document.querySelectorAll('[data-work-param]').forEach(function(el){
    el.addEventListener('input', function(){ WorkRuntime.applyParams(); });
    el.addEventListener('change', function(){ WorkRuntime.applyParams(); });
  });
  if (window.ResizeObserver && canvas){
    new ResizeObserver(function(){ resize(); paint(); }).observe(canvas.parentNode || canvas);
  }
  window.addEventListener('keydown', function(ev){
    if (ev.key === ' '){ ev.preventDefault(); running ? WorkRuntime.pause() : WorkRuntime.start(); }
    if (ev.key === 'r' || ev.key === 'R') WorkRuntime.reset();
  });
  resize();
  if (window.WorkFamily && WorkFamily.reset) WorkFamily.reset();
  paint();
  raf = requestAnimationFrame(frame);
})();
</script>
</body>
</html>
"""
