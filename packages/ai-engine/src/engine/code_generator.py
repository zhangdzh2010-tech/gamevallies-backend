"""Stage 05: Code Generator – dual-path HTML5 game code generation.

Path A (template fill): confidence >= 0.8 → load template + fill parameters (~1-3s)
Path B (hybrid):        confidence 0.5-0.8 → template skeleton + LLM customisation
Path C (full LLM):      confidence < 0.5  → Claude generates complete HTML from GDD

Uses Claude Sonnet 4.5 for full generation and Claude Haiku for parameter filling.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

from ..api.models import GDD, GameSpec, GenerateCodeResult, IterationType
from ..config.settings import settings
from ..services.llm_client import LLMClient
from .template_engine import TemplateEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Three-layer prompt construction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_LAYER1 = """You are PlayForge GameEngine, an expert HTML5 game developer.

OUTPUT FORMAT:
- Return ONLY a single complete HTML file (<!DOCTYPE html> ... </html>)
- No markdown code fences, no explanations, no extra text
- Inline all CSS and JavaScript inside the HTML

HARD RULES:
- Single self-contained file, zero external dependencies
- Use Canvas 2D API (no WebGL, no libraries)
- Touch-friendly: implement touchstart/touchmove/touchend events
- Target 60fps with requestAnimationFrame game loop
- Maximum 500 lines of code
- ES2017 syntax only
- FORBIDDEN APIs: eval, Function(), import, require, fetch, XMLHttpRequest, WebSocket, localStorage, document.cookie, document.write"""

PLATFORM_PROMPT_WECHAT = """PLATFORM: WeChat WebView
- Max file size: 300 KB
- Input: touch events only (no keyboard)
- Canvas: single canvas element, id="gameCanvas"
- On game over, call: window.parent?.postMessage({type:'game_over',score:SCORE},'*')"""

PLATFORM_PROMPT_STANDARD = """PLATFORM: Standard H5 Mobile Browser
- Max file size: 500 KB
- Input: touch + mouse fallback
- Canvas: single canvas element, id="gameCanvas" """

GAME_DESIGN_PROMPT_TEMPLATE = """GAME DESIGN DOCUMENT:

Game Type: {game_type}
Theme: {theme} | Art Style: {art_style}
Color Palette: {palette}

Canvas: {canvas_w}×{canvas_h}px, DPR adaptive, target 60fps

Player: speed={player_speed}px/frame, hitbox={hitbox_ratio}x
Obstacle/Spawn: base_speed={obstacle_speed}, interval={spawn_interval}ms
Difficulty: {speed_formula}
Score: +{score_per_second}/s, +{score_per_collect} per collectible
Lives: {lives} | Expected survival: {expected_s}s

Win condition: {win_condition}
Lose condition: {lose_condition}

Entities:
{entities_desc}

Input mapping:
{input_map}

Game states: init → playing → [paused | game_over] → init

UI:
- Score: top-left at (16, 36)
- Lives: top-right
- Game Over overlay: centered, show score + "Tap to restart"

Implement the complete, playable game following every detail above."""

ITERATE_CLASSIFY_PROMPT = """Classify this user feedback into one category. Return ONLY the category name.

Categories:
- param_adjust: change a numeric value (speed, color, size, lives, score)
- element_change: add or remove a game element (new entity, background effect, UI element)
- mechanic_change: change how the game works (new ability, different win condition, gameplay rule)
- major_overhaul: fundamentally different game type or complete redesign

Feedback: "{feedback}"

Category:"""

PARAM_ADJUST_PROMPT = """You are editing HTML5 game code. The user wants to change a parameter.
Apply ONLY the requested parameter change. Keep everything else identical.

User feedback: {feedback}

Current code:
{code}

Return ONLY the complete updated HTML file with no extra text."""

ELEMENT_CHANGE_PROMPT = """You are editing HTML5 game code. Add or remove one game element as requested.
Make the minimal change needed. Keep the rest of the code identical.

User feedback: {feedback}

