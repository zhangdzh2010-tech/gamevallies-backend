/**
 * 种子游戏业务初始化脚本
 *
 * 将 17 款精选 HTML5 游戏（6 款桌面端 + 11 款移动端）写入 MySQL 数据库，
 * 适配当前 Prisma / MySQL 架构（GameBundle 存储在 MySQL，不再依赖 MongoDB）。
 *
 * 特性：
 *  - 以 slug 为 key 做 upsert，可重复执行（幂等）
 *  - 自动创建 seed_creator 系统用户（若不存在）
 *  - 同步写入 Game + GameBundle 两张表
 *
 * 运行：
 *   DATABASE_URL=... npx ts-node scripts/init-games.ts
 *   或在 package.json 中添加：
 *   "init:games": "ts-node scripts/init-games.ts"
 */

import { PrismaClient } from '@prisma/client';
import bcrypt from 'bcryptjs';
import { randomUUID } from 'crypto';
import { readFileSync } from 'fs';
import { join } from 'path';

const GAMES_DIR = join(process.cwd(), 'scripts', 'games');
function loadGameFile(filename: string): string {
  return readFileSync(join(GAMES_DIR, filename), 'utf-8');
}

const prisma = new PrismaClient({
  datasources: { db: { url: process.env.DATABASE_URL } },
});

// ============================================================
// 桌面端游戏 HTML（来自 seed-games.mjs，适配 MySQL 架构）
// ============================================================

