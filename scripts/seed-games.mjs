/**
 * 游戏测试数据种子脚本
 * 向 PostgreSQL + MongoDB 写入 6 个完整的 HTML5 游戏
 */

import { PrismaClient } from '@prisma/client';
import { MongoClient } from 'mongodb';
import { randomUUID } from 'crypto';
import { createHash } from 'crypto';

const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://gamevallies:playforge_dev_2026@localhost:5433/playforge';
const MONGO_URL = process.env.MONGO_URL || 'mongodb://gamevallies:playforge_dev_2026@localhost:27017/gamevallies?authSource=admin';
const APP_URL = process.env.APP_URL || 'http://localhost:3002';

const prisma = new PrismaClient({ datasources: { db: { url: DATABASE_URL } } });

// ── HTML Games ─────────────────────────────────────────────────────────────

const GAMES = [
  {
    title: '贪吃蛇',
    description: '经典贪吃蛇游戏！用方向键或 WASD 控制蛇移动，吃到食物增长身体，碰到墙壁或自身即结束。看看你能吃多少？',
    gameType: 'casual',
    tags: ['休闲', '经典', '单人'],
    qualityScore: 88,
    playCount: 3241,
    likeCount: 412,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>贪吃蛇</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #1a1a2e; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; font-family: 'Arial', sans-serif; color: #fff; }
h1 { font-size: 2rem; margin-bottom: 10px; color: #16c79a; text-shadow: 0 0 20px #16c79a; }
#score-board { display: flex; gap: 40px; margin-bottom: 15px; font-size: 1.1rem; }
.score-item { text-align: center; }
.score-item span { display: block; font-size: 1.8rem; font-weight: bold; color: #f5a623; }
canvas { border: 3px solid #16c79a; border-radius: 8px; box-shadow: 0 0 30px rgba(22,199,154,0.4); }
#message { margin-top: 15px; font-size: 1.1rem; color: #aaa; }
#btn { margin-top: 10px; padding: 10px 30px; background: #16c79a; color: #1a1a2e; border: none; border-radius: 25px; font-size: 1rem; font-weight: bold; cursor: pointer; transition: transform 0.1s; }
#btn:hover { transform: scale(1.05); }
</style>
</head>
<body>
<h1>🐍 贪吃蛇</h1>
<div id="score-board">
  <div class="score-item">得分<span id="score">0</span></div>
  <div class="score-item">最高<span id="best">0</span></div>
  <div class="score-item">长度<span id="len">3</span></div>
</div>
<canvas id="c" width="400" height="400"></canvas>
<div id="message">按空格键 / 点击开始</div>
<button id="btn" onclick="startGame()">开始游戏</button>
<script>
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const GRID = 20, SIZE = 400 / GRID;
let snake, dir, food, score, best = 0, running = false, interval;

function startGame() {
  snake = [{x:10,y:10},{x:9,y:10},{x:8,y:10}];
  dir = {x:1,y:0};
  score = 0;
  running = true;
  placeFood();
  update();
  clearInterval(interval);
  interval = setInterval(loop, 130);
  document.getElementById('message').textContent = '方向键 / WASD 控制';
  document.getElementById('btn').textContent = '重新开始';
}

function placeFood() {
  do { food = {x:Math.floor(Math.random()*GRID), y:Math.floor(Math.random()*GRID)}; }
  while (snake.some(s=>s.x===food.x&&s.y===food.y));
}

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
  ctx.fillStyle = '#0f0f23';
  ctx.fillRect(0, 0, 400, 400);
  // grid lines
  ctx.strokeStyle = 'rgba(255,255,255,0.03)';
  for(let i=0;i<GRID;i++) { ctx.beginPath(); ctx.moveTo(i*SIZE,0); ctx.lineTo(i*SIZE,400); ctx.stroke(); ctx.beginPath(); ctx.moveTo(0,i*SIZE); ctx.lineTo(400,i*SIZE); ctx.stroke(); }
  // food
  ctx.fillStyle = '#f5a623';
  ctx.shadowColor = '#f5a623'; ctx.shadowBlur = 15;
  ctx.beginPath(); ctx.arc(food.x*SIZE+SIZE/2, food.y*SIZE+SIZE/2, SIZE/2-2, 0, Math.PI*2); ctx.fill();
  ctx.shadowBlur = 0;
  // snake
  snake.forEach((s,i) => {
    const ratio = 1 - i/snake.length * 0.7;
    ctx.fillStyle = \`rgba(22, \${Math.floor(199*ratio)}, 154, \${ratio})\`;
    ctx.beginPath();
    ctx.roundRect(s.x*SIZE+1, s.y*SIZE+1, SIZE-2, SIZE-2, 4);
    ctx.fill();
  });
  document.getElementById('score').textContent = score;
  document.getElementById('best').textContent = best;
  document.getElementById('len').textContent = snake.length;
}

document.addEventListener('keydown', e => {
  if (!running && e.code === 'Space') { startGame(); return; }
  const map = {ArrowUp:{x:0,y:-1},ArrowDown:{x:0,y:1},ArrowLeft:{x:-1,y:0},ArrowRight:{x:1,y:0},KeyW:{x:0,y:-1},KeyS:{x:0,y:1},KeyA:{x:-1,y:0},KeyD:{x:1,y:0}};
  const d = map[e.code];
  if (d && !(d.x===-dir.x && d.y===-dir.y)) { dir = d; e.preventDefault(); }
});

update();
</script>
</body>
</html>`,
  },
  {
    title: '打砖块',
    description: '经典街机打砖块！移动挡板反弹小球，消灭所有砖块即可过关。速度会越来越快，你能撑到最后吗？',
    gameType: 'casual',
    tags: ['街机', '经典', '技巧'],
    qualityScore: 92,
    playCount: 5102,
    likeCount: 731,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>打砖块</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #0d1117; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; font-family: Arial, sans-serif; color: #fff; }
h1 { font-size: 2rem; margin-bottom: 8px; color: #58a6ff; }
#info { display: flex; gap: 40px; margin-bottom: 12px; font-size: 1rem; }
.v { font-size: 1.6rem; font-weight: bold; color: #ffa657; }
canvas { border: 2px solid #30363d; border-radius: 6px; cursor: none; }
#msg { margin-top: 12px; font-size: 1rem; color: #8b949e; }
</style>
</head>
<body>
<h1>🧱 打砖块</h1>
<div id="info">
  <div>得分 <span class="v" id="sc">0</span></div>
  <div>生命 <span class="v" id="lv">3</span></div>
  <div>关卡 <span class="v" id="lv2">1</span></div>
</div>
<canvas id="c" width="480" height="400"></canvas>
<div id="msg">点击画布开始 · 移动鼠标控制挡板</div>
<script>
const C = document.getElementById('c'), ctx = C.getContext('2d');
const W = 480, H = 400;
let bx=W/2, by=H-60, bdx=3.5, bdy=-3.5;
let px=W/2-40, pw=80, ph=10, py=H-20;
let lives=3, score=0, level=1, running=false, won=false, lost=false;
const COLS=10, ROWS=5;
const COLORS=['#ff6b6b','#ffa657','#ffd700','#7ce38b','#58a6ff','#d2a8ff'];
let bricks=[];

function initBricks(){
  bricks=[];
  for(let r=0;r<ROWS;r++) for(let c=0;c<COLS;c++)
    bricks.push({x:c*46+5,y:r*24+40,w:42,h:20,alive:true,color:COLORS[r%COLORS.length],hp:r<2?1:r<4?2:3});
}

function reset(){
  bx=W/2; by=H-60; const spd=3.5+level*0.3;
  bdx=(Math.random()>0.5?1:-1)*spd; bdy=-spd;
  if(!won) initBricks();
  won=false; lost=false;
}

function start(){ running=true; reset(); loop(); }

C.addEventListener('mousemove',e=>{ const r=C.getBoundingClientRect(); px=e.clientX-r.left-pw/2; px=Math.max(0,Math.min(W-pw,px)); });
C.addEventListener('touchmove',e=>{ e.preventDefault(); const r=C.getBoundingClientRect(); px=e.touches[0].clientX-r.left-pw/2; px=Math.max(0,Math.min(W-pw,px)); },{passive:false});
C.addEventListener('click',()=>{ if(!running) start(); });

function loop(){
  if(!running) return;
  ctx.fillStyle='#0d1117'; ctx.fillRect(0,0,W,H);
  // paddle
  ctx.fillStyle='#58a6ff';
  ctx.beginPath(); ctx.roundRect(px,py,pw,ph,5); ctx.fill();
  // ball
  const grad=ctx.createRadialGradient(bx,by,2,bx,by,8);
  grad.addColorStop(0,'#fff'); grad.addColorStop(1,'#ffa657');
  ctx.fillStyle=grad; ctx.beginPath(); ctx.arc(bx,by,8,0,Math.PI*2); ctx.fill();
  // bricks
  bricks.forEach(b=>{
    if(!b.alive) return;
    ctx.fillStyle=b.hp===1?b.color:b.hp===2?b.color+'cc':b.color+'88';
    ctx.beginPath(); ctx.roundRect(b.x,b.y,b.w,b.h,3); ctx.fill();
    if(b.hp>1){ ctx.fillStyle='rgba(255,255,255,0.4)'; ctx.font='bold 10px Arial'; ctx.fillText(b.hp,b.x+b.w/2-4,b.y+b.h/2+4); }
  });
  // physics
  bx+=bdx; by+=bdy;
  if(bx<8||bx>W-8) bdx=-bdx;
  if(by<8) bdy=-bdy;
  if(by>H){ lives--; document.getElementById('lv').textContent=lives; if(lives<=0){ running=false; ctx.fillStyle='rgba(0,0,0,0.7)'; ctx.fillRect(0,0,W,H); ctx.fillStyle='#ff6b6b'; ctx.font='bold 36px Arial'; ctx.textAlign='center'; ctx.fillText('游戏结束 💀',W/2,H/2); ctx.fillStyle='#fff'; ctx.font='18px Arial'; ctx.fillText('点击重新开始',W/2,H/2+40); ctx.textAlign='left'; lives=3; score=0; level=1; document.getElementById('sc').textContent=0; document.getElementById('lv').textContent=3; document.getElementById('lv2').textContent=1; return; } reset(); }
  if(by>py-8&&by<py+ph&&bx>px-8&&bx<px+pw+8){ bdy=-Math.abs(bdy); const rel=(bx-(px+pw/2))/(pw/2); bdx=rel*5; }
  bricks.forEach(b=>{
    if(!b.alive) return;
    if(bx>b.x-8&&bx<b.x+b.w+8&&by>b.y-8&&by<b.y+b.h+8){
      b.hp--; if(b.hp<=0){ b.alive=false; score+=10+level*5; document.getElementById('sc').textContent=score; }
      if(bx<b.x||bx>b.x+b.w) bdx=-bdx; else bdy=-bdy;
    }
  });
  if(bricks.every(b=>!b.alive)){ level++; document.getElementById('lv2').textContent=level; reset(); }
  requestAnimationFrame(loop);
}

initBricks(); ctx.fillStyle='#0d1117'; ctx.fillRect(0,0,W,H);
ctx.fillStyle='#58a6ff'; ctx.font='bold 22px Arial'; ctx.textAlign='center'; ctx.fillText('点击开始游戏',W/2,H/2); ctx.textAlign='left';
</script>
</body>
</html>`,
  },
  {
    title: '记忆翻牌',
    description: '考验记忆力的翻牌配对游戏！翻开两张相同的牌即可消除，用最少步数完成全部配对。共有4个难度等级。',
    gameType: 'puzzle',
    tags: ['益智', '记忆', '休闲'],
    qualityScore: 85,
    playCount: 2187,
    likeCount: 298,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>记忆翻牌</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; font-family: Arial, sans-serif; color: #fff; }
h1 { font-size: 1.8rem; margin-bottom: 10px; color: #e94560; }
#controls { display: flex; gap: 10px; margin-bottom: 15px; flex-wrap: wrap; justify-content: center; }
.diff-btn { padding: 6px 16px; border: 2px solid #e94560; background: transparent; color: #fff; border-radius: 20px; cursor: pointer; transition: all 0.2s; font-size: 0.85rem; }
.diff-btn.active, .diff-btn:hover { background: #e94560; }
#info { display: flex; gap: 30px; margin-bottom: 15px; font-size: 1rem; }
.iv { font-size: 1.5rem; font-weight: bold; color: #ffd700; }
#board { display: grid; gap: 8px; }
.card { width: 70px; height: 70px; border-radius: 10px; cursor: pointer; perspective: 600px; }
.card-inner { width: 100%; height: 100%; position: relative; transform-style: preserve-3d; transition: transform 0.4s; }
.card.flipped .card-inner { transform: rotateY(180deg); }
.card-front, .card-back { position: absolute; width: 100%; height: 100%; backface-visibility: hidden; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 2rem; }
.card-front { background: linear-gradient(135deg, #0f3460, #16213e); border: 2px solid #e94560; }
.card-back { background: linear-gradient(135deg, #1a1a2e, #0f3460); border: 2px solid #ffd700; transform: rotateY(180deg); }
.card.matched .card-back { background: linear-gradient(135deg, #1a6b3c, #0f3460); border-color: #4caf50; }
#result { margin-top: 15px; font-size: 1.1rem; color: #4caf50; font-weight: bold; }
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

function setDiff(c, r, name) {
  cols=c; rows=r;
  document.querySelectorAll('.diff-btn').forEach((b,i)=>b.classList.toggle('active',b.textContent.startsWith(name)));
  init();
}

function init() {
  clearInterval(timer); sec=0; moves=0; pairs=0; flipped=[]; lock=false;
  document.getElementById('moves').textContent=0;
  document.getElementById('pairs').textContent=0;
  document.getElementById('time').textContent='0s';
  document.getElementById('result').textContent='';
  total = Math.floor(cols*rows/2);
  const emojis = EMOJIS.slice(0,total);
  cards = shuffle([...emojis,...emojis]).slice(0,cols*rows);
  if(cols*rows%2!==0) cards.push('⚡');
  render();
  timer = setInterval(()=>{ sec++; document.getElementById('time').textContent=sec+'s'; },1000);
}

function shuffle(arr) { for(let i=arr.length-1;i>0;i--){ const j=Math.floor(Math.random()*(i+1)); [arr[i],arr[j]]=[arr[j],arr[i]]; } return arr; }

function render() {
  const board = document.getElementById('board');
  board.style.gridTemplateColumns = \`repeat(\${cols}, 70px)\`;
  board.innerHTML = '';
  cards.forEach((emoji,i)=>{
    const card = document.createElement('div');
    card.className='card';
    card.innerHTML=\`<div class="card-inner"><div class="card-front">❓</div><div class="card-back">\${emoji}</div></div>\`;
    card.addEventListener('click',()=>flip(card,i));
    board.appendChild(card);
  });
}

function flip(card, idx) {
  if(lock || card.classList.contains('flipped') || card.classList.contains('matched')) return;
  card.classList.add('flipped');
  flipped.push({card,idx});
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
</html>`,
  },
  {
    title: '打地鼠',
    description: '疯狂打地鼠！地鼠从不同的洞里随机出现，快速点击消灭它们获得分数。60秒内打到尽可能多的地鼠！',
    gameType: 'casual',
    tags: ['休闲', '反应', '趣味'],
    qualityScore: 90,
    playCount: 4876,
    likeCount: 623,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>打地鼠</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #2d5016; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; font-family: Arial, sans-serif; color: #fff; user-select: none; }
h1 { font-size: 2rem; margin-bottom: 10px; text-shadow: 2px 2px 4px rgba(0,0,0,0.5); }
#hud { display: flex; gap: 40px; margin-bottom: 20px; background: rgba(0,0,0,0.4); padding: 10px 30px; border-radius: 30px; }
.hud-item { text-align: center; font-size: 0.85rem; color: #ccc; }
.hud-val { font-size: 1.8rem; font-weight: bold; color: #ffd700; display: block; }
#grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; padding: 20px; background: rgba(0,0,0,0.3); border-radius: 20px; }
.hole { width: 110px; height: 90px; background: #1a3009; border-radius: 50% 50% 45% 45%; border: 4px solid #4a7c2f; position: relative; overflow: hidden; cursor: pointer; transition: transform 0.1s; }
.hole:active { transform: scale(0.95); }
.mole { position: absolute; bottom: -110%; left: 50%; transform: translateX(-50%); font-size: 3rem; transition: bottom 0.15s ease-out; line-height: 1; }
.hole.up .mole { bottom: 5%; }
.hole.bonk .mole { filter: brightness(0.5) sepia(1) hue-rotate(-20deg); }
#start-btn { margin-top: 20px; padding: 12px 40px; background: #ffd700; color: #1a1a1a; border: none; border-radius: 30px; font-size: 1.1rem; font-weight: bold; cursor: pointer; transition: transform 0.1s; }
#start-btn:hover { transform: scale(1.05); }
#result { margin-top: 15px; font-size: 1.2rem; color: #ffd700; font-weight: bold; }
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
  const hole = document.createElement('div');
  hole.className = 'hole';
  const moleType = MOLES[i % MOLES.length];
  hole.innerHTML = \`<span class="mole">\${moleType}</span>\`;
  hole.addEventListener('click', () => whack(hole, i));
  grid.appendChild(hole);
  holes.push(hole);
}

function startGame(){
  holes.forEach(h => { h.classList.remove('up','bonk'); });
  moleTimers.forEach(clearTimeout);
  moleTimers = [];
  clearInterval(gameTimer);
  score = 0; miss = 0; combo = 1; timeLeft = 60; running = true;
  document.getElementById('sc').textContent = 0;
  document.getElementById('combo').textContent = 'x1';
  document.getElementById('miss').textContent = 0;
  document.getElementById('result').textContent = '';
  document.getElementById('start-btn').textContent = '重新开始';
  gameTimer = setInterval(() => {
    timeLeft--;
    document.getElementById('tm').textContent = timeLeft;
    if(timeLeft <= 0){ endGame(); }
  }, 1000);
  scheduleMole();
}

function scheduleMole(){
  if(!running) return;
  const idx = Math.floor(Math.random() * 9);
  const hole = holes[idx];
  if(!hole.classList.contains('up')){
    hole.classList.add('up');
    const duration = Math.max(600, 1200 - score * 3);
    const t = setTimeout(() => {
      if(hole.classList.contains('up')){ hole.classList.remove('up'); miss++; document.getElementById('miss').textContent = miss; }
    }, duration);
    moleTimers.push(t);
  }
  const next = Math.max(200, 700 - score * 2);
  moleTimers.push(setTimeout(scheduleMole, next));
}

function whack(hole, idx){
  if(!running || !hole.classList.contains('up')) return;
  hole.classList.remove('up'); hole.classList.add('bonk');
  setTimeout(() => hole.classList.remove('bonk'), 400);
  clearTimeout(comboTimer);
  comboTimer = setTimeout(() => { combo = 1; document.getElementById('combo').textContent = 'x1'; }, 1500);
  const pts = 10 * combo;
  score += pts; combo = Math.min(combo + 1, 10);
  document.getElementById('sc').textContent = score;
  document.getElementById('combo').textContent = 'x' + combo;
  // float score
  const el = document.createElement('div');
  el.style.cssText = \`position:fixed;color:#ffd700;font-weight:bold;font-size:1.2rem;pointer-events:none;z-index:999;\`;
  el.textContent = '+' + pts;
  const r = hole.getBoundingClientRect();
  el.style.left = r.left + r.width/2 + 'px';
  el.style.top = r.top + 'px';
  document.body.appendChild(el);
  let op = 1, ty = 0;
  const anim = setInterval(() => { op -= 0.05; ty -= 2; el.style.opacity = op; el.style.transform = \`translateY(\${ty}px)\`; if(op <= 0){ clearInterval(anim); el.remove(); }}, 30);
}

function endGame(){
  running = false; clearInterval(gameTimer); moleTimers.forEach(clearTimeout);
  holes.forEach(h => h.classList.remove('up'));
  document.getElementById('result').textContent = \`🎉 游戏结束！最终得分: \${score}，未中: \${miss}\`;
}
</script>
</body>
</html>`,
  },
  {
    title: '2048',
    description: '经典数字合并益智游戏！滑动方向键合并相同数字，目标是凑出 2048。越大的数字越难合并，需要策略！',
    gameType: 'puzzle',
    tags: ['益智', '数字', '策略'],
    qualityScore: 95,
    playCount: 8934,
    likeCount: 1102,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>2048</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #faf8ef; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; font-family: 'Arial', sans-serif; }
h1 { font-size: 3rem; font-weight: bold; color: #776e65; margin-bottom: 5px; }
#top { display: flex; gap: 15px; margin-bottom: 15px; align-items: center; }
.score-box { background: #bbada0; color: #fff; padding: 8px 20px; border-radius: 6px; text-align: center; min-width: 80px; }
.score-box .label { font-size: 0.7rem; text-transform: uppercase; }
.score-box .val { font-size: 1.4rem; font-weight: bold; }
#new-btn { padding: 10px 20px; background: #8f7a66; color: #fff; border: none; border-radius: 6px; font-size: 0.9rem; font-weight: bold; cursor: pointer; }
#board { background: #bbada0; border-radius: 8px; padding: 10px; display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; }
.cell { width: 90px; height: 90px; background: rgba(238,228,218,0.35); border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: 1.8rem; font-weight: bold; transition: all 0.1s; }
.t2{background:#eee4da;color:#776e65}.t4{background:#ede0c8;color:#776e65}.t8{background:#f2b179;color:#fff}.t16{background:#f59563;color:#fff}.t32{background:#f67c5f;color:#fff}.t64{background:#f65e3b;color:#fff}.t128{background:#edcf72;color:#fff;font-size:1.5rem}.t256{background:#edcc61;color:#fff;font-size:1.5rem}.t512{background:#edc850;color:#fff;font-size:1.5rem}.t1024{background:#edc53f;color:#fff;font-size:1.2rem}.t2048{background:#edc22e;color:#fff;font-size:1.2rem}
#msg { margin-top: 15px; font-size: 1.1rem; color: #776e65; }
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

function newGame(){ grid = Array(4).fill(null).map(()=>Array(4).fill(0)); score=0; won=false; addTile(); addTile(); render(); document.getElementById('msg').textContent='用方向键或滑动控制'; }

function addTile(){
  const empty=[];
  grid.forEach((r,i)=>r.forEach((v,j)=>{ if(!v) empty.push([i,j]); }));
  if(!empty.length) return;
  const [r,c]=empty[Math.floor(Math.random()*empty.length)];
  grid[r][c]=Math.random()<0.9?2:4;
}

function render(){
  const board=document.getElementById('board');
  board.innerHTML='';
  grid.forEach(row=>row.forEach(v=>{
    const cell=document.createElement('div');
    cell.className='cell'+(v?' t'+v:'');
    cell.textContent=v||'';
    board.appendChild(cell);
  }));
  document.getElementById('sc').textContent=score;
  if(score>best){ best=score; document.getElementById('best').textContent=best; }
}

function slide(row){
  let r=row.filter(v=>v), pts=0;
  for(let i=0;i<r.length-1;i++) if(r[i]===r[i+1]){ r[i]*=2; pts+=r[i]; r.splice(i+1,1); i++; }
  while(r.length<4) r.push(0);
  score+=pts; return r;
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
</html>`,
  },
  {
    title: '太空射击',
    description: '紧张刺激的太空射击游戏！驾驶飞船消灭来袭的外星舰队，躲避子弹，升级武器。你能守住地球吗？',
    gameType: 'casual',
    tags: ['动作', '射击', '太空'],
    qualityScore: 93,
    playCount: 6521,
    likeCount: 876,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>太空射击</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #000; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; font-family: Arial, sans-serif; overflow: hidden; }
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
  <p>空格 / 点击 自动射击</p>
  <p>消灭所有外星人！</p>
  <button id="play-btn" onclick="startGame()">开始游戏</button>
</div>
</div>
<script>
const C = document.getElementById('c'), ctx = C.getContext('2d');
const W = 400, H = 520;
let player, bullets, enemies, eBullets, particles, score, lives, level, running, keys = {}, shootCooldown = 0, enemyDir = 1, enemyMoveTimer = 0, stars = [];

// Stars
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

function loop(){
  if(!running) return;
  update(); draw();
  requestAnimationFrame(loop);
}

function update(){
  // Player
  if(keys['ArrowLeft']||keys['a']||keys['A']) player.x = Math.max(15, player.x-player.speed);
  if(keys['ArrowRight']||keys['d']||keys['D']) player.x = Math.min(W-15, player.x+player.speed);
  // Auto shoot
  shootCooldown--;
  if((keys[' ']||keys['f']||keys['F']||touchShoot) && shootCooldown<=0){
    bullets.push({x:player.x, y:player.y-15, w:3, h:12, dy:-9});
    if(level>=3) bullets.push({x:player.x-12, y:player.y-10, w:3, h:12, dy:-9},{x:player.x+12, y:player.y-10, w:3, h:12, dy:-9});
    shootCooldown = 10;
  }
  // Bullets
  bullets = bullets.filter(b=>{ b.y+=b.dy; return b.y>0; });
  eBullets = eBullets.filter(b=>{ b.x+=b.dx||0; b.y+=b.dy; return b.y<H; });
  // Enemy move
  enemyMoveTimer++;
  const spd = 0.4 + level*0.15;
  if(enemyMoveTimer > 60/spd){
    enemyMoveTimer = 0;
    const alive = enemies.filter(e=>e.alive);
    const minX = Math.min(...alive.map(e=>e.x)), maxX = Math.max(...alive.map(e=>e.x+e.w));
    if((enemyDir>0&&maxX>W-10)||(enemyDir<0&&minX<10)){ enemyDir=-enemyDir; alive.forEach(e=>e.y+=15); }
    alive.forEach(e=>e.x+=enemyDir*8);
    // Enemy shoot
    alive.forEach(e=>{ e.shootTimer--; if(e.shootTimer<=0){ e.shootTimer=60+Math.random()*80/level; const angle=Math.atan2(player.y-e.y,player.x-e.x); eBullets.push({x:e.x+e.w/2,y:e.y+e.h,dx:Math.cos(angle)*3,dy:Math.abs(Math.sin(angle))*3+2,w:4,h:4}); } });
  }
  // Collisions bullets -> enemies
  bullets.forEach((b,bi)=>{
    enemies.forEach(e=>{ if(!e.alive) return;
      if(b.x>e.x&&b.x<e.x+e.w&&b.y>e.y&&b.y<e.y+e.h){ e.hp--; b.y=-100; if(e.hp<=0){ e.alive=false; score+=10*(e.type+1); addParticles(e.x+e.w/2,e.y+e.h/2,e.type); } }
    });
  });
  // eBullets -> player
  eBullets.forEach(b=>{
    if(Math.abs(b.x-player.x)<14&&Math.abs(b.y-player.y)<12){ b.y=H+1; lives--; addParticles(player.x,player.y,0); if(lives<=0){ endGame(); } }
  });
  // Particles
  particles = particles.filter(p=>{ p.x+=p.vx; p.y+=p.vy; p.life--; return p.life>0; });
  // Next level?
  if(enemies.every(e=>!e.alive)){ level++; spawnEnemies(); }
  // UI
  document.getElementById('sc').textContent=score;
  document.getElementById('hp').textContent='❤️'.repeat(lives);
  document.getElementById('lv').textContent=level;
}

function addParticles(x,y,type){
  const colors=['#58a6ff','#ffd700','#ff6b6b'];
  for(let i=0;i<8;i++) particles.push({x,y,vx:(Math.random()-0.5)*4,vy:(Math.random()-0.5)*4,life:30,color:colors[type%3]});
}

function draw(){
  ctx.fillStyle='#000010'; ctx.fillRect(0,0,W,H);
  // Stars
  stars.forEach(s=>{ s.y+=s.sp; if(s.y>H) s.y=0; ctx.fillStyle=\`rgba(255,255,255,\${s.s/3})\`; ctx.fillRect(s.x,s.y,s.s,s.s); });
  // Player ship
  ctx.fillStyle='#58a6ff';
  ctx.beginPath(); ctx.moveTo(player.x,player.y-player.h); ctx.lineTo(player.x-player.w/2,player.y+player.h/2); ctx.lineTo(player.x,player.y+5); ctx.lineTo(player.x+player.w/2,player.y+player.h/2); ctx.closePath(); ctx.fill();
  ctx.fillStyle='#fff'; ctx.beginPath(); ctx.arc(player.x,player.y-5,5,0,Math.PI*2); ctx.fill();
  // Enemies
  const emojiMap=['👾','🛸','👽'];
  enemies.forEach(e=>{ if(!e.alive) return; ctx.font=\`\${e.w}px serif\`; ctx.fillText(emojiMap[e.type]||'👾',e.x,e.y+e.h); });
  // Bullets
  ctx.fillStyle='#ffd700';
  bullets.forEach(b=>{ ctx.fillStyle='#ffd700'; ctx.shadowColor='#ffd700'; ctx.shadowBlur=8; ctx.fillRect(b.x-b.w/2,b.y,b.w,b.h); });
  ctx.fillStyle='#ff6b6b';
  eBullets.forEach(b=>{ ctx.fillStyle='#ff6b6b'; ctx.shadowColor='#ff6b6b'; ctx.shadowBlur=6; ctx.beginPath(); ctx.arc(b.x,b.y,b.w/2,0,Math.PI*2); ctx.fill(); });
  ctx.shadowBlur=0;
  // Particles
  particles.forEach(p=>{ ctx.fillStyle=p.color; ctx.globalAlpha=p.life/30; ctx.fillRect(p.x-2,p.y-2,4,4); });
  ctx.globalAlpha=1;
}

function endGame(){
  running=false;
  const ov=document.getElementById('overlay');
  ov.style.display='flex';
  ov.innerHTML=\`<h2>💀 游戏结束</h2><p style="color:#ffd700;font-size:1.5rem;margin:10px">得分: \${score}</p><p>关卡: \${level}</p><button class="play-btn" style="margin-top:20px;padding:12px 40px;background:#58a6ff;color:#000;border:none;border-radius:25px;font-size:1rem;font-weight:bold;cursor:pointer" onclick="startGame()">再来一次</button>\`;
}

document.addEventListener('keydown',e=>{ keys[e.key]=true; if([' '].includes(e.key)) e.preventDefault(); });
document.addEventListener('keyup',e=>{ keys[e.key]=false; });

let touchShoot = false, touchStartX = 0;
C.addEventListener('touchstart',e=>{ e.preventDefault(); touchShoot=true; touchStartX=e.touches[0].clientX; },{passive:false});
C.addEventListener('touchmove',e=>{ e.preventDefault(); const dx=e.touches[0].clientX-C.getBoundingClientRect().left; player.x=Math.max(15,Math.min(W-15,dx)); },{passive:false});
C.addEventListener('touchend',()=>{ touchShoot=false; });
</script>
</body>
</html>`,
  },
];

// ── Seed Logic ──────────────────────────────────────────────────────────────

async function seed() {
  console.log('🌱 开始写入测试数据...\n');

  // 1. 确保有测试用户
  let testUser = await prisma.user.findFirst({ where: { username: 'demo_creator' } });
  if (!testUser) {
    const hash = createHash('sha256').update('Demo@123456').digest('hex');
    testUser = await prisma.user.create({
      data: {
        id: randomUUID(),
        username: 'demo_creator',
        displayName: '游戏创作者',
        email: 'demo@playforge.dev',
        passwordHash: hash,
        bio: '热爱游戏创作的开发者，专注制作有趣的 HTML5 小游戏。',
        role: 'creator',
        gameCount: 6,
        totalPlays: 30861,
        followerCount: 1234,
        followingCount: 89,
      },
    });
    console.log(`✅ 创建测试用户: ${testUser.username} (${testUser.id})`);
  } else {
    console.log(`ℹ️  使用已有用户: ${testUser.username} (${testUser.id})`);
  }

  // 2. 连接 MongoDB
  const mongo = new MongoClient(MONGO_URL);
  await mongo.connect();
  const db = mongo.db('playforge');
  const bundles = db.collection('game_bundles');
  console.log('✅ MongoDB 连接成功\n');

  const insertedGames = [];

  for (const g of GAMES) {
    const gameId = randomUUID();
    const previewUrl = `${APP_URL}/games/${gameId}/preview`;

    // PostgreSQL game record
    const game = await prisma.game.create({
      data: {
        id: gameId,
        authorId: testUser.id,
        title: g.title,
        description: g.description,
        status: 'published',
        gameType: g.gameType,
        tags: g.tags,
        version: 1,
        playCount: BigInt(g.playCount),
        likeCount: BigInt(g.likeCount),
        forkCount: BigInt(Math.floor(g.playCount * 0.03)),
        qualityScore: g.qualityScore,
        publishedAt: new Date(Date.now() - Math.random() * 7 * 24 * 3600 * 1000),
      },
    });

    // MongoDB bundle
    await bundles.insertOne({
      game_id: gameId,
      version: 1,
      html_code: g.html,
      css_code: '',
      js_code: '',
      generation_meta: { strategy: 'template', qa_passed: true, qa_retries: 0 },
      code_size_bytes: Buffer.byteLength(g.html, 'utf8'),
      preview_url: previewUrl,
      created_at: new Date(),
      updated_at: new Date(),
    });

    console.log(`✅ [${g.gameType}] ${g.title} → ${gameId}`);
    console.log(`   ▸ 游玩 ${g.playCount.toLocaleString()}  点赞 ${g.likeCount}  质量分 ${g.qualityScore}`);
    console.log(`   ▸ 预览: ${previewUrl}\n`);
    insertedGames.push({ title: g.title, id: gameId, previewUrl });
  }

  // 3. 更新用户计数
  await prisma.user.update({
    where: { id: testUser.id },
    data: { gameCount: 6 },
  });

  await mongo.close();
  await prisma.$disconnect();

  console.log('═'.repeat(60));
  console.log('🎉 6 个游戏写入完成！\n');
  console.log('快速测试：');
  insertedGames.forEach(g => console.log(`  GET ${g.previewUrl}`));
  console.log('\n探索接口：');
  console.log('  GET http://localhost:3002/api/v1/games/explore/published');
  console.log('  GET http://localhost:3004/api/v1/feed/trending');
}

seed().catch(err => { console.error('❌ 种子脚本失败:', err.message); process.exit(1); });