Current code:
{code}

Return ONLY the complete updated HTML file with no extra text."""

MECHANIC_CHANGE_PROMPT = """You are editing HTML5 game code. Modify the game mechanics as requested.
You may rewrite the relevant section(s) of the code. Keep the rest unchanged.

User feedback: {feedback}
Conversation history: {history}

Current code:
{code}

Return ONLY the complete updated HTML file with no extra text."""


class CodeGenerator:
    """Stage 05: Dual-path HTML5 game code generator."""

    def __init__(self, llm_mode: str = "mock") -> None:
        self.llm_mode = llm_mode
        self.template_engine = TemplateEngine()
        self._client = LLMClient()

    # ------------------------------------------------------------------
    # Main generation entry point
    # ------------------------------------------------------------------

    async def generate(
        self,
        spec: GameSpec,
        gdd: GDD,
        template_id: Optional[str] = None,
        confidence: float = 0.0,
        description: str = "",
    ) -> GenerateCodeResult:
        start = time.time()

        if self.llm_mode == "mock" or not self._client.is_enabled():
            html = self._mock_generate(spec)
            strategy = "mock"
        elif confidence >= settings.TEMPLATE_CONFIDENCE_THRESHOLD and template_id:
            html = self._template_fill(spec, gdd, template_id)
            strategy = "template"
        elif confidence >= settings.HYBRID_CONFIDENCE_THRESHOLD and template_id:
            html = await self._hybrid_generate(spec, gdd, template_id)
            strategy = "hybrid"
        else:
            html = await self._llm_generate(spec, gdd, description=description)
            strategy = "llm"

        elapsed = int((time.time() - start) * 1000)
        return GenerateCodeResult(
            html_code=html,
            strategy=strategy,
            template_id=template_id,
            generation_time_ms=elapsed,
            code_size_bytes=len(html.encode("utf-8")),
        )

    # ------------------------------------------------------------------
    # Path A: Template fill
    # ------------------------------------------------------------------

    def _template_fill(self, spec: GameSpec, gdd: GDD, template_id: str) -> str:
        try:
            return self.template_engine.generate(spec, template_id)
        except Exception as e:
            logger.warning(f"Template fill failed ({e}), falling back to mock")
            return self._mock_generate(spec)

    # ------------------------------------------------------------------
    # Path B: Hybrid (template skeleton + LLM customisation)
    # ------------------------------------------------------------------

    async def _hybrid_generate(self, spec: GameSpec, gdd: GDD, template_id: str) -> str:
        skeleton = self._template_fill(spec, gdd, template_id)

        prompt = (
            f"Here is a base game template:\n\n{skeleton}\n\n"
            f"Customise it to match this additional game design:\n"
            f"Theme: {spec.visual_style.theme}, Art: {spec.visual_style.art_style}\n"
            f"Win condition: {spec.rules.win_condition}\n"
            f"Entities: {[e.name for e in spec.entities]}\n"
            f"Return ONLY the complete modified HTML file."
        )
        try:
            text = await self._client.complete(
                model=self._client.model_for(),
                max_tokens=4096,
                system=SYSTEM_PROMPT_LAYER1,
                messages=[{"role": "user", "content": prompt}],
            )
            return _extract_html(text)
        except Exception as e:
            logger.warning(f"Hybrid LLM failed ({e}), using skeleton")
            return skeleton

    # ------------------------------------------------------------------
    # Path C: Full LLM generation
    # ------------------------------------------------------------------

    async def _llm_generate(self, spec: GameSpec, gdd: GDD, description: str = "") -> str:
        # Use user's original description directly for better results
        if description:
            full_prompt = (
                f"用户需求：{description}\n\n"
                f"{PLATFORM_PROMPT_STANDARD}\n\n"
                f"请根据用户需求生成完整的 HTML5 游戏。游戏必须完整可玩、触屏操作、有计分系统。"
            )
        else:
            # Fallback to spec-based prompt
            entities_desc = "\n".join(
                f"  - {e.name} ({e.role}): shape={e.shape or 'auto'}, color={e.color or 'auto'}"
                for e in spec.entities
            )
            input_map_str = "\n".join(
                f"  {k} → {v}" for k, v in gdd.input_map.items()
            )
            full_prompt = GAME_DESIGN_PROMPT_TEMPLATE.format(
                game_type=spec.game_type,
                theme=spec.visual_style.theme,
                art_style=spec.visual_style.art_style,
                palette=", ".join(spec.visual_style.palette),
                canvas_w=gdd.canvas.width, canvas_h=gdd.canvas.height,
                player_speed=gdd.numerics.player_speed,
                hitbox_ratio=gdd.collision.hitbox_ratio,
                obstacle_speed=gdd.numerics.base_obstacle_speed,
                spawn_interval=gdd.numerics.spawn_interval_ms,
                speed_formula=gdd.numerics.speed_formula,
                score_per_second=gdd.numerics.score_per_second,
                score_per_collect=gdd.numerics.score_per_collect,
                lives=spec.rules.lives,
                expected_s=gdd.numerics.expected_survival_s,
                win_condition=spec.rules.win_condition,
                lose_condition=spec.rules.lose_condition,
                entities_desc=entities_desc,
                input_map=input_map_str,
            ) + f"\n\n{PLATFORM_PROMPT_STANDARD}"

        try:
            text = await self._client.complete(
                model=self._client.model_for(),
                max_tokens=8192,
                system=SYSTEM_PROMPT_LAYER1,
                messages=[{"role": "user", "content": full_prompt}],
            )
            return _extract_html(text)
        except Exception as e:
            logger.error(f"Full LLM generation failed: {e}")
            return self._mock_generate(spec)

    # ------------------------------------------------------------------
    # Stage 07: Iteration
    # ------------------------------------------------------------------

    async def iterate(
        self,
        current_code: str,
        feedback: str,
        conversation: List[dict],
    ) -> Tuple[str, IterationType]:
        """Classify feedback and apply minimal incremental change."""
        if self.llm_mode == "mock" or not self._client.is_enabled():
            return self._mock_iterate(current_code, feedback), IterationType.param_adjust

        iter_type = await self._classify_iteration(feedback)

        if iter_type == IterationType.param_adjust:
            updated = self._param_adjust(current_code, feedback)
            if updated != current_code:
                return updated, iter_type
            # Fallback to LLM if regex didn't match
            iter_type = IterationType.element_change

        updated = await self._llm_iterate(current_code, feedback, conversation, iter_type)
        return updated, iter_type

    async def _classify_iteration(self, feedback: str) -> IterationType:
        try:
            text = await self._client.complete(
                model=self._client.model_for(fast=True),
                max_tokens=20,
                messages=[{
                    "role": "user",
                    "content": ITERATE_CLASSIFY_PROMPT.format(feedback=feedback),
                }],
            )
            label = text.strip().lower()
            for it in IterationType:
                if it.value in label:
                    return it
        except Exception:
            pass
        return IterationType.element_change

    def _param_adjust(self, code: str, feedback: str) -> str:
        """Zero-token regex parameter replacement."""
        fb = feedback.lower()

        # Speed
        if "快" in fb or "faster" in fb or "速度" in fb:
            code = re.sub(r"(player\.speed\s*=\s*)(\d+\.?\d*)", lambda m: m.group(1) + str(round(float(m.group(2)) * 1.5, 1)), code, count=1)
        if "慢" in fb or "slower" in fb:
            code = re.sub(r"(player\.speed\s*=\s*)(\d+\.?\d*)", lambda m: m.group(1) + str(round(float(m.group(2)) * 0.7, 1)), code, count=1)

        # Lives
        m = re.search(r"(\d+)\s*(命|lives|生命)", fb)
        if m:
            lives = m.group(1)
            code = re.sub(r"(lives\s*[:=]\s*)\d+", lambda _: _.group(1) + lives, code, count=2)

        # Color: simple primary color swap
        if "红色" in fb or "red" in fb:
            code = re.sub(r"#6366f1", "#ef4444", code)
        if "绿色" in fb or "green" in fb:
            code = re.sub(r"#6366f1", "#22c55e", code)
        if "蓝色" in fb or "blue" in fb:
            code = re.sub(r"#6366f1", "#3b82f6", code)

        return code

    async def _llm_iterate(
        self,
        code: str,
        feedback: str,
        conversation: List[dict],
        iter_type: IterationType,
    ) -> str:
        history_text = "\n".join(
            f"{m.get('role','user')}: {m.get('content','')}" for m in conversation[-4:]
        )

        if iter_type == IterationType.element_change:
            prompt = ELEMENT_CHANGE_PROMPT.format(feedback=feedback, code=code)
        else:
            prompt = MECHANIC_CHANGE_PROMPT.format(
                feedback=feedback, history=history_text, code=code
            )

        try:
            text = await self._client.complete(
                model=self._client.model_for(),
                max_tokens=8192,
                system=SYSTEM_PROMPT_LAYER1,
                messages=[{"role": "user", "content": prompt}],
            )
            return _extract_html(text)
        except Exception as e:
            logger.error(f"LLM iterate failed: {e}")
            return code

    # ------------------------------------------------------------------
    # Mock implementations
    # ------------------------------------------------------------------

    def _mock_generate(self, spec: GameSpec) -> str:
        """Return a basic but functional game based on game_type."""
        colors = spec.visual_style
        bg = colors.palette[0] if colors.palette else "#08080d"
        primary = colors.palette[1] if len(colors.palette) > 1 else "#6366f1"
        secondary = colors.palette[3] if len(colors.palette) > 3 else "#f43f5e"
        lives = spec.rules.lives

        if spec.game_type == "dodge":
            return _DODGE_TEMPLATE.format(bg=bg, primary=primary, secondary=secondary, lives=lives)
        if spec.game_type in ("runner", "platformer"):
            return _RUNNER_TEMPLATE.format(bg=bg, primary=primary, secondary=secondary, lives=lives)
        # Fallback
        return _DODGE_TEMPLATE.format(bg=bg, primary=primary, secondary=secondary, lives=lives)

    def _mock_iterate(self, code: str, feedback: str) -> str:
        return self._param_adjust(code, feedback)


# ---------------------------------------------------------------------------
# HTML extraction helper
# ---------------------------------------------------------------------------

def _extract_html(text: str) -> str:
    """Extract clean HTML from LLM output (strip markdown fences if present)."""
    # Remove ``` fences
    text = re.sub(r"```(?:html)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    # Find first <!DOCTYPE or <html
    m = re.search(r"(<!DOCTYPE|<html)", text, re.IGNORECASE)
    if m:
        text = text[m.start():]
    return text.strip()


# ---------------------------------------------------------------------------
# Minimal mock templates
# ---------------------------------------------------------------------------

_DODGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>PlayForge Game</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:{bg};display:flex;justify-content:center;align-items:center;height:100vh;overflow:hidden;}}
canvas{{display:block;touch-action:none;}}
</style>
</head>
<body>
<canvas id="gameCanvas"></canvas>
<script>
const canvas=document.getElementById('gameCanvas');
const ctx=canvas.getContext('2d');
canvas.width=Math.min(window.innerWidth,420);
canvas.height=Math.min(window.innerHeight,600);
const W=canvas.width,H=canvas.height;
const C={{bg:'{bg}',p:'{primary}',s:'{secondary}',t:'#fff'}};
const game={{score:0,lives:{lives},over:false,elapsed:0,lastTime:0}};
const player={{x:W/2,y:H-80,w:36,h:36,vx:0,speed:8}};
let obstacles=[],collectibles=[],frameId;
class Obstacle{{
  constructor(){{this.x=Math.random()*(W-28);this.y=-30;this.w=28;this.h=28;this.vy=3+Math.random()*2;}}
  update(){{this.y+=this.vy+(game.elapsed*0.015);}}
  draw(){{ctx.fillStyle=C.s;ctx.fillRect(this.x,this.y,this.w,this.h);}}
}}
class Collectible{{
  constructor(){{this.x=Math.random()*(W-20);this.y=-20;this.r=10;this.vy=2;}}
  update(){{this.y+=this.vy;}}
  draw(){{ctx.fillStyle=C.p;ctx.beginPath();ctx.arc(this.x,this.y,this.r,0,Math.PI*2);ctx.fill();}}
}}
function hit(a,b){{return a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y;}}
function hitCircle(r,b){{return r.x<b.x+b.r+r.w/2&&r.x+r.w>b.x-b.r&&r.y<b.y+b.r+r.h/2&&r.y+r.h>b.y-b.r;}}
function restart(){{game.score=0;game.lives={lives};game.over=false;game.elapsed=0;obstacles=[];collectibles=[];player.x=W/2;player.vx=0;}}
function update(ts){{
  if(game.over)return;
  const dt=(ts-game.lastTime)/1000;
  game.lastTime=ts;
  game.elapsed+=dt;
  game.score+=dt;
  player.x+=player.vx;
  player.x=Math.max(0,Math.min(W-player.w,player.x));
  if(Math.random()<0.025)obstacles.push(new Obstacle());
  if(Math.random()<0.010)collectibles.push(new Collectible());
  for(let i=obstacles.length-1;i>=0;i--){{
    obstacles[i].update();
    if(obstacles[i].y>H){{obstacles.splice(i,1);continue;}}
    if(hit(player,obstacles[i])){{obstacles.splice(i,1);game.lives--;if(game.lives<=0){{game.over=true;window.parent?.postMessage({{type:'game_over',score:Math.floor(game.score)}},'*');}}}}
  }}
  for(let i=collectibles.length-1;i>=0;i--){{
    collectibles[i].update();
    if(collectibles[i].y>H){{collectibles.splice(i,1);continue;}}
    if(hitCircle(player,collectibles[i])){{collectibles.splice(i,1);game.score+=10;}}
  }}
}}
function draw(){{
  ctx.fillStyle='#000';ctx.fillRect(0,0,W,H);
  ctx.fillStyle=C.p;ctx.fillRect(player.x,player.y,player.w,player.h);
  obstacles.forEach(o=>o.draw());
  collectibles.forEach(c=>c.draw());
  ctx.fillStyle=C.t;ctx.font='bold 18px Arial';
  ctx.textAlign='left';ctx.fillText('Score: '+Math.floor(game.score),16,32);
  ctx.textAlign='right';ctx.fillText('Lives: '+game.lives,W-16,32);
  if(game.over){{
    ctx.fillStyle='rgba(0,0,0,0.75)';ctx.fillRect(0,0,W,H);
    ctx.fillStyle=C.s;ctx.textAlign='center';ctx.font='bold 42px Arial';
    ctx.fillText('GAME OVER',W/2,H/2-40);
    ctx.font='24px Arial';ctx.fillStyle=C.t;
    ctx.fillText('Score: '+Math.floor(game.score),W/2,H/2+10);
    ctx.fillText('Tap to restart',W/2,H/2+60);
  }}
}}
function loop(ts){{update(ts);draw();frameId=requestAnimationFrame(loop);}}
canvas.addEventListener('touchmove',e=>{{e.preventDefault();const t=e.touches[0];const r=canvas.getBoundingClientRect();const tx=t.clientX-r.left;player.vx=tx<player.x+player.w/2?-player.speed:player.speed;}},{{passive:false}});
canvas.addEventListener('touchend',()=>{{player.vx=0;}});
canvas.addEventListener('touchstart',e=>{{if(game.over)restart();}});
canvas.addEventListener('click',()=>{{if(game.over)restart();}});
game.lastTime=performance.now();
requestAnimationFrame(loop);
</script>
</body>
</html>"""

