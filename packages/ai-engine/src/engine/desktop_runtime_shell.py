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
</form>
<script>
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
  function paint(){
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
    start: function(){ running = true; if (window.WorkFamily && WorkFamily.onStart) WorkFamily.onStart(); },
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
  {{FAMILY_SCRIPT}}
  resize();
  if (window.WorkFamily && WorkFamily.reset) WorkFamily.reset();
  paint();
  raf = requestAnimationFrame(frame);
})();
</script>
</body>
</html>
"""