const SNAKE_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no,viewport-fit=cover">
<title>贪吃蛇</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; -webkit-tap-highlight-color: transparent; user-select: none; -webkit-user-select: none; }
body { background: #1a1a2e; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); font-family: 'Arial', sans-serif; color: #fff; }
h1 { font-size: clamp(1.2rem, 5vw, 2rem); margin-bottom: 8px; color: #16c79a; text-shadow: 0 0 20px #16c79a; }
#score-board { display: flex; gap: clamp(16px, 5vw, 40px); margin-bottom: 10px; font-size: clamp(0.85rem, 3vw, 1.1rem); }
.score-item { text-align: center; }
.score-item span { display: block; font-size: clamp(1.2rem, 5vw, 1.8rem); font-weight: bold; color: #f5a623; }
canvas { border: 3px solid #16c79a; border-radius: 8px; box-shadow: 0 0 30px rgba(22,199,154,0.4); }
#message { margin-top: 8px; font-size: clamp(0.8rem, 3vw, 1.1rem); color: #aaa; text-align: center; }
#btn { margin-top: 8px; padding: 12px 30px; min-height: 48px; background: #16c79a; color: #1a1a2e; border: none; border-radius: 25px; font-size: clamp(0.9rem, 3.5vw, 1rem); font-weight: bold; cursor: pointer; transition: transform 0.1s; }
#btn:active { transform: scale(0.97); }
#dpad { display: flex; flex-direction: column; align-items: center; margin-top: 10px; gap: 2px; }
.dpad-row { display: flex; gap: 2px; }
.dpad-btn { width: 52px; height: 52px; background: rgba(22,199,154,0.2); border: 2px solid #16c79a; border-radius: 8px; color: #16c79a; font-size: 1.4rem; display: flex; align-items: center; justify-content: center; cursor: pointer; -webkit-tap-highlight-color: transparent; }
.dpad-btn:active { background: rgba(22,199,154,0.5); }
</style>
</head>
<body>
<h1>🐍 贪吃蛇</h1>
<div id="score-board">
  <div class="score-item">得分<span id="score">0</span></div>
  <div class="score-item">最高<span id="best">0</span></div>
  <div class="score-item">长度<span id="len">3</span></div>
</div>
<canvas id="c"></canvas>
<div id="message">按空格键 / 点击开始</div>
<button id="btn" onclick="startGame()">开始游戏</button>
<div id="dpad">
  <div><button class="dpad-btn" id="btn-up">↑</button></div>
  <div class="dpad-row"><button class="dpad-btn" id="btn-left">←</button><button class="dpad-btn" id="btn-down">↓</button><button class="dpad-btn" id="btn-right">→</button></div>
</div>
<script>
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const GRID = 20;
const SZ = Math.min(window.innerWidth - 16, window.innerHeight - 260, 360);
canvas.width = SZ; canvas.height = SZ;
const cellSz = SZ / GRID;
let snake, dir, food, score, best = 0, running = false, interval;
function startGame() {
  snake = [{x:10,y:10},{x:9,y:10},{x:8,y:10}];
  dir = {x:1,y:0}; score = 0; running = true;
  placeFood(); update(); clearInterval(interval);
  interval = setInterval(loop, 130);
  document.getElementById('message').textContent = '方向键 / WASD 控制';
  document.getElementById('btn').textContent = '重新开始';
}
function placeFood() {
  do { food = {x:Math.floor(Math.random()*GRID), y:Math.floor(Math.random()*GRID)}; }
  while (snake.some(s=>s.x===food.x&&s.y===food.y));
}
function setDir(d) { if (d && !(d.x===-dir.x && d.y===-dir.y)) dir = d; }
function loop() {
  const head = {x: snake[0].x + dir.x, y: snake[0].y + dir.y};
  if (head.x<0||head.x>=GRID||head.y<0||head.y>=GRID||snake.some(s=>s.x===head.x&&s.y===head.y)) {
    clearInterval(interval); running = false;
    document.getElementById('message').textContent = \`游戏结束！得分: \${score}\`;
    return;
  }
  snake.unshift(head);
  if (head.x===food.x&&head.y===food.y) {
    score += 10; if(score>best) best = score; placeFood();
  } else snake.pop();
  update();
}
function update() {
  ctx.fillStyle = '#0f0f23'; ctx.fillRect(0, 0, SZ, SZ);
  ctx.strokeStyle = 'rgba(255,255,255,0.03)';
  for(let i=0;i<GRID;i++){ctx.beginPath();ctx.moveTo(i*cellSz,0);ctx.lineTo(i*cellSz,SZ);ctx.stroke();ctx.beginPath();ctx.moveTo(0,i*cellSz);ctx.lineTo(SZ,i*cellSz);ctx.stroke();}
  ctx.fillStyle = '#f5a623'; ctx.shadowColor = '#f5a623'; ctx.shadowBlur = 15;
  ctx.beginPath(); ctx.arc(food.x*cellSz+cellSz/2, food.y*cellSz+cellSz/2, cellSz/2-2, 0, Math.PI*2); ctx.fill();
  ctx.shadowBlur = 0;
  snake.forEach((s,i) => {
    const ratio = 1 - i/snake.length * 0.7;
    ctx.fillStyle = \`rgba(22, \${Math.floor(199*ratio)}, 154, \${ratio})\`;
    ctx.beginPath(); ctx.roundRect(s.x*cellSz+1, s.y*cellSz+1, cellSz-2, cellSz-2, 4); ctx.fill();
  });
  document.getElementById('score').textContent = score;
  document.getElementById('best').textContent = best;
  document.getElementById('len').textContent = snake.length;
}
document.addEventListener('keydown', e => {
  if (!running && e.code === 'Space') { startGame(); return; }
  const map = {ArrowUp:{x:0,y:-1},ArrowDown:{x:0,y:1},ArrowLeft:{x:-1,y:0},ArrowRight:{x:1,y:0},KeyW:{x:0,y:-1},KeyS:{x:0,y:1},KeyA:{x:-1,y:0},KeyD:{x:1,y:0}};
  const d = map[e.code];
  if (d) { setDir(d); e.preventDefault(); }
});
document.getElementById('btn-up').addEventListener('touchstart',e=>{e.preventDefault();if(!running)startGame();else setDir({x:0,y:-1});},{passive:false});
document.getElementById('btn-down').addEventListener('touchstart',e=>{e.preventDefault();if(!running)startGame();else setDir({x:0,y:1});},{passive:false});
document.getElementById('btn-left').addEventListener('touchstart',e=>{e.preventDefault();if(!running)startGame();else setDir({x:-1,y:0});},{passive:false});
document.getElementById('btn-right').addEventListener('touchstart',e=>{e.preventDefault();if(!running)startGame();else setDir({x:1,y:0});},{passive:false});
let touchStartX=0,touchStartY=0;
canvas.addEventListener('touchstart',e=>{e.preventDefault();const t=e.touches[0];touchStartX=t.clientX;touchStartY=t.clientY;},{passive:false});
canvas.addEventListener('touchend',e=>{
  e.preventDefault();
  const dx=e.changedTouches[0].clientX-touchStartX,dy=e.changedTouches[0].clientY-touchStartY;
  if(Math.abs(dx)<10&&Math.abs(dy)<10){if(!running)startGame();return;}
  if(Math.abs(dx)>Math.abs(dy)){setDir(dx>0?{x:1,y:0}:{x:-1,y:0});}else{setDir(dy>0?{x:0,y:1}:{x:0,y:-1});}
},{passive:false});
update();
</script>
</body>
</html>`;

const BREAKOUT_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no,viewport-fit=cover">
<title>打砖块</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; -webkit-tap-highlight-color: transparent; user-select: none; -webkit-user-select: none; }
body { background: #0d1117; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); font-family: Arial, sans-serif; color: #fff; }
h1 { font-size: clamp(1.2rem, 5vw, 2rem); margin-bottom: 8px; color: #58a6ff; }
#info { display: flex; gap: clamp(16px, 5vw, 40px); margin-bottom: 10px; font-size: clamp(0.85rem, 3vw, 1rem); }
.v { font-size: clamp(1.2rem, 5vw, 1.6rem); font-weight: bold; color: #ffa657; }
canvas { border: 2px solid #30363d; border-radius: 6px; }
#msg { margin-top: 10px; font-size: clamp(0.8rem, 3vw, 1rem); color: #8b949e; text-align: center; }
</style>
</head>
<body>
<h1>🧱 打砖块</h1>
<div id="info">
  <div>得分 <span class="v" id="sc">0</span></div>
  <div>生命 <span class="v" id="lv">3</span></div>
  <div>关卡 <span class="v" id="lv2">1</span></div>
</div>
<canvas id="c"></canvas>
<div id="msg">点击/触摸开始 · 移动控制挡板</div>
<script>
const C = document.getElementById('c'), ctx = C.getContext('2d');
const CW = Math.min(window.innerWidth - 16, 480);
const CH = Math.round(CW * 400 / 480);
C.width = CW; C.height = CH;
const scale = CW / 480;
let bx=CW/2, by=CH-60*scale, bdx=3.5*scale, bdy=-3.5*scale;
let px=CW/2-40*scale, pw=80*scale, ph=10*scale, py=CH-20*scale;
let lives=3, score=0, level=1, running=false, won=false, lost=false;
const COLS=10, ROWS=5;
const COLORS=['#ff6b6b','#ffa657','#ffd700','#7ce38b','#58a6ff','#d2a8ff'];
let bricks=[];
function initBricks(){
  bricks=[];
  for(let r=0;r<ROWS;r++) for(let c=0;c<COLS;c++)
    bricks.push({x:c*46*scale+5*scale,y:r*24*scale+40*scale,w:42*scale,h:20*scale,alive:true,color:COLORS[r%COLORS.length],hp:r<2?1:r<4?2:3});
}
function reset(){ bx=CW/2; by=CH-60*scale; const spd=(3.5+level*0.3)*scale; bdx=(Math.random()>0.5?1:-1)*spd; bdy=-spd; if(!won) initBricks(); won=false; lost=false; }
function start(){ running=true; reset(); loop(); }
C.addEventListener('mousemove',e=>{ const r=C.getBoundingClientRect(); px=e.clientX-r.left-pw/2; px=Math.max(0,Math.min(CW-pw,px)); });
C.addEventListener('click',()=>{ if(!running) start(); });
C.addEventListener('touchstart',e=>{e.preventDefault();if(!running)start();},{passive:false});
C.addEventListener('touchmove',e=>{ e.preventDefault(); const rect=C.getBoundingClientRect(); px=Math.max(0,Math.min(CW-pw,e.touches[0].clientX-rect.left-pw/2)); },{passive:false});
function loop(){
  if(!running) return;
  ctx.fillStyle='#0d1117'; ctx.fillRect(0,0,CW,CH);
  ctx.fillStyle='#58a6ff'; ctx.beginPath(); ctx.roundRect(px,py,pw,ph,5); ctx.fill();
  const grad=ctx.createRadialGradient(bx,by,2*scale,bx,by,8*scale);
  grad.addColorStop(0,'#fff'); grad.addColorStop(1,'#ffa657');
  ctx.fillStyle=grad; ctx.beginPath(); ctx.arc(bx,by,8*scale,0,Math.PI*2); ctx.fill();
  bricks.forEach(b=>{
    if(!b.alive) return;
    ctx.fillStyle=b.hp===1?b.color:b.hp===2?b.color+'cc':b.color+'88';
    ctx.beginPath(); ctx.roundRect(b.x,b.y,b.w,b.h,3); ctx.fill();
  });
  bx+=bdx; by+=bdy;
  if(bx<8*scale||bx>CW-8*scale) bdx=-bdx;
  if(by<8*scale) bdy=-bdy;
  if(by>CH){ lives--; document.getElementById('lv').textContent=lives; if(lives<=0){ running=false; return; } reset(); }
  if(by>py-8*scale&&by<py+ph&&bx>px-8*scale&&bx<px+pw+8*scale){ bdy=-Math.abs(bdy); const rel=(bx-(px+pw/2))/(pw/2); bdx=rel*5*scale; }
  bricks.forEach(b=>{
    if(!b.alive) return;
    if(bx>b.x-8*scale&&bx<b.x+b.w+8*scale&&by>b.y-8*scale&&by<b.y+b.h+8*scale){
      b.hp--; if(b.hp<=0){ b.alive=false; score+=10+level*5; document.getElementById('sc').textContent=score; }
      if(bx<b.x||bx>b.x+b.w) bdx=-bdx; else bdy=-bdy;
    }
  });
  if(bricks.every(b=>!b.alive)){ level++; document.getElementById('lv2').textContent=level; reset(); }
  requestAnimationFrame(loop);
}
initBricks(); ctx.fillStyle='#0d1117'; ctx.fillRect(0,0,CW,CH);
ctx.fillStyle='#58a6ff'; ctx.font='bold '+Math.round(22*scale)+'px Arial'; ctx.textAlign='center'; ctx.fillText('点击开始游戏',CW/2,CH/2); ctx.textAlign='left';
</script>
</body>
</html>`;

const MEMORY_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no,viewport-fit=cover">
<title>记忆翻牌</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; -webkit-tap-highlight-color: transparent; user-select: none; -webkit-user-select: none; }
body { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); font-family: Arial, sans-serif; color: #fff; }
h1 { font-size: clamp(1.2rem, 5vw, 1.8rem); margin-bottom: 8px; color: #e94560; }
#controls { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; justify-content: center; }
.diff-btn { padding: 8px 14px; min-height: 40px; border: 2px solid #e94560; background: transparent; color: #fff; border-radius: 20px; cursor: pointer; font-size: clamp(0.75rem, 3vw, 0.85rem); }
.diff-btn.active, .diff-btn:active { background: #e94560; }
#info { display: flex; gap: clamp(14px, 4vw, 30px); margin-bottom: 12px; font-size: clamp(0.85rem, 3vw, 1rem); }
.iv { font-size: clamp(1.1rem, 4.5vw, 1.5rem); font-weight: bold; color: #ffd700; }
#board { display: grid; gap: 6px; }
.card { border-radius: 10px; cursor: pointer; perspective: 600px; }
.card-inner { width: 100%; height: 100%; position: relative; transform-style: preserve-3d; transition: transform 0.4s; }
.card.flipped .card-inner { transform: rotateY(180deg); }
.card-front, .card-back { position: absolute; width: 100%; height: 100%; backface-visibility: hidden; border-radius: 10px; display: flex; align-items: center; justify-content: center; }
.card-front { background: linear-gradient(135deg, #0f3460, #16213e); border: 2px solid #e94560; }
.card-back { background: linear-gradient(135deg, #1a1a2e, #0f3460); border: 2px solid #ffd700; transform: rotateY(180deg); }
.card.matched .card-back { background: linear-gradient(135deg, #1a6b3c, #0f3460); border-color: #4caf50; }
#result { margin-top: 12px; font-size: clamp(0.9rem, 3.5vw, 1.1rem); color: #4caf50; font-weight: bold; }
</style>
</head>
<body>
<h1>🃏 记忆翻牌</h1>
<div id="controls">
  <button class="diff-btn active" onclick="setDiff(3,3,'简单')">简单 3×3</button>
  <button class="diff-btn" onclick="setDiff(4,4,'普通')">普通 4×4</button>
  <button class="diff-btn" onclick="setDiff(5,4,'困难')">困难 5×4</button>
  <button class="diff-btn" onclick="setDiff(6,5,'极难')">极难 6×5</button>
</div>
<div id="info">
  <div>步数 <span class="iv" id="moves">0</span></div>
  <div>配对 <span class="iv" id="pairs">0</span></div>
  <div>时间 <span class="iv" id="time">0s</span></div>
</div>
<div id="board"></div>
<div id="result"></div>
<script>
const EMOJIS = ['🎮','🎯','🚀','⭐','🌈','🎸','🦋','🐉','🌺','💎','🎪','🏆','🎭','🦄','🌙'];
let cols=3, rows=3, cards=[], flipped=[], moves=0, pairs=0, total=0, lock=false, timer=null, sec=0;
const cardSize = Math.floor((Math.min(window.innerWidth - 40, 360)) / 4) - 6;
function setDiff(c, r, name) {
  cols=c; rows=r;
  document.querySelectorAll('.diff-btn').forEach(b=>b.classList.toggle('active',b.textContent.startsWith(name)));
  init();
}
function init() {
  clearInterval(timer); sec=0; moves=0; pairs=0; flipped=[]; lock=false;
  document.getElementById('moves').textContent=0; document.getElementById('pairs').textContent=0;
  document.getElementById('time').textContent='0s'; document.getElementById('result').textContent='';
  total = Math.floor(cols*rows/2);
  const emojis = EMOJIS.slice(0,total);
  cards = shuffle([...emojis,...emojis]).slice(0,cols*rows);
  render();
  timer = setInterval(()=>{ sec++; document.getElementById('time').textContent=sec+'s'; },1000);
}
function shuffle(arr) { for(let i=arr.length-1;i>0;i--){ const j=Math.floor(Math.random()*(i+1)); [arr[i],arr[j]]=[arr[j],arr[i]]; } return arr; }
function render() {
  const board = document.getElementById('board');
  board.style.gridTemplateColumns = \`repeat(\${cols}, \${cardSize}px)\`;
  board.innerHTML = '';
  cards.forEach((emoji,i)=>{
    const card = document.createElement('div');
    card.className='card';
    card.style.width=cardSize+'px'; card.style.height=cardSize+'px';
    card.innerHTML=\`<div class="card-inner"><div class="card-front" style="font-size:\${Math.round(cardSize*0.45)}px">❓</div><div class="card-back" style="font-size:\${Math.round(cardSize*0.45)}px">\${emoji}</div></div>\`;
    card.addEventListener('click',()=>flip(card,i));
    board.appendChild(card);
  });
}
function flip(card, idx) {
  if(lock || card.classList.contains('flipped') || card.classList.contains('matched')) return;
  card.classList.add('flipped'); flipped.push({card,idx});
  if(flipped.length===2){
    moves++; document.getElementById('moves').textContent=moves; lock=true;
    const [a,b]=flipped;
    if(cards[a.idx]===cards[b.idx]){
      a.card.classList.add('matched'); b.card.classList.add('matched');
      pairs++; document.getElementById('pairs').textContent=pairs;
      flipped=[]; lock=false;
      if(pairs===total){ clearInterval(timer); document.getElementById('result').textContent=\`🎉 完成！用时 \${sec}s，步数 \${moves}\`; }
    } else {
      setTimeout(()=>{ a.card.classList.remove('flipped'); b.card.classList.remove('flipped'); flipped=[]; lock=false; },900);
    }
  }
}
init();
</script>
</body>
</html>`;

const WHACK_MOLE_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no,viewport-fit=cover">
<title>打地鼠</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; -webkit-tap-highlight-color: transparent; user-select: none; -webkit-user-select: none; }
body { background: #2d5016; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); font-family: Arial, sans-serif; color: #fff; }
h1 { font-size: clamp(1.2rem, 5vw, 2rem); margin-bottom: 8px; text-shadow: 2px 2px 4px rgba(0,0,0,0.5); }
#hud { display: flex; gap: clamp(14px, 4vw, 40px); margin-bottom: 16px; background: rgba(0,0,0,0.4); padding: 10px 24px; border-radius: 30px; }
.hud-item { text-align: center; font-size: clamp(0.7rem, 2.5vw, 0.85rem); color: #ccc; }
.hud-val { font-size: clamp(1.3rem, 5vw, 1.8rem); font-weight: bold; color: #ffd700; display: block; }
#grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: clamp(8px, 3vw, 15px); padding: clamp(10px, 3vw, 20px); background: rgba(0,0,0,0.3); border-radius: 20px; }
.hole { width: min(110px, 28vw); height: min(90px, 22vw); background: #1a3009; border-radius: 50% 50% 45% 45%; border: 4px solid #4a7c2f; position: relative; overflow: hidden; cursor: pointer; }
.mole { position: absolute; bottom: -110%; left: 50%; transform: translateX(-50%); font-size: clamp(1.8rem, 7vw, 3rem); transition: bottom 0.15s ease-out; line-height: 1; }
.hole.up .mole { bottom: 5%; }
.hole.bonk .mole { filter: brightness(0.5) sepia(1) hue-rotate(-20deg); }
#start-btn { margin-top: 16px; padding: 14px 40px; min-height: 48px; background: #ffd700; color: #1a1a1a; border: none; border-radius: 30px; font-size: clamp(0.95rem, 3.5vw, 1.1rem); font-weight: bold; cursor: pointer; }
#start-btn:active { transform: scale(0.97); }
#result { margin-top: 12px; font-size: clamp(0.9rem, 3.5vw, 1.2rem); color: #ffd700; font-weight: bold; }
</style>
</head>
<body>
<h1>🔨 打地鼠</h1>
<div id="hud">
  <div class="hud-item">得分<span class="hud-val" id="sc">0</span></div>
  <div class="hud-item">连击<span class="hud-val" id="combo">x1</span></div>
  <div class="hud-item">时间<span class="hud-val" id="tm">60</span></div>
  <div class="hud-item">未中<span class="hud-val" id="miss">0</span></div>
</div>
<div id="grid"></div>
<button id="start-btn" onclick="startGame()">开始游戏</button>
<div id="result"></div>
<script>
const MOLES = ['🐹','🐭','🦔','🐿️','🦦'];
const grid = document.getElementById('grid');
let holes = [], timeLeft = 60, score = 0, miss = 0, combo = 1, comboTimer = null;
let gameTimer = null, moleTimers = [], running = false;
for(let i = 0; i < 9; i++){
  const hole = document.createElement('div'); hole.className = 'hole';
  const moleType = MOLES[i % MOLES.length];
  hole.innerHTML = \`<span class="mole">\${moleType}</span>\`;
  hole.addEventListener('click', () => whack(hole, i));
  grid.appendChild(hole); holes.push(hole);
}
function startGame(){
  holes.forEach(h => { h.classList.remove('up','bonk'); });
  moleTimers.forEach(clearTimeout); moleTimers = []; clearInterval(gameTimer);
  score = 0; miss = 0; combo = 1; timeLeft = 60; running = true;
  document.getElementById('sc').textContent = 0; document.getElementById('combo').textContent = 'x1';
  document.getElementById('miss').textContent = 0; document.getElementById('result').textContent = '';
  document.getElementById('start-btn').textContent = '重新开始';
  gameTimer = setInterval(() => { timeLeft--; document.getElementById('tm').textContent = timeLeft; if(timeLeft <= 0){ endGame(); } }, 1000);
  scheduleMole();
}
function scheduleMole(){
  if(!running) return;
  const idx = Math.floor(Math.random() * 9); const hole = holes[idx];
  if(!hole.classList.contains('up')){
    hole.classList.add('up');
    const duration = Math.max(600, 1200 - score * 3);
    const t = setTimeout(() => { if(hole.classList.contains('up')){ hole.classList.remove('up'); miss++; document.getElementById('miss').textContent = miss; } }, duration);
    moleTimers.push(t);
  }
  const next = Math.max(200, 700 - score * 2); moleTimers.push(setTimeout(scheduleMole, next));
}
function whack(hole, idx){
  if(!running || !hole.classList.contains('up')) return;
  hole.classList.remove('up'); hole.classList.add('bonk');
  setTimeout(() => hole.classList.remove('bonk'), 400);
  clearTimeout(comboTimer); comboTimer = setTimeout(() => { combo = 1; document.getElementById('combo').textContent = 'x1'; }, 1500);
  const pts = 10 * combo; score += pts; combo = Math.min(combo + 1, 10);
  document.getElementById('sc').textContent = score; document.getElementById('combo').textContent = 'x' + combo;
}
function endGame(){
  running = false; clearInterval(gameTimer); moleTimers.forEach(clearTimeout);
  holes.forEach(h => h.classList.remove('up'));
  document.getElementById('result').textContent = \`🎉 游戏结束！最终得分: \${score}，未中: \${miss}\`;
}
</script>
</body>
</html>`;

const GAME_2048_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no,viewport-fit=cover">
<title>2048</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; -webkit-tap-highlight-color: transparent; user-select: none; -webkit-user-select: none; }
body { background: #faf8ef; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); font-family: 'Arial', sans-serif; }
h1 { font-size: clamp(2rem, 8vw, 3rem); font-weight: bold; color: #776e65; margin-bottom: 5px; }
#top { display: flex; gap: 12px; margin-bottom: 12px; align-items: center; }
.score-box { background: #bbada0; color: #fff; padding: 6px 16px; border-radius: 6px; text-align: center; min-width: 70px; }
.score-box .label { font-size: 0.7rem; text-transform: uppercase; }
.score-box .val { font-size: clamp(1.1rem, 4vw, 1.4rem); font-weight: bold; }
#new-btn { padding: 12px 18px; min-height: 48px; background: #8f7a66; color: #fff; border: none; border-radius: 6px; font-size: clamp(0.8rem, 3vw, 0.9rem); font-weight: bold; cursor: pointer; }
#new-btn:active { opacity: 0.85; }
#board { background: #bbada0; border-radius: 8px; padding: 8px; display: grid; grid-template-columns: repeat(4,1fr); gap: 8px; }
.cell { background: rgba(238,228,218,0.35); border-radius: 4px; display: flex; align-items: center; justify-content: center; font-weight: bold; }
.t2{background:#eee4da;color:#776e65}.t4{background:#ede0c8;color:#776e65}.t8{background:#f2b179;color:#fff}.t16{background:#f59563;color:#fff}.t32{background:#f67c5f;color:#fff}.t64{background:#f65e3b;color:#fff}.t128{background:#edcf72;color:#fff}.t256{background:#edcc61;color:#fff}.t512{background:#edc850;color:#fff}.t1024{background:#edc53f;color:#fff}.t2048{background:#edc22e;color:#fff}
#msg { margin-top: 12px; font-size: clamp(0.85rem, 3.5vw, 1.1rem); color: #776e65; }
</style>
</head>
<body>
<h1>2048</h1>
<div id="top">
  <div class="score-box"><div class="label">得分</div><div class="val" id="sc">0</div></div>
  <div class="score-box"><div class="label">最高</div><div class="val" id="best">0</div></div>
  <button id="new-btn" onclick="newGame()">新游戏</button>
</div>
<div id="board"></div>
<div id="msg">用方向键或滑动控制</div>
<script>
let grid, score = 0, best = 0, won = false;
const cellSz2048 = Math.floor((Math.min(window.innerWidth - 40, 380)) / 4) - 8;
const board2048 = document.getElementById('board');
board2048.style.width = (cellSz2048 * 4 + 8 * 3 + 8 * 2) + 'px';
const cellFontSz = Math.round(cellSz2048 * 0.38);
document.querySelector('style').sheet.insertRule('.cell{width:'+cellSz2048+'px;height:'+cellSz2048+'px;font-size:'+cellFontSz+'px;}',0);
function newGame(){ grid = Array(4).fill(null).map(()=>Array(4).fill(0)); score=0; won=false; addTile(); addTile(); render(); document.getElementById('msg').textContent='用方向键或滑动控制'; }
function addTile(){
  const empty=[]; grid.forEach((r,i)=>r.forEach((v,j)=>{ if(!v) empty.push([i,j]); }));
  if(!empty.length) return; const [r,c]=empty[Math.floor(Math.random()*empty.length)]; grid[r][c]=Math.random()<0.9?2:4;
}
function render(){
  const board=document.getElementById('board'); board.innerHTML='';
  grid.forEach(row=>row.forEach(v=>{
    const cell=document.createElement('div'); cell.className='cell'+(v?' t'+v:''); cell.textContent=v||''; board.appendChild(cell);
  }));
  document.getElementById('sc').textContent=score;
  if(score>best){ best=score; document.getElementById('best').textContent=best; }
}
function slide(row){
  let r=row.filter(v=>v), pts=0;
  for(let i=0;i<r.length-1;i++) if(r[i]===r[i+1]){ r[i]*=2; pts+=r[i]; r.splice(i+1,1); i++; }
  while(r.length<4) r.push(0); score+=pts; return r;
}
function move(dir){
  let moved=false;
  const rotated=(g,times)=>{ let r=[...g]; for(let t=0;t<times;t++) r=r[0].map((_,i)=>r.map(row=>row[i]).reverse()); return r; };
  const times={left:0,right:2,up:3,down:1}[dir];
  let g=rotated(grid,times);
  g=g.map(row=>{ const s=slide(row); if(s.join()!==row.join()) moved=true; return s; });
  grid=rotated(g,(4-times)%4);
  if(moved){ addTile(); render(); checkEnd(); }
}
function checkEnd(){
  if(grid.some(r=>r.includes(2048))&&!won){ won=true; document.getElementById('msg').textContent='🎉 恭喜达到 2048！'; }
  const canMove=grid.some((r,i)=>r.some((v,j)=>!v||(i<3&&grid[i+1][j]===v)||(j<3&&r[j+1]===v)));
  if(!canMove&&!won) document.getElementById('msg').textContent='❌ 无法移动，游戏结束！';
}
let touchX, touchY;
document.addEventListener('keydown',e=>{ const m={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down'}; if(m[e.key]){ e.preventDefault(); move(m[e.key]); } });
document.addEventListener('touchstart',e=>{ touchX=e.touches[0].clientX; touchY=e.touches[0].clientY; },{passive:true});
document.addEventListener('touchend',e=>{ const dx=e.changedTouches[0].clientX-touchX, dy=e.changedTouches[0].clientY-touchY; if(Math.max(Math.abs(dx),Math.abs(dy))<20) return; move(Math.abs(dx)>Math.abs(dy)?(dx>0?'right':'left'):(dy>0?'down':'up')); });
newGame();
</script>
</body>
</html>`;

const SPACE_SHOOTER_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no,viewport-fit=cover">
<title>太空射击</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; -webkit-tap-highlight-color: transparent; user-select: none; -webkit-user-select: none; }
body { background: #000; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); font-family: Arial, sans-serif; overflow: hidden; }
canvas { display: block; border: 1px solid #1a1a3e; }
#ui { position: absolute; top: 0; left: 50%; transform: translateX(-50%); display: flex; gap: 30px; padding: 10px 20px; background: rgba(0,0,0,0.7); color: #fff; font-size: 0.9rem; }
.ui-item { text-align: center; }
.ui-val { font-size: 1.3rem; font-weight: bold; color: #58a6ff; }
#overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; background: rgba(0,0,0,0.85); color: #fff; }
#overlay h2 { font-size: 2rem; margin-bottom: 10px; color: #58a6ff; }
#overlay p { margin-bottom: 5px; color: #aaa; font-size: 0.9rem; }
#play-btn { margin-top: 20px; padding: 12px 40px; background: #58a6ff; color: #000; border: none; border-radius: 25px; font-size: 1rem; font-weight: bold; cursor: pointer; }
</style>
</head>
<body>
<div style="position:relative">
<canvas id="c" width="400" height="520"></canvas>
<div id="ui">
  <div class="ui-item">得分<div class="ui-val" id="sc">0</div></div>
  <div class="ui-item">生命<div class="ui-val" id="hp">❤️❤️❤️</div></div>
  <div class="ui-item">关卡<div class="ui-val" id="lv">1</div></div>
</div>
<div id="overlay">
  <h2>🚀 太空射击</h2>
  <p>← → 或 A D 移动飞船</p>
  <p>空格 自动射击</p>
  <p>消灭所有外星人！</p>
  <button id="play-btn" onclick="startGame()">开始游戏</button>
</div>
</div>
<script>
const C = document.getElementById('c'), ctx = C.getContext('2d');
const W = 400, H = 520;
let player, bullets, enemies, eBullets, particles, score, lives, level, running, keys = {}, shootCooldown = 0, enemyDir = 1, enemyMoveTimer = 0, stars = [];
for(let i = 0; i < 80; i++) stars.push({x: Math.random()*W, y: Math.random()*H, s: Math.random()*2+0.5, sp: Math.random()*0.5+0.2});
function startGame(){
  document.getElementById('overlay').style.display = 'none';
  player = {x: W/2, y: H-50, w: 30, h: 20, speed: 4};
  bullets = []; enemies = []; eBullets = []; particles = [];
  score = 0; lives = 3; level = 1; running = true; enemyDir = 1; enemyMoveTimer = 0;
  spawnEnemies(); loop();
}
function spawnEnemies(){
  enemies = [];
  for(let r = 0; r < 3; r++) for(let c = 0; c < 8; c++)
    enemies.push({x: 40+c*42, y: 60+r*36, w: 28, h: 20, alive: true, type: r, hp: r+1, shootTimer: Math.random()*120});
}
function loop(){ if(!running) return; update(); draw(); requestAnimationFrame(loop); }
function update(){
  if(keys['ArrowLeft']||keys['a']||keys['A']) player.x = Math.max(15, player.x-player.speed);
  if(keys['ArrowRight']||keys['d']||keys['D']) player.x = Math.min(W-15, player.x+player.speed);
  shootCooldown--;
  if((keys[' ']||keys['f']) && shootCooldown<=0){
    bullets.push({x:player.x, y:player.y-15, w:3, h:12, dy:-9});
    if(level>=3) bullets.push({x:player.x-12, y:player.y-10, w:3, h:12, dy:-9},{x:player.x+12, y:player.y-10, w:3, h:12, dy:-9});
    shootCooldown = 10;
  }
  bullets = bullets.filter(b=>{ b.y+=b.dy; return b.y>0; });
  eBullets = eBullets.filter(b=>{ b.x+=b.dx||0; b.y+=b.dy; return b.y<H; });
  enemyMoveTimer++; const spd = 0.4 + level*0.15;
  if(enemyMoveTimer > 60/spd){
    enemyMoveTimer = 0;
    const alive = enemies.filter(e=>e.alive);
    if(!alive.length) return;
    const minX = Math.min(...alive.map(e=>e.x)), maxX = Math.max(...alive.map(e=>e.x+e.w));
    if((enemyDir>0&&maxX>W-10)||(enemyDir<0&&minX<10)){ enemyDir=-enemyDir; alive.forEach(e=>e.y+=15); }
    alive.forEach(e=>{ e.x+=enemyDir*8; e.shootTimer--; if(e.shootTimer<=0){ e.shootTimer=60+Math.random()*80/level; const angle=Math.atan2(player.y-e.y,player.x-e.x); eBullets.push({x:e.x+e.w/2,y:e.y+e.h,dx:Math.cos(angle)*3,dy:Math.abs(Math.sin(angle))*3+2,w:4,h:4}); } });
  }
  bullets.forEach(b=>{ enemies.forEach(e=>{ if(!e.alive) return; if(b.x>e.x&&b.x<e.x+e.w&&b.y>e.y&&b.y<e.y+e.h){ e.hp--; b.y=-100; if(e.hp<=0){ e.alive=false; score+=10*(e.type+1); addParticles(e.x+e.w/2,e.y+e.h/2,e.type); } } }); });
  eBullets.forEach(b=>{ if(Math.abs(b.x-player.x)<14&&Math.abs(b.y-player.y)<12){ b.y=H+1; lives--; addParticles(player.x,player.y,0); if(lives<=0){ endGame(); } } });
  particles = particles.filter(p=>{ p.x+=p.vx; p.y+=p.vy; p.life--; return p.life>0; });
  if(enemies.every(e=>!e.alive)){ level++; spawnEnemies(); }
  document.getElementById('sc').textContent=score;
  document.getElementById('hp').textContent='❤️'.repeat(Math.max(0,lives));
  document.getElementById('lv').textContent=level;
}
function addParticles(x,y,type){ const colors=['#58a6ff','#ffd700','#ff6b6b']; for(let i=0;i<8;i++) particles.push({x,y,vx:(Math.random()-0.5)*4,vy:(Math.random()-0.5)*4,life:30,color:colors[type%3]}); }
function draw(){
  ctx.fillStyle='#000010'; ctx.fillRect(0,0,W,H);
  stars.forEach(s=>{ s.y+=s.sp; if(s.y>H) s.y=0; ctx.fillStyle=\`rgba(255,255,255,\${s.s/3})\`; ctx.fillRect(s.x,s.y,s.s,s.s); });
  ctx.fillStyle='#58a6ff';
  ctx.beginPath(); ctx.moveTo(player.x,player.y-player.h); ctx.lineTo(player.x-player.w/2,player.y+player.h/2); ctx.lineTo(player.x,player.y+5); ctx.lineTo(player.x+player.w/2,player.y+player.h/2); ctx.closePath(); ctx.fill();
  const emojiMap=['👾','🛸','👽'];
  enemies.forEach(e=>{ if(!e.alive) return; ctx.font=\`\${e.w}px serif\`; ctx.fillText(emojiMap[e.type]||'👾',e.x,e.y+e.h); });
  bullets.forEach(b=>{ ctx.fillStyle='#ffd700'; ctx.shadowColor='#ffd700'; ctx.shadowBlur=8; ctx.fillRect(b.x-b.w/2,b.y,b.w,b.h); });
  eBullets.forEach(b=>{ ctx.fillStyle='#ff6b6b'; ctx.shadowColor='#ff6b6b'; ctx.shadowBlur=6; ctx.beginPath(); ctx.arc(b.x,b.y,b.w/2,0,Math.PI*2); ctx.fill(); });
  ctx.shadowBlur=0;
  particles.forEach(p=>{ ctx.fillStyle=p.color; ctx.globalAlpha=p.life/30; ctx.fillRect(p.x-2,p.y-2,4,4); });
  ctx.globalAlpha=1;
}
function endGame(){
  running=false;
  const ov=document.getElementById('overlay'); ov.style.display='flex';
  ov.innerHTML=\`<h2>💀 游戏结束</h2><p style="color:#ffd700;font-size:1.5rem;margin:10px">得分: \${score}</p><p>关卡: \${level}</p><button style="margin-top:20px;padding:12px 40px;background:#58a6ff;color:#000;border:none;border-radius:25px;font-size:1rem;font-weight:bold;cursor:pointer" onclick="startGame()">再来一次</button>\`;
}
document.addEventListener('keydown',e=>{ keys[e.key]=true; if(e.key===' ') e.preventDefault(); });
document.addEventListener('keyup',e=>{ keys[e.key]=false; });
let touchShoot = false;
C.addEventListener('touchstart',e=>{ e.preventDefault(); touchShoot=true; },{passive:false});
C.addEventListener('touchmove',e=>{ e.preventDefault(); const dx=e.touches[0].clientX-C.getBoundingClientRect().left; player.x=Math.max(15,Math.min(W-15,dx)); },{passive:false});
C.addEventListener('touchend',()=>{ touchShoot=false; });
</script>
</body>
</html>`;

// ============================================================
// 移动端游戏 HTML（来自 seed-mobile-games.mjs）
// ============================================================

const STACK_TOWER_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>叠叠高塔</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;-webkit-tap-highlight-color:transparent}
body{background:#1a1a2e;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);font-family:Arial,sans-serif;color:#fff;user-select:none;-webkit-user-select:none}
h1{font-size:1.5rem;color:#f5a623;margin-bottom:8px}
#sc-wrap{display:flex;gap:40px;margin-bottom:10px}
.sv{font-size:1.8rem;font-weight:bold;color:#16c79a}
#msg{margin-top:10px;font-size:1rem;color:#aaa;text-align:center}
#btn{margin-top:12px;padding:12px 36px;background:#f5a623;color:#000;border:none;border-radius:25px;font-size:1.1rem;font-weight:bold;cursor:pointer;display:none}
</style>
</head>
<body>
<h1>🏗️ 叠叠高塔</h1>
<div id="sc-wrap">
  <div>得分 <span class="sv" id="sc">0</span></div>
  <div>最高 <span class="sv" id="best">0</span></div>
</div>
<canvas id="c"></canvas>
<div id="msg">触摸屏幕开始</div>
<button id="btn" onclick="startGame()">再来一次</button>
<script>
var C=document.getElementById('c'),ctx=C.getContext('2d');
var W=Math.min(window.innerWidth-16,340),H=Math.min(window.innerHeight-180,460);
C.width=W;C.height=H;
var BH=24,COLS=['#16c79a','#58a6ff','#f5a623','#f06292','#ab47bc','#26c6da','#66bb6a'];
var blocks,mov,score,best=0,running=false,camY=0,raf;
function rr(x,y,w,h,r){ctx.beginPath();ctx.moveTo(x+r,y);ctx.lineTo(x+w-r,y);ctx.arcTo(x+w,y,x+w,y+r,r);ctx.lineTo(x+w,y+h-r);ctx.arcTo(x+w,y+h,x+w-r,y+h,r);ctx.lineTo(x+r,y+h);ctx.arcTo(x,y+h,x,y+h-r,r);ctx.lineTo(x,y+r);ctx.arcTo(x,y,x+r,y,r);ctx.closePath();}
function startGame(){
  cancelAnimationFrame(raf);
  blocks=[{x:W/2-70,w:140,y:H-BH,c:COLS[0]}];
  mov={x:0,w:140,dir:1,spd:2.5,c:COLS[1]};
  score=0;camY=0;running=true;
  document.getElementById('sc').textContent=0;
  document.getElementById('msg').textContent='触摸放下方块！';
  document.getElementById('btn').style.display='none';
  loop();
}
function drop(){
  if(!running){startGame();return;}
  var top=blocks[blocks.length-1];
  var ol=Math.max(mov.x,top.x),or2=Math.min(mov.x+mov.w,top.x+top.w),ow=or2-ol;
  if(ow<=0){endGame();return;}
  blocks.push({x:ol,w:ow,y:top.y-BH,c:mov.c});
  score++;
  if(score>best){best=score;document.getElementById('best').textContent=best;}
  document.getElementById('sc').textContent=score;
  if(blocks.length>14)camY+=BH;
  var spd=Math.min(2.5+score*0.2,9);
  mov={x:mov.dir>0?W+20:-ow-20,w:ow,dir:-mov.dir,spd:spd,c:COLS[score%COLS.length]};
}
function loop(){
  raf=requestAnimationFrame(loop);
  ctx.fillStyle='#0a0a1a';ctx.fillRect(0,0,W,H);
  blocks.forEach(function(b){
    var dy=b.y+camY;if(dy>H+BH||dy<-BH)return;
    ctx.fillStyle=b.c;rr(b.x,dy,b.w,BH-2,3);ctx.fill();
    ctx.fillStyle='rgba(255,255,255,0.25)';ctx.fillRect(b.x,dy,b.w,5);
  });
  if(running){
    mov.x+=mov.dir*mov.spd;
    if(mov.x+mov.w>W+30)mov.dir=-1;if(mov.x<-30)mov.dir=1;
    var top=blocks[blocks.length-1],my=top.y-BH+camY;
    ctx.fillStyle=mov.c;rr(mov.x,my,mov.w,BH-2,3);ctx.fill();
    ctx.fillStyle='rgba(255,255,255,0.25)';ctx.fillRect(mov.x,my,mov.w,5);
  }
}
function endGame(){
  running=false;cancelAnimationFrame(raf);
  ctx.fillStyle='rgba(0,0,0,0.7)';ctx.fillRect(0,0,W,H);
  ctx.textAlign='center';
  ctx.fillStyle='#f5a623';ctx.font='bold 30px Arial';ctx.fillText('倒塌了！',W/2,H/2-20);
  ctx.fillStyle='#fff';ctx.font='20px Arial';ctx.fillText('叠了 '+score+' 层',W/2,H/2+18);
  ctx.textAlign='left';document.getElementById('btn').style.display='block';
}
C.addEventListener('touchstart',function(e){e.preventDefault();drop();},{passive:false});
C.addEventListener('click',function(){drop();});
ctx.fillStyle='#0a0a1a';ctx.fillRect(0,0,W,H);
ctx.textAlign='center';ctx.fillStyle='#f5a623';ctx.font='bold 22px Arial';ctx.fillText('触摸开始',W/2,H/2);ctx.textAlign='left';
</script>
</body>
</html>`;

const FRUIT_NINJA_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>水果忍者</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;-webkit-tap-highlight-color:transparent}
body{background:#0d1b2a;height:100vh;font-family:Arial,sans-serif;user-select:none;-webkit-user-select:none}
#hud{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-between;padding:calc(12px + env(safe-area-inset-top)) 20px 12px;background:rgba(0,0,0,0.6);color:#fff;font-size:1rem;z-index:10}
.hv{font-size:1.6rem;font-weight:bold;color:#ffd700;display:block}
#overlay{position:fixed;inset:0;background:rgba(0,0,0,0.85);display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;z-index:20}
#overlay h2{font-size:2rem;color:#ffd700;margin-bottom:12px}
#overlay p{color:#ccc;margin:4px 0;font-size:0.95rem}
#pbtn{margin-top:20px;padding:14px 44px;background:#ffd700;color:#000;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer}
canvas{display:block;touch-action:none}
</style>
</head>
<body>
<div id="hud">
  <div>得分<span class="hv" id="sc">0</span></div>
  <div>❤️<span class="hv" id="hp">3</span></div>
  <div>⏱️<span class="hv" id="tm">60</span>s</div>
</div>
<canvas id="c"></canvas>
<div id="overlay">
  <h2>🍉 水果忍者</h2>
  <p>滑动手指切开水果</p>
  <p>💣 碰到炸弹扣一条血</p>
  <p>60秒挑战最高分！</p>
  <button id="pbtn" onclick="startGame()">开始游戏</button>
</div>
<script>
var C=document.getElementById('c'),ctx=C.getContext('2d');
C.width=window.innerWidth;C.height=window.innerHeight;
var W=C.width,H=C.height;
var FRUITS=['🍎','🍊','🍋','🍇','🍓','🍑','🍍','🥭','🍌','🫐'];
var items=[],score=0,lives=3,timeLeft=60,running=false,raf,gameInt,spawnT;
var tp=null,pp=null,trail=[];
function startGame(){
  document.getElementById('overlay').style.display='none';
  items=[];score=0;lives=3;timeLeft=60;running=true;trail=[];
  document.getElementById('sc').textContent=0;document.getElementById('hp').textContent=3;document.getElementById('tm').textContent=60;
  clearInterval(gameInt);clearTimeout(spawnT);
  gameInt=setInterval(function(){if(!running)return;timeLeft--;document.getElementById('tm').textContent=timeLeft;if(timeLeft<=0)endGame();},1000);
  doSpawn();cancelAnimationFrame(raf);loop();
}
function doSpawn(){
  if(!running)return;
  var bomb=Math.random()<0.18,r=26+Math.random()*18,x=r+Math.random()*(W-r*2),vy=-(H*0.017+Math.random()*H*0.007);
  items.push({x:x,y:H+r,vx:(Math.random()-0.5)*5,vy:vy,r:r,emoji:bomb?'💣':FRUITS[Math.floor(Math.random()*10)],bomb:bomb,cut:false,alpha:1});
  var delay=Math.max(250,850-score*4);spawnT=setTimeout(doSpawn,delay);
}
function segCircleDist(x1,y1,x2,y2,cx,cy){
  var dx=x2-x1,dy=y2-y1,len2=dx*dx+dy*dy;
  if(len2<1)return Math.sqrt((x1-cx)*(x1-cx)+(y1-cy)*(y1-cy));
  var t=Math.max(0,Math.min(1,((cx-x1)*dx+(cy-y1)*dy)/len2));
  return Math.sqrt((x1+dx*t-cx)*(x1+dx*t-cx)+(y1+dy*t-cy)*(y1+dy*t-cy));
}
function loop(){
  raf=requestAnimationFrame(loop);ctx.clearRect(0,0,W,H);
  if(trail.length>1){for(var i=1;i<trail.length;i++){var a=(i/trail.length)*0.9;ctx.strokeStyle='rgba(255,255,255,'+a+')';ctx.lineWidth=3+a*2;ctx.lineCap='round';ctx.beginPath();ctx.moveTo(trail[i-1].x,trail[i-1].y);ctx.lineTo(trail[i].x,trail[i].y);ctx.stroke();}}
  items=items.filter(function(it){
    it.vy+=0.3;it.x+=it.vx;it.y+=it.vy;
    if(it.cut){it.y+=it.vy*0.5;it.alpha-=0.07;if(it.alpha<=0)return false;}
    else if(it.y>H+60){if(!it.bomb){lives--;document.getElementById('hp').textContent=Math.max(0,lives);if(lives<=0)endGame();}return false;}
    ctx.save();ctx.globalAlpha=it.alpha;ctx.font=(it.r*2)+'px serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(it.emoji,it.x,it.y);ctx.restore();return true;
  });
}
function slash(x1,y1,x2,y2){
  if(!running)return;var len=Math.sqrt((x2-x1)*(x2-x1)+(y2-y1)*(y2-y1));if(len<10)return;
  for(var i=items.length-1;i>=0;i--){var it=items[i];if(it.cut)continue;if(segCircleDist(x1,y1,x2,y2,it.x,it.y)<it.r){it.cut=true;if(it.bomb){lives--;document.getElementById('hp').textContent=Math.max(0,lives);if(lives<=0){endGame();return;}}else{score+=10;document.getElementById('sc').textContent=score;}}}
}
function endGame(){
  running=false;clearInterval(gameInt);clearTimeout(spawnT);
  var ov=document.getElementById('overlay');ov.style.display='flex';
  ov.innerHTML='<h2>🎉 游戏结束</h2><p style="font-size:1.5rem;color:#ffd700;margin:12px">得分: '+score+'</p><button onclick="startGame()" style="margin-top:20px;padding:14px 44px;background:#ffd700;color:#000;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer">再来一次</button>';
}
C.addEventListener('touchstart',function(e){e.preventDefault();var t=e.touches[0];pp=null;tp={x:t.clientX,y:t.clientY};trail=[tp];},{passive:false});
C.addEventListener('touchmove',function(e){e.preventDefault();var t=e.touches[0];pp=tp;tp={x:t.clientX,y:t.clientY};trail.push(tp);if(trail.length>20)trail.shift();if(pp)slash(pp.x,pp.y,tp.x,tp.y);},{passive:false});
C.addEventListener('touchend',function(){tp=null;pp=null;trail=[];});
</script>
</body>
</html>`;

const BUBBLE_POP_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>泡泡消消</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;-webkit-tap-highlight-color:transparent}
body{background:linear-gradient(180deg,#0d1b3e,#1a0533);height:100vh;font-family:Arial,sans-serif;user-select:none;-webkit-user-select:none}
#hud{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-around;padding:calc(12px + env(safe-area-inset-top)) 12px 12px;background:rgba(0,0,0,0.5);color:#fff;z-index:10}
.hl{font-size:0.75rem;color:#aaa;display:block;text-align:center}
.hv{font-size:1.5rem;font-weight:bold;color:#fff;display:block;text-align:center}
#overlay{position:fixed;inset:0;background:rgba(0,0,0,0.85);display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;z-index:20}
#overlay h2{font-size:2rem;margin-bottom:12px}
#overlay p{color:#ccc;margin:4px 0;font-size:0.9rem}
#pbtn{margin-top:20px;padding:14px 44px;background:linear-gradient(135deg,#a855f7,#3b82f6);color:#fff;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer}
canvas{display:block;touch-action:none}
</style>
</head>
<body>
<div id="hud">
  <div><span class="hl">得分</span><span class="hv" id="sc">0</span></div>
  <div><span class="hl">连击</span><span class="hv" id="combo">x1</span></div>
  <div><span class="hl">时间</span><span class="hv" id="tm">60</span></div>
</div>
<canvas id="c"></canvas>
<div id="overlay">
  <h2>🫧 泡泡消消</h2>
  <p>点击气泡获得分数</p>
  <p>连续点击提升连击倍数！</p>
  <p>💣 红色炸弹会扣分</p>
  <button id="pbtn" onclick="startGame()">开始游戏</button>
</div>
<script>
var C=document.getElementById('c'),ctx=C.getContext('2d');
C.width=window.innerWidth;C.height=window.innerHeight;
var W=C.width,H=C.height,HH=70;
var BCOLORS=[['#a855f7','#7c3aed'],['#3b82f6','#1d4ed8'],['#06b6d4','#0891b2'],['#10b981','#059669'],['#f59e0b','#d97706'],['#ec4899','#db2777']];
var bubbles=[],score=0,combo=1,comboT=null,timeLeft=60,running=false,raf,gameInt,spawnT;
function startGame(){
  document.getElementById('overlay').style.display='none';
  bubbles=[];score=0;combo=1;timeLeft=60;running=true;
  document.getElementById('sc').textContent=0;document.getElementById('combo').textContent='x1';document.getElementById('tm').textContent=60;
  clearInterval(gameInt);clearTimeout(spawnT);
  gameInt=setInterval(function(){if(!running)return;timeLeft--;document.getElementById('tm').textContent=timeLeft;if(timeLeft<=0)endGame();},1000);
  doSpawn();cancelAnimationFrame(raf);loop();
}
function doSpawn(){
  if(!running)return;
  var bomb=Math.random()<0.14,r=22+Math.random()*24,ci=Math.floor(Math.random()*BCOLORS.length);
  bubbles.push({x:r+Math.random()*(W-r*2),y:H+r,r:r,spd:0.7+Math.random()*1.1,bomb:bomb,ci:ci,alpha:1,popped:false,pa:0,wobble:Math.random()*6.28,ws:0.03+Math.random()*0.02});
  var delay=Math.max(180,800-score*2);spawnT=setTimeout(doSpawn,delay);
}
function loop(){
  raf=requestAnimationFrame(loop);ctx.clearRect(0,0,W,H);
  bubbles=bubbles.filter(function(b){
    if(b.popped){b.pa+=0.12;if(b.pa>1)return false;var cl=b.bomb?'#ef4444':BCOLORS[b.ci][0];ctx.save();ctx.globalAlpha=1-b.pa;ctx.strokeStyle=cl;ctx.lineWidth=3;ctx.beginPath();ctx.arc(b.x,b.y,b.r*(1+b.pa*0.8),0,Math.PI*2);ctx.stroke();ctx.restore();return true;}
    b.wobble+=b.ws;b.y-=b.spd;var wx=Math.sin(b.wobble)*2;if(b.y+b.r<HH)return false;
    var cl=b.bomb?['#ef4444','#b91c1c']:BCOLORS[b.ci];
    var g=ctx.createRadialGradient(b.x+wx-b.r*0.3,b.y-b.r*0.3,b.r*0.1,b.x+wx,b.y,b.r);
    g.addColorStop(0,cl[0]+'cc');g.addColorStop(1,cl[1]+'99');
    ctx.beginPath();ctx.arc(b.x+wx,b.y,b.r,0,Math.PI*2);ctx.fillStyle=g;ctx.fill();ctx.strokeStyle=cl[0];ctx.lineWidth=2;ctx.stroke();
    ctx.beginPath();ctx.arc(b.x+wx-b.r*0.25,b.y-b.r*0.3,b.r*0.3,0,Math.PI*2);ctx.fillStyle='rgba(255,255,255,0.25)';ctx.fill();return true;
  });
}
function handleTap(px,py){
  if(!running)return;
  for(var i=bubbles.length-1;i>=0;i--){
    var b=bubbles[i];if(b.popped)continue;var wx=Math.sin(b.wobble)*2;
    if(Math.sqrt((px-b.x-wx)*(px-b.x-wx)+(py-b.y)*(py-b.y))<b.r){
      b.popped=true;
      if(b.bomb){score=Math.max(0,score-20*combo);combo=1;document.getElementById('combo').textContent='x1';}
      else{score+=10*combo*(b.r>36?2:1);combo=Math.min(combo+1,10);clearTimeout(comboT);comboT=setTimeout(function(){combo=1;document.getElementById('combo').textContent='x1';},1500);document.getElementById('combo').textContent='x'+combo;}
      document.getElementById('sc').textContent=score;break;
    }
  }
}
function endGame(){
  running=false;clearInterval(gameInt);clearTimeout(spawnT);
  var ov=document.getElementById('overlay');ov.style.display='flex';
  ov.innerHTML='<h2>🎉 时间到！</h2><p style="font-size:1.5rem;color:#a855f7;margin:12px">得分: '+score+'</p><button onclick="startGame()" style="margin-top:20px;padding:14px 44px;background:linear-gradient(135deg,#a855f7,#3b82f6);color:#fff;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer">再玩一次</button>';
}
C.addEventListener('touchstart',function(e){e.preventDefault();Array.from(e.changedTouches).forEach(function(t){handleTap(t.clientX,t.clientY);});},{passive:false});
C.addEventListener('click',function(e){handleTap(e.clientX,e.clientY);});
</script>
</body>
</html>`;

const STAR_BLAST_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>消消星</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;-webkit-tap-highlight-color:transparent}
body{background:#1a0533;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);font-family:Arial,sans-serif;color:#fff;user-select:none;-webkit-user-select:none}
h1{font-size:clamp(1.1rem,4.5vw,1.4rem);color:#f59e0b;margin-bottom:6px}
#hud{display:flex;gap:clamp(12px,4vw,20px);margin-bottom:8px;background:rgba(255,255,255,0.08);padding:8px 16px;border-radius:20px}
.hl{font-size:0.7rem;color:#aaa;display:block;text-align:center}
.hv{font-size:clamp(1.1rem,4.5vw,1.4rem);font-weight:bold;color:#ffd700;display:block;text-align:center}
#board{display:grid;gap:3px;margin-bottom:6px}
.cell{border-radius:10px;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:transform 0.12s,opacity 0.25s;-webkit-tap-highlight-color:transparent;border:2px solid transparent}
.cell.removing{transform:scale(0);opacity:0}
#msg{font-size:clamp(0.75rem,3vw,0.85rem);color:#aaa;min-height:20px;text-align:center;margin-bottom:6px}
#btn{padding:12px 36px;min-height:48px;background:#f59e0b;color:#000;border:none;border-radius:25px;font-size:clamp(0.9rem,3.5vw,1rem);font-weight:bold;cursor:pointer;display:none;margin-top:8px}
</style>
</head>
<body>
<h1>⭐ 消消星</h1>
<div id="hud">
  <div><span class="hl">得分</span><span class="hv" id="sc">0</span></div>
  <div><span class="hl">时间</span><span class="hv" id="tm">60</span></div>
  <div><span class="hl">最高</span><span class="hv" id="best">0</span></div>
</div>
<div id="board"></div>
<div id="msg">点击 2 个以上相邻同色星星消除</div>
<button id="btn" onclick="init()">再玩一次</button>
<script>
var COLS=6,ROWS=7;
var COLORS=['#ef4444','#f97316','#eab308','#22c55e','#3b82f6','#a855f7'];
var EMOJIS=['🔴','🟠','🟡','🟢','🔵','🟣'];
var grid=[],score=0,best=0,timeLeft=60,running=false,animating=false,gameInt;
var cs=Math.floor((Math.min(window.innerWidth-20,300))/COLS)-3;
document.querySelector('style').sheet.insertRule('.cell{width:'+cs+'px;height:'+cs+'px;font-size:'+Math.round(cs*0.55)+'px;}',0);
function init(){
  score=0;timeLeft=60;running=true;animating=false;
  document.getElementById('sc').textContent=0;document.getElementById('tm').textContent=60;
  document.getElementById('msg').textContent='点击 2 个以上相邻同色星星消除';
  document.getElementById('btn').style.display='none';
  clearInterval(gameInt);
  gameInt=setInterval(function(){if(!running)return;timeLeft--;document.getElementById('tm').textContent=timeLeft;if(timeLeft<=0)endGame();},1000);
  grid=[];for(var r=0;r<ROWS;r++){grid.push([]);for(var c=0;c<COLS;c++)grid[r].push(Math.floor(Math.random()*COLORS.length));}
  render();
}
function render(){
  var board=document.getElementById('board');board.style.gridTemplateColumns='repeat('+COLS+', '+cs+'px)';board.innerHTML='';
  for(var r=0;r<ROWS;r++){for(var c=0;c<COLS;c++){
    var cell=document.createElement('div'),ci=grid[r][c];cell.className='cell';
    if(ci>=0){cell.style.background=COLORS[ci]+'33';cell.style.borderColor=COLORS[ci];cell.textContent=EMOJIS[ci];(function(rr,cc){cell.addEventListener('touchstart',function(e){e.preventDefault();tap(rr,cc);},{passive:false});cell.addEventListener('click',function(){tap(rr,cc);});})(r,c);}
    else{cell.style.background='rgba(255,255,255,0.04)';}board.appendChild(cell);
  }}
}
function bfs(r,c,col){
  var visited=[];for(var i=0;i<ROWS;i++){visited.push([]);for(var j=0;j<COLS;j++)visited[i].push(false);}
  var q=[[r,c]],group=[];visited[r][c]=true;
  while(q.length){var cur=q.shift(),cr=cur[0],cc2=cur[1];group.push([cr,cc2]);var dirs=[[-1,0],[1,0],[0,-1],[0,1]];for(var d=0;d<dirs.length;d++){var nr=cr+dirs[d][0],nc=cc2+dirs[d][1];if(nr>=0&&nr<ROWS&&nc>=0&&nc<COLS&&!visited[nr][nc]&&grid[nr][nc]===col){visited[nr][nc]=true;q.push([nr,nc]);}}}
  return group;
}
function tap(r,c){
  if(animating||!running||grid[r][c]<0)return;
  var col=grid[r][c],group=bfs(r,c,col);
  if(group.length<2){document.getElementById('msg').textContent='需要相邻的 2 个以上同色星星！';return;}
  animating=true;var pts=group.length*group.length*10;score+=pts;
  if(score>best){best=score;document.getElementById('best').textContent=best;}
  document.getElementById('sc').textContent=score;document.getElementById('msg').textContent='+'+pts+' 分！';
  var cells=document.getElementById('board').children;
  for(var i=0;i<group.length;i++)cells[group[i][0]*COLS+group[i][1]].classList.add('removing');
  setTimeout(function(){
    for(var i=0;i<group.length;i++)grid[group[i][0]][group[i][1]]=-1;
    for(var cc2=0;cc2<COLS;cc2++){var wr=ROWS-1;for(var rr=ROWS-1;rr>=0;rr--){if(grid[rr][cc2]>=0){grid[wr][cc2]=grid[rr][cc2];if(wr!==rr)grid[rr][cc2]=-1;wr--;}}while(wr>=0){grid[wr][cc2]=Math.floor(Math.random()*COLORS.length);wr--;}}
    render();animating=false;
  },280);
}
function endGame(){running=false;clearInterval(gameInt);document.getElementById('msg').textContent='时间到！最终得分: '+score;document.getElementById('btn').style.display='block';}
init();
</script>
</body>
</html>`;

const RHYTHM_TAP_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>节奏达人</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;-webkit-tap-highlight-color:transparent}
body{background:#0a0a1a;height:100vh;font-family:Arial,sans-serif;user-select:none;-webkit-user-select:none}
#hud{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-around;padding:calc(12px + env(safe-area-inset-top)) 12px 12px;background:rgba(0,0,0,0.6);color:#fff;z-index:10}
.hl{font-size:0.75rem;color:#888;display:block;text-align:center}
.hv{font-size:1.5rem;font-weight:bold;display:block;text-align:center}
#overlay{position:fixed;inset:0;background:rgba(0,0,0,0.88);display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;z-index:20}
#overlay h2{font-size:2rem;color:#a855f7;margin-bottom:12px}
#overlay p{color:#aaa;margin:4px 0;font-size:0.9rem}
#pbtn{margin-top:20px;padding:14px 44px;background:#a855f7;color:#fff;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer}
canvas{display:block;touch-action:none}
</style>
</head>
<body>
<div id="hud">
  <div><span class="hl">得分</span><span class="hv" id="sc" style="color:#a855f7">0</span></div>
  <div><span class="hl">连击</span><span class="hv" id="combo" style="color:#ffd700">x0</span></div>
  <div><span class="hl">❤️生命</span><span class="hv" id="hp" style="color:#ef4444">5</span></div>
  <div><span class="hl">时间</span><span class="hv" id="tm" style="color:#22c55e">60</span></div>
</div>
<canvas id="c"></canvas>
<div id="overlay">
  <h2>🎵 节奏达人</h2>
  <p>点击圆圈收集它！</p>
  <p>在圆圈消失前点击</p>
  <p>连击获得更多分数！</p>
  <button id="pbtn" onclick="startGame()">开始游戏</button>
</div>
<script>
var C=document.getElementById('c'),ctx=C.getContext('2d');
C.width=window.innerWidth;C.height=window.innerHeight;
var W=C.width,H=C.height,HH=70;
var PAL=['#a855f7','#3b82f6','#06b6d4','#10b981','#f59e0b','#ef4444','#ec4899'];
var circles=[],score=0,combo=0,lives=5,timeLeft=60,running=false,raf,gameInt,spawnT,lastTime=0;
function startGame(){
  document.getElementById('overlay').style.display='none';
  circles=[];score=0;combo=0;lives=5;timeLeft=60;running=true;
  document.getElementById('sc').textContent=0;document.getElementById('combo').textContent='x0';document.getElementById('hp').textContent=5;document.getElementById('tm').textContent=60;
  clearInterval(gameInt);clearTimeout(spawnT);
  gameInt=setInterval(function(){if(!running)return;timeLeft--;document.getElementById('tm').textContent=timeLeft;if(timeLeft<=0)endGame();},1000);
  scheduleSpawn();cancelAnimationFrame(raf);lastTime=performance.now();loop(lastTime);
}
function scheduleSpawn(){
  if(!running)return;
  var delay=Math.max(350,1100-score*1.5);
  spawnT=setTimeout(function(){
    if(!running)return;
    if(circles.length<6){var r=32+Math.random()*22,margin=r+50;circles.push({x:margin+Math.random()*(W-margin*2),y:HH+margin+Math.random()*(H-HH-margin*2),r:r,life:0,maxLife:1.6+Math.random()*1.4,color:PAL[Math.floor(Math.random()*PAL.length)],popping:false,pa:0});}
    scheduleSpawn();
  },delay);
}
function loop(now){
  raf=requestAnimationFrame(loop);var dt=Math.min((now-lastTime)/1000,0.05);lastTime=now;ctx.clearRect(0,0,W,H);
  circles=circles.filter(function(c){
    if(c.popping){c.pa+=dt*5;if(c.pa>1)return false;ctx.save();ctx.globalAlpha=1-c.pa;ctx.strokeStyle=c.color;ctx.lineWidth=3;ctx.beginPath();ctx.arc(c.x,c.y,c.r*(1+c.pa),0,Math.PI*2);ctx.stroke();ctx.restore();return true;}
    c.life+=dt;
    if(c.life>=c.maxLife){lives--;document.getElementById('hp').textContent=Math.max(0,lives);combo=0;document.getElementById('combo').textContent='x'+combo;if(lives<=0){endGame();return false;}return false;}
    var prog=c.life/c.maxLife,shrink=1-prog*0.55,cr=c.r*shrink,pulse=1+Math.sin(c.life*7)*0.03;
    ctx.strokeStyle=c.color;ctx.lineWidth=5;ctx.globalAlpha=0.35;ctx.beginPath();ctx.arc(c.x,c.y,cr*1.5,0,Math.PI*2);ctx.stroke();ctx.globalAlpha=1;
    ctx.strokeStyle=c.color;ctx.lineWidth=5;ctx.beginPath();ctx.arc(c.x,c.y,cr*1.5,-Math.PI/2,-Math.PI/2+Math.PI*2*(1-prog));ctx.stroke();
    var g=ctx.createRadialGradient(c.x-cr*0.3,c.y-cr*0.3,cr*0.1,c.x,c.y,cr*pulse);g.addColorStop(0,c.color+'dd');g.addColorStop(1,c.color+'55');
    ctx.beginPath();ctx.arc(c.x,c.y,cr*pulse,0,Math.PI*2);ctx.fillStyle=g;ctx.fill();
    var secs=Math.ceil(c.maxLife-c.life);ctx.fillStyle='#fff';ctx.font='bold '+(cr*0.65)+'px Arial';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(secs>0?secs:'',c.x,c.y);ctx.textAlign='left';ctx.textBaseline='alphabetic';
    return true;
  });
}
function handleTap(px,py){
  if(!running)return;
  for(var i=circles.length-1;i>=0;i--){var c=circles[i];if(c.popping)continue;var prog=c.life/c.maxLife,cr=c.r*(1-prog*0.55);if(Math.sqrt((px-c.x)*(px-c.x)+(py-c.y)*(py-c.y))<cr*1.5){c.popping=true;combo++;var pts=10*(1+Math.min(combo,10));score+=pts;document.getElementById('sc').textContent=score;document.getElementById('combo').textContent='x'+combo;break;}}
}
function endGame(){
  running=false;clearInterval(gameInt);clearTimeout(spawnT);
  var ov=document.getElementById('overlay');ov.style.display='flex';
  ov.innerHTML='<h2>🎵 游戏结束</h2><p style="font-size:1.5rem;color:#a855f7;margin:12px">得分: '+score+'</p><button onclick="startGame()" style="margin-top:20px;padding:14px 44px;background:#a855f7;color:#fff;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer">再来一次</button>';
}
C.addEventListener('touchstart',function(e){e.preventDefault();Array.from(e.changedTouches).forEach(function(t){handleTap(t.clientX,t.clientY);});},{passive:false});
C.addEventListener('click',function(e){handleTap(e.clientX,e.clientY);});
</script>
</body>
</html>`;

const CATCH_FRUITS_HTML = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>接水果</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;-webkit-tap-highlight-color:transparent}
body{background:#1a4a0a;height:100vh;font-family:Arial,sans-serif;user-select:none;-webkit-user-select:none}
#hud{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-around;padding:calc(12px + env(safe-area-inset-top)) 12px 12px;background:rgba(0,0,0,0.55);color:#fff;z-index:10}
.hl{font-size:0.75rem;color:#aaa;display:block;text-align:center}
.hv{font-size:1.5rem;font-weight:bold;display:block;text-align:center;color:#ffd700}
#overlay{position:fixed;inset:0;background:rgba(0,0,0,0.85);display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;z-index:20}
#overlay h2{font-size:2rem;color:#22c55e;margin-bottom:12px}
#overlay p{color:#ccc;margin:4px 0;font-size:0.95rem}
#pbtn{margin-top:20px;padding:14px 44px;background:#22c55e;color:#fff;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer}
canvas{display:block;touch-action:none}
</style>
</head>
<body>
<div id="hud">
  <div><span class="hl">得分</span><span class="hv" id="sc">0</span></div>
  <div><span class="hl">❤️生命</span><span class="hv" id="hp">5</span></div>
  <div><span class="hl">时间</span><span class="hv" id="tm">60</span></div>
</div>
<canvas id="c"></canvas>
<div id="overlay">
  <h2>🍎 接水果</h2>
  <p>拖动篮子接住水果</p>
  <p>💣 炸弹会扣掉一条生命</p>
  <p>60秒内接越多越好！</p>
  <button id="pbtn" onclick="startGame()">开始游戏</button>
</div>
<script>
var C=document.getElementById('c'),ctx=C.getContext('2d');
C.width=window.innerWidth;C.height=window.innerHeight;
var W=C.width,H=C.height,HH=70;
var FRUITS=['🍎','🍊','🍋','🍇','🍓','🍑','🍍','🥭','🍌','🫐'];
var items=[],score=0,lives=5,timeLeft=60,running=false,raf,gameInt,spawnT;
var basketX=W/2,BW=90,BH2=44,floats=[];
function startGame(){
  document.getElementById('overlay').style.display='none';
  items=[];floats=[];score=0;lives=5;timeLeft=60;running=true;basketX=W/2;
  document.getElementById('sc').textContent=0;document.getElementById('hp').textContent=5;document.getElementById('tm').textContent=60;
  clearInterval(gameInt);clearTimeout(spawnT);
  gameInt=setInterval(function(){if(!running)return;timeLeft--;document.getElementById('tm').textContent=timeLeft;if(timeLeft<=0)endGame();},1000);
  doSpawn();cancelAnimationFrame(raf);loop();
}
function doSpawn(){
  if(!running)return;
  var bomb=Math.random()<0.18,r=20+Math.random()*14,x=r+Math.random()*(W-r*2),spd=2.5+Math.random()*2+score*0.015;
  items.push({x:x,y:HH,r:r,spd:spd,emoji:bomb?'💣':FRUITS[Math.floor(Math.random()*10)],bomb:bomb});
  var delay=Math.max(280,900-score*4);spawnT=setTimeout(doSpawn,delay);
}
function loop(){
  raf=requestAnimationFrame(loop);ctx.clearRect(0,0,W,H);
  var by=H-BH2-36,bx=basketX-BW/2;
  ctx.fillStyle='#2d5a1b';ctx.fillRect(0,H-50,W,50);
  ctx.fillStyle='#8b4513';ctx.beginPath();ctx.moveTo(bx,by);ctx.lineTo(bx+BW,by);ctx.lineTo(bx+BW-12,by+BH2);ctx.lineTo(bx+12,by+BH2);ctx.closePath();ctx.fill();ctx.strokeStyle='#a0522d';ctx.lineWidth=3;ctx.stroke();
  ctx.strokeStyle='#6b3410';ctx.lineWidth=1.5;for(var j=0;j<4;j++){ctx.beginPath();ctx.moveTo(bx+10+j*18,by);ctx.lineTo(bx+14+j*18,by+BH2);ctx.stroke();}
  ctx.strokeStyle='#8b4513';ctx.lineWidth=4;ctx.beginPath();ctx.arc(basketX,by-14,BW/3,Math.PI,0);ctx.stroke();
  floats=floats.filter(function(f){f.y-=2;f.alpha-=0.04;if(f.alpha<=0)return false;ctx.save();ctx.globalAlpha=f.alpha;ctx.fillStyle='#ffd700';ctx.font='bold 18px Arial';ctx.textAlign='center';ctx.fillText(f.txt,f.x,f.y);ctx.restore();return true;});
  items=items.filter(function(it){
    it.y+=it.spd;if(it.y>H+it.r)return false;
    if(it.y+it.r>by&&it.y<by+BH2&&it.x>bx-8&&it.x<bx+BW+8){
      if(it.bomb){lives--;document.getElementById('hp').textContent=Math.max(0,lives);floats.push({x:it.x,y:by,txt:'-💣',alpha:1});if(lives<=0)endGame();}
      else{score+=10;document.getElementById('sc').textContent=score;floats.push({x:it.x,y:by,txt:'+10',alpha:1});}
      return false;
    }
    ctx.font=(it.r*2)+'px serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(it.emoji,it.x,it.y);return true;
  });
  ctx.textAlign='left';ctx.textBaseline='alphabetic';
}
function handleMove(px){basketX=Math.max(BW/2,Math.min(W-BW/2,px));}
function endGame(){
  running=false;clearInterval(gameInt);clearTimeout(spawnT);
  var ov=document.getElementById('overlay');ov.style.display='flex';
  ov.innerHTML='<h2>🎉 游戏结束</h2><p style="font-size:1.5rem;color:#22c55e;margin:12px">得分: '+score+'</p><button onclick="startGame()" style="margin-top:20px;padding:14px 44px;background:#22c55e;color:#fff;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer">再来一次</button>';
}
C.addEventListener('touchstart',function(e){e.preventDefault();handleMove(e.touches[0].clientX);},{passive:false});
C.addEventListener('touchmove',function(e){e.preventDefault();handleMove(e.touches[0].clientX);},{passive:false});
C.addEventListener('mousemove',function(e){handleMove(e.clientX);});
</script>
</body>
</html>`;

// ============================================================
// 12 款种子游戏定义
// ============================================================

const SEED_GAMES = [
  // ── 6 款桌面端游戏 ────────────────────────────────────────
  {
    slug: 'seed-snake-classic',
    title: '贪吃蛇',
    description: '经典贪吃蛇游戏！用方向键或 WASD 控制蛇移动，吃到食物增长身体，碰到墙壁或自身即结束。看看你能吃多少？',
    gameType: 'casual',
    tags: ['休闲', '经典', '单人'],
    platform: 'desktop',
    qualityScore: 8.8,
    avgPlayTime: 4.0,
    playCount: BigInt(3241),
    likeCount: BigInt(412),
    forkCount: BigInt(97),
    htmlCode: SNAKE_HTML,
  },
  {
    slug: 'seed-breakout-classic',
    title: '打砖块',
    description: '经典街机打砖块！移动挡板反弹小球，消灭所有砖块即可过关。速度会越来越快，你能撑到最后吗？',
    gameType: 'casual',
    tags: ['街机', '经典', '技巧'],
    platform: 'desktop',
    qualityScore: 9.2,
    avgPlayTime: 6.0,
    playCount: BigInt(5102),
    likeCount: BigInt(731),
    forkCount: BigInt(153),
    htmlCode: BREAKOUT_HTML,
  },
  {
    slug: 'seed-memory-card-classic',
    title: '记忆翻牌',
    description: '考验记忆力的翻牌配对游戏！翻开两张相同的牌即可消除，用最少步数完成全部配对。共有4个难度等级。',
    gameType: 'puzzle',
    tags: ['益智', '记忆', '休闲'],
    platform: 'desktop',
    qualityScore: 8.5,
    avgPlayTime: 5.5,
    playCount: BigInt(2187),
    likeCount: BigInt(298),
    forkCount: BigInt(65),
    htmlCode: MEMORY_HTML,
  },
  {
    slug: 'seed-whack-mole-classic',
    title: '打地鼠',
    description: '疯狂打地鼠！地鼠从不同的洞里随机出现，快速点击消灭它们获得分数。60秒内打到尽可能多的地鼠！',
    gameType: 'casual',
    tags: ['休闲', '反应', '趣味'],
    platform: 'desktop',
    qualityScore: 9.0,
    avgPlayTime: 3.5,
    playCount: BigInt(4876),
    likeCount: BigInt(623),
    forkCount: BigInt(146),
    htmlCode: WHACK_MOLE_HTML,
  },
  {
    slug: 'seed-2048-classic',
    title: '2048',
    description: '经典数字合并益智游戏！滑动方向键合并相同数字，目标是凑出 2048。越大的数字越难合并，需要策略！',
    gameType: 'puzzle',
    tags: ['益智', '数字', '策略'],
    platform: 'desktop',
    qualityScore: 9.5,
    avgPlayTime: 8.0,
    playCount: BigInt(8934),
    likeCount: BigInt(1102),
    forkCount: BigInt(268),
    htmlCode: GAME_2048_HTML,
  },
  {
    slug: 'seed-space-shooter',
    title: '太空射击',
    description: '紧张刺激的太空射击游戏！驾驶飞船消灭来袭的外星舰队，躲避子弹，升级武器。你能守住地球吗？',
    gameType: 'action',
    tags: ['动作', '射击', '太空'],
    platform: 'desktop',
    qualityScore: 9.3,
    avgPlayTime: 7.0,
    playCount: BigInt(6521),
    likeCount: BigInt(876),
    forkCount: BigInt(195),
    htmlCode: SPACE_SHOOTER_HTML,
  },
  // ── 6 款移动端游戏 ────────────────────────────────────────
  {
    slug: 'seed-stack-tower',
    title: '叠叠高塔',
    description: '触摸放下移动的方块，只保留与下方重叠的部分，看你能叠多高！方块越叠越窄，失误即倒塌。挑战自己的反应极限！',
    gameType: 'casual',
    tags: ['休闲', '触控', '技巧', '单人'],
    platform: 'mobile',
    qualityScore: 9.1,
    avgPlayTime: 4.0,
    playCount: BigInt(5230),
    likeCount: BigInt(734),
    forkCount: BigInt(156),
    htmlCode: STACK_TOWER_HTML,
  },
  {
    slug: 'seed-fruit-ninja',
    title: '水果忍者',
    description: '手指滑动切开飞来的水果，小心别碰到炸弹！连切多个水果触发连击加分。60秒内追求最高分，体验畅快的切割快感！',
    gameType: 'action',
    tags: ['动作', '滑动', '反应', '休闲'],
    platform: 'mobile',
    qualityScore: 9.4,
    avgPlayTime: 4.5,
    playCount: BigInt(8910),
    likeCount: BigInt(1203),
    forkCount: BigInt(267),
    htmlCode: FRUIT_NINJA_HTML,
  },
  {
    slug: 'seed-bubble-pop',
    title: '泡泡消消',
    description: '五颜六色的气泡不断上升，快速点击让它们爆掉！连续点击触发连击倍数，红色炸弹气泡会扣分。60秒内能拿多少分？',
    gameType: 'casual',
    tags: ['休闲', '触控', '连击', '单人'],
    platform: 'mobile',
    qualityScore: 8.8,
    avgPlayTime: 3.5,
    playCount: BigInt(4650),
    likeCount: BigInt(612),
    forkCount: BigInt(139),
    htmlCode: BUBBLE_POP_HTML,
  },
  {
    slug: 'seed-star-blast',
    title: '消消星',
    description: '点击 2 个以上相邻的同色星星将其消除！消除的星星越多，得分越高。星星会自动下落填补空缺，60秒内追求最高分！',
    gameType: 'puzzle',
    tags: ['益智', '消除', '策略', '触控'],
    platform: 'mobile',
    qualityScore: 8.9,
    avgPlayTime: 5.0,
    playCount: BigInt(3870),
    likeCount: BigInt(521),
    forkCount: BigInt(116),
    htmlCode: STAR_BLAST_HTML,
  },
  {
    slug: 'seed-rhythm-tap',
    title: '节奏达人',
    description: '彩色圆圈出现并缩小，在圆圈消失前点击它！连续点击触发连击加成。圆圈越来越多越来越快，手速和反应力的终极考验！',
    gameType: 'casual',
    tags: ['反应', '触控', '连击', '挑战'],
    platform: 'mobile',
    qualityScore: 9.2,
    avgPlayTime: 4.0,
    playCount: BigInt(6340),
    likeCount: BigInt(891),
    forkCount: BigInt(190),
    htmlCode: RHYTHM_TAP_HTML,
  },
  {
    slug: 'seed-catch-fruits',
    title: '接水果',
    description: '拖动篮子接住从天而降的水果！水果越接越多越快，小心炸弹会扣掉宝贵的生命。60秒内接越多越好，挑战高分！',
    gameType: 'casual',
    tags: ['休闲', '拖拽', '触控', '反应'],
    platform: 'mobile',
    qualityScore: 8.7,
    avgPlayTime: 3.8,
    playCount: BigInt(4120),
    likeCount: BigInt(563),
    forkCount: BigInt(123),
    htmlCode: CATCH_FRUITS_HTML,
  },
  // ── 5 款精品手机游戏（从文件加载）──────────────────────────────
  {
    slug: 'premium-pixel-dungeon',
    title: '像素地牢',
    description:
      'Roguelike地牢探险！程序生成地牢地图，回合制策略战斗。5层地牢逐层深入，收集武器装备，' +
      '使用药水恢复生命，升级角色属性，最终击败第5层的远古巨龙！',
    gameType: 'puzzle',
    tags: ['策略', 'RPG', 'Roguelike', '回合制', '地牢'],
    platform: 'mobile',
    qualityScore: 9.5,
    avgPlayTime: 15.0,
    playCount: BigInt(12800),
    likeCount: BigInt(2340),
    forkCount: BigInt(456),
    htmlCode: loadGameFile('pixel-dungeon.html'),
  },
  {
    slug: 'premium-tower-defense',
    title: '塔防战线',
    description:
      '经典策略塔防！4种防御塔各具特色，3级升级系统深度策略搭配。' +
      '20波敌人逐渐增强，每5波出现BOSS挑战。合理布局决定胜负！',
    gameType: 'puzzle',
    tags: ['策略', '塔防', '回合制', '升级'],
    platform: 'mobile',
    qualityScore: 9.4,
    avgPlayTime: 12.0,
    playCount: BigInt(10500),
    likeCount: BigInt(1870),
    forkCount: BigInt(380),
    htmlCode: loadGameFile('tower-defense.html'),
  },
  {
    slug: 'premium-gravity-flip',
    title: '重力翻转',
    description:
      '赛博朋克风重力翻转平台跳跃！点击屏幕切换重力方向，躲避障碍物。' +
      '10个精心设计关卡，3星收集系统，连击加分，挑战最快通关时间！',
    gameType: 'action',
    tags: ['动作', '平台跳跃', '物理', '关卡', '挑战'],
    platform: 'mobile',
    qualityScore: 9.3,
    avgPlayTime: 8.0,
    playCount: BigInt(15200),
    likeCount: BigInt(2650),
    forkCount: BigInt(520),
    htmlCode: loadGameFile('gravity-flip.html'),
  },
  {
    slug: 'premium-space-miner',
    title: '星际矿工',
    description:
      '太空采矿资源管理！采集4种稀有矿石，返回基地升级飞船。' +
      '4大升级系统，躲避陨石雨、太空海盗和黑洞，日夜交替影响资源分布！',
    gameType: 'action',
    tags: ['动作', '资源管理', '太空', '升级', '生存'],
    platform: 'mobile',
    qualityScore: 9.2,
    avgPlayTime: 10.0,
    playCount: BigInt(8900),
    likeCount: BigInt(1520),
    forkCount: BigInt(310),
    htmlCode: loadGameFile('space-miner.html'),
  },
  {
    slug: 'premium-sudoku-master',
    title: '数独大师',
    description:
      '精品数独解谜！算法生成唯一解谜题，4难度等级。' +
      '笔记系统、提示、撤销重做、自动检查、冲突高亮、计时统计，挑战逻辑思维！',
    gameType: 'puzzle',
    tags: ['益智', '数独', '逻辑', '经典', '解谜'],
    platform: 'mobile',
    qualityScore: 9.6,
    avgPlayTime: 20.0,
    playCount: BigInt(18500),
    likeCount: BigInt(3200),
    forkCount: BigInt(680),
    htmlCode: loadGameFile('sudoku-master.html'),
  },
];

// ============================================================
// 业务初始化主逻辑
// ============================================================

async function initSeedGames() {
  console.log('🎮 种子游戏业务初始化\n' + '═'.repeat(50));

  // Step 1: 确保系统种子创作者存在
  const SEED_USER = {
    username: 'seed_creator',
    email: 'seed@gamevallies.com',
    displayName: '平台精选游戏',
    bio: '平台官方精选游戏，汇聚多款经典 HTML5 小游戏。',
  };

  let seedUser = await prisma.user.findUnique({
    where: { username: SEED_USER.username },
  });

  if (!seedUser) {
    const passwordHash = await bcrypt.hash('SeedCreator@2026!', 12);
    seedUser = await prisma.user.create({
      data: {
        id: randomUUID(),
        username: SEED_USER.username,
        email: SEED_USER.email,
        displayName: SEED_USER.displayName,
        bio: SEED_USER.bio,
        passwordHash,
        role: 'creator',
        followerCount: 9999,
        followingCount: 0,
        gameCount: SEED_GAMES.length,
        totalPlays: SEED_GAMES.reduce((s, g) => s + g.playCount, BigInt(0)),
      },
    });
    console.log(`✅ 创建种子用户: ${seedUser.username} (${seedUser.id})`);
  } else {
    console.log(`ℹ️  种子用户已存在: ${seedUser.username} (${seedUser.id})`);
  }

  // Step 2: 写入游戏（upsert 幂等）
  console.log(`\n📦 写入 ${SEED_GAMES.length} 款种子游戏...\n`);

  const results: Array<{ title: string; slug: string; id: string; isNew: boolean }> = [];

  for (const def of SEED_GAMES) {
    const publishedAt = new Date(
      Date.now() - Math.floor(Math.random() * 60 + 30) * 86400000,
    );

    // 查找是否已存在
    const existing = await prisma.game.findUnique({ where: { slug: def.slug } });

    const gameId = existing?.id ?? randomUUID();
    const bundleId = randomUUID();

    const game = await prisma.game.upsert({
      where: { slug: def.slug },
      update: {
        playCount: def.playCount,
        likeCount: def.likeCount,
        forkCount: def.forkCount,
        qualityScore: def.qualityScore,
        avgPlayTime: def.avgPlayTime,
      },
      create: {
        id: gameId,
        authorId: seedUser.id,
        title: def.title,
        description: def.description,
        slug: def.slug,
        status: 'published',
        gameType: def.gameType,
        tags: def.tags,
        codeBundleId: bundleId,
        version: 1,
        playCount: def.playCount,
        likeCount: def.likeCount,
        forkCount: def.forkCount,
        qualityScore: def.qualityScore,
        avgPlayTime: def.avgPlayTime,
        publishedAt,
        createdAt: publishedAt,
      },
    });

    // 写入或更新 GameBundle
    await prisma.gameBundle.upsert({
      where: { uk_game_version: { gameId: game.id, version: 1 } },
      update: {
        htmlCode: def.htmlCode,
        codeSizeBytes: Buffer.byteLength(def.htmlCode, 'utf8'),
      },
      create: {
        id: bundleId,
        gameId: game.id,
        version: 1,
        htmlCode: def.htmlCode,
        codeSizeBytes: Buffer.byteLength(def.htmlCode, 'utf8'),
        spec: { gameType: def.gameType, tags: def.tags, platform: def.platform },
        generationMeta: { method: 'seed', aiAssisted: false, seedVersion: '1.0.0' },
        metadata: { title: def.title, description: def.description, platform: def.platform },
      },
    });

    const isNew = !existing;
    const icon = isNew ? '✅' : '🔄';
    const label = isNew ? '新建' : '更新';
    console.log(
      `${icon} [${label}][${def.platform}] ${def.title}\n` +
        `   slug: ${def.slug}\n` +
        `   id: ${game.id}\n` +
        `   质量分: ${def.qualityScore}  游玩: ${Number(def.playCount).toLocaleString()}  点赞: ${Number(def.likeCount).toLocaleString()}\n`,
    );

    results.push({ title: def.title, slug: def.slug, id: game.id, isNew });
  }

  // Step 3: 更新种子用户统计
  const totalPlayCount = SEED_GAMES.reduce((s, g) => s + g.playCount, BigInt(0));
  await prisma.user.update({
    where: { id: seedUser.id },
    data: {
      gameCount: SEED_GAMES.length,
      totalPlays: totalPlayCount,
    },
  });

  // Step 4: 汇总
  const newCount = results.filter((r) => r.isNew).length;
  const updateCount = results.filter((r) => !r.isNew).length;
  const desktopCount = SEED_GAMES.filter((g) => g.platform === 'desktop').length;
  const mobileCount = SEED_GAMES.filter((g) => g.platform === 'mobile').length;

  console.log('═'.repeat(50));
  console.log(`
╔══════════════════════════════════════════════╗
║       🎉 种子游戏初始化完成                   ║
╠══════════════════════════════════════════════╣
║  新建游戏:    ${String(newCount).padEnd(30)}║
║  更新游戏:    ${String(updateCount).padEnd(30)}║
║  桌面端游戏:  ${String(desktopCount).padEnd(30)}║
║  移动端游戏:  ${String(mobileCount).padEnd(30)}║
║  种子用户:    ${seedUser.username.padEnd(30)}║
╠══════════════════════════════════════════════╣
║  游戏列表:                                    ║`);
  results.forEach((r) => {
    console.log(`║    ${r.title.padEnd(40)}  ║`);
  });
  console.log(`╚══════════════════════════════════════════════╝
`);
}

initSeedGames()
  .catch((e) => {
    console.error('❌ 种子游戏初始化失败:', e.message);
    console.error(e.stack);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