_RUNNER_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>PlayForge Game</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:{bg};display:flex;justify-content:center;align-items:center;height:100vh;overflow:hidden;}}
canvas{{display:block;touch-action:none;}}
</style>
</head>
<body>
<canvas id="gameCanvas"></canvas>
<script>
const canvas=document.getElementById('gameCanvas');
const ctx=canvas.getContext('2d');
canvas.width=Math.min(window.innerWidth,420);
canvas.height=Math.min(window.innerHeight,600);
const W=canvas.width,H=canvas.height;
const C={{bg:'{bg}',p:'{primary}',s:'{secondary}',t:'#fff'}};
const GROUND=H-60;
const game={{score:0,over:false,speed:4,elapsed:0,lastTime:0}};
const player={{x:80,y:GROUND-40,w:32,h:40,vy:0,onGround:true,jumpPower:14,gravity:0.6}};
let obstacles=[],frameId;
class Block{{
  constructor(){{this.x=W+20;this.y=GROUND-50;this.w=28+Math.random()*20;this.h=50+Math.random()*30;this.y=GROUND-this.h;}}
  update(){{this.x-=game.speed;}}
  draw(){{ctx.fillStyle=C.s;ctx.fillRect(this.x,this.y,this.w,this.h);}}
}}
function jump(){{if(player.onGround){{player.vy=-player.jumpPower;player.onGround=false;}}}}
function restart(){{game.score=0;game.over=false;game.speed=4;game.elapsed=0;obstacles=[];player.y=GROUND-player.h;player.vy=0;player.onGround=true;}}
function update(ts){{
  if(game.over)return;
  const dt=(ts-game.lastTime)/1000;game.lastTime=ts;game.elapsed+=dt;
  game.score+=dt*10;game.speed=4+game.elapsed*0.02;
  player.vy+=player.gravity;player.y+=player.vy;
  if(player.y>=GROUND-player.h){{player.y=GROUND-player.h;player.vy=0;player.onGround=true;}}
  if(Math.random()<0.018)obstacles.push(new Block());
  for(let i=obstacles.length-1;i>=0;i--){{
    obstacles[i].update();
    if(obstacles[i].x+obstacles[i].w<0){{obstacles.splice(i,1);continue;}}
    const o=obstacles[i];
    if(player.x<o.x+o.w&&player.x+player.w>o.x&&player.y<o.y+o.h&&player.y+player.h>o.y){{game.over=true;window.parent?.postMessage({{type:'game_over',score:Math.floor(game.score)}},'*');}}
  }}
}}
function draw(){{
  ctx.fillStyle='#000';ctx.fillRect(0,0,W,H);
  ctx.fillStyle='#333';ctx.fillRect(0,GROUND,W,H-GROUND);
  ctx.fillStyle=C.p;ctx.fillRect(player.x,player.y,player.w,player.h);
  obstacles.forEach(o=>o.draw());
  ctx.fillStyle=C.t;ctx.font='bold 18px Arial';ctx.textAlign='left';
  ctx.fillText('Score: '+Math.floor(game.score),16,32);
  if(game.over){{
    ctx.fillStyle='rgba(0,0,0,0.75)';ctx.fillRect(0,0,W,H);
    ctx.fillStyle=C.s;ctx.textAlign='center';ctx.font='bold 42px Arial';
    ctx.fillText('GAME OVER',W/2,H/2-40);
    ctx.font='24px Arial';ctx.fillStyle=C.t;
    ctx.fillText('Score: '+Math.floor(game.score),W/2,H/2+10);
    ctx.fillText('Tap to restart',W/2,H/2+60);
  }}
}}
function loop(ts){{update(ts);draw();frameId=requestAnimationFrame(loop);}}
canvas.addEventListener('touchstart',e=>{{e.preventDefault();if(game.over)restart();else jump();}},{{passive:false}});
canvas.addEventListener('click',()=>{{if(game.over)restart();else jump();}});
game.lastTime=performance.now();
requestAnimationFrame(loop);
</script>
</body>
</html>"""
