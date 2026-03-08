import { PrismaClient } from '@prisma/client';
import bcrypt from 'bcryptjs';
import { randomUUID } from 'crypto';

const prisma = new PrismaClient({
  datasources: { db: { url: process.env.DATABASE_URL } },
});

// ============================================================
// 6 完整 HTML5 小游戏代码
// ============================================================

const snakeGameHtml = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>贪吃蛇</title>
<style>
  body { margin:0; background:#1a1a2e; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:Arial,sans-serif; color:#eee; }
  h1 { color:#00d4ff; text-shadow:0 0 10px #00d4ff; margin-bottom:10px; font-size:2em; }
  #score { font-size:1.2em; margin-bottom:10px; color:#ffd700; }
  canvas { border:3px solid #00d4ff; box-shadow:0 0 20px #00d4ff55; background:#0d0d1a; }
  #msg { margin-top:12px; font-size:1em; color:#aaa; }
</style>
</head>
<body>
<h1>🐍 贪吃蛇</h1>
<div id="score">得分: <span id="s">0</span>　最高: <span id="hi">0</span></div>
<canvas id="c" width="400" height="400"></canvas>
<div id="msg">WASD 或方向键控制 · 空格暂停</div>
<script>
const c=document.getElementById('c'),ctx=c.getContext('2d'),sz=20,cols=20,rows=20;
let snake=[{x:10,y:10}],dir={x:1,y:0},food={},score=0,hi=0,running=false,paused=false,loop;
function rnd(){return Math.floor(Math.random()*20);}
function placeFood(){do{food={x:rnd(),y:rnd()};}while(snake.some(s=>s.x===food.x&&s.y===food.y));}
function draw(){
  ctx.clearRect(0,0,400,400);
  ctx.fillStyle='#ff4757';ctx.shadowColor='#ff4757';ctx.shadowBlur=15;
  ctx.fillRect(food.x*sz+2,food.y*sz+2,sz-4,sz-4);ctx.shadowBlur=0;
  snake.forEach((s,i)=>{
    const g=ctx.createLinearGradient(s.x*sz,s.y*sz,(s.x+1)*sz,(s.y+1)*sz);
    g.addColorStop(0,i===0?'#00d4ff':'#00ff88');g.addColorStop(1,i===0?'#0088cc':'#008844');
    ctx.fillStyle=g;ctx.shadowColor=i===0?'#00d4ff':'#00ff88';ctx.shadowBlur=i===0?12:4;
    ctx.beginPath();ctx.roundRect(s.x*sz+1,s.y*sz+1,sz-2,sz-2,4);ctx.fill();
  });ctx.shadowBlur=0;
  if(!running){
    ctx.fillStyle='rgba(0,0,0,0.6)';ctx.fillRect(0,0,400,400);
    ctx.fillStyle='#00d4ff';ctx.font='bold 28px Arial';ctx.textAlign='center';
    ctx.fillText(score?'游戏结束! 按空格重新开始':'按空格开始游戏',200,200);
    if(score){ctx.font='18px Arial';ctx.fillStyle='#ffd700';ctx.fillText('得分: '+score,200,240);}
  }
  if(paused&&running){
    ctx.fillStyle='rgba(0,0,0,0.5)';ctx.fillRect(0,0,400,400);
    ctx.fillStyle='#ffd700';ctx.font='bold 32px Arial';ctx.textAlign='center';ctx.fillText('⏸ 暂停',200,200);
  }
}
function step(){
  if(paused)return;
  const h={x:snake[0].x+dir.x,y:snake[0].y+dir.y};
  if(h.x<0||h.x>=cols||h.y<0||h.y>=rows||snake.some(s=>s.x===h.x&&s.y===h.y)){
    clearInterval(loop);running=false;if(score>hi){hi=document.getElementById('hi').textContent=score;}draw();return;
  }
  snake.unshift(h);
  if(h.x===food.x&&h.y===food.y){score++;document.getElementById('s').textContent=score;placeFood();}
  else snake.pop();
  draw();
}
function start(){snake=[{x:10,y:10}];dir={x:1,y:0};score=0;document.getElementById('s').textContent=0;placeFood();running=true;paused=false;clearInterval(loop);loop=setInterval(step,150);}
document.addEventListener('keydown',e=>{
  const k={ArrowUp:{x:0,y:-1},ArrowDown:{x:0,y:1},ArrowLeft:{x:-1,y:0},ArrowRight:{x:1,y:0},w:{x:0,y:-1},s:{x:0,y:1},a:{x:-1,y:0},d:{x:1,y:0}};
  if(e.code==='Space'){e.preventDefault();if(!running)start();else paused=!paused;}
  if(k[e.key]&&!(k[e.key].x===-dir.x&&k[e.key].y===-dir.y)){dir=k[e.key];e.preventDefault();}
});
placeFood();draw();
</script>
</body>
</html>`;

const breakoutGameHtml = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>打砖块</title>
<style>
  body{margin:0;background:linear-gradient(135deg,#1a1a2e,#16213e,#0f3460);display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;font-family:Arial,sans-serif;color:#eee;}
  h1{color:#e94560;text-shadow:0 0 15px #e94560;margin-bottom:8px;font-size:2em;}
  #info{font-size:1.1em;margin-bottom:8px;color:#ffd700;}
  canvas{border:3px solid #e94560;box-shadow:0 0 20px #e9456055;}
</style>
</head>
<body>
<h1>🧱 打砖块</h1>
<div id="info">分数:<span id="sc">0</span>　生命:<span id="lv">3</span>　关卡:<span id="lev">1</span></div>
<canvas id="c" width="480" height="500"></canvas>
<script>
const c=document.getElementById('c'),ctx=c.getContext('2d');
const colors=['#e94560','#f5a623','#7ed321','#00d4ff','#b8e986','#ff6b6b','#4ecdc4'];
let bricks=[],ball={},paddle={},score=0,lives=3,level=1,started=false,lost=false;
function initBricks(){
  bricks=[];const rows=4+level,cols=10;
  for(let r=0;r<rows;r++)for(let cc=0;cc<cols;cc++)
    bricks.push({x:4+cc*47,y:40+r*28,w:43,h:22,color:colors[(r+cc)%colors.length],hp:Math.ceil((r+1)/2)});
}
function initBall(){ball={x:240,y:400,vx:(Math.random()>.5?1:-1)*3,vy:-4.5,r:8};}
function initPaddle(){paddle={x:190,y:460,w:100,h=14};}
function draw(){
  ctx.clearRect(0,0,480,500);
  bricks.forEach(b=>{if(b.hp<=0)return;
    const g=ctx.createLinearGradient(b.x,b.y,b.x+b.w,b.y+b.h);
    g.addColorStop(0,b.color);g.addColorStop(1,'#000');
    ctx.fillStyle=g;ctx.shadowColor=b.color;ctx.shadowBlur=8;
    ctx.beginPath();ctx.roundRect(b.x,b.y,b.w,b.h,4);ctx.fill();
    ctx.shadowBlur=0;ctx.fillStyle='rgba(255,255,255,0.5)';ctx.font='bold 11px Arial';ctx.textAlign='center';
    if(b.hp>1)ctx.fillText('★'.repeat(b.hp),b.x+b.w/2,b.y+b.h/2+4);
  });
  const pg=ctx.createLinearGradient(paddle.x,paddle.y,paddle.x+paddle.w,paddle.y);
  pg.addColorStop(0,'#e94560');pg.addColorStop(0.5,'#ff6b6b');pg.addColorStop(1,'#e94560');
  ctx.fillStyle=pg;ctx.shadowColor='#e94560';ctx.shadowBlur=15;
  ctx.beginPath();ctx.roundRect(paddle.x,paddle.y,paddle.w,paddle.h,7);ctx.fill();ctx.shadowBlur=0;
  const bg=ctx.createRadialGradient(ball.x,ball.y,0,ball.x,ball.y,ball.r);
  bg.addColorStop(0,'#fff');bg.addColorStop(1,'#00d4ff');
  ctx.fillStyle=bg;ctx.shadowColor='#00d4ff';ctx.shadowBlur=15;
  ctx.beginPath();ctx.arc(ball.x,ball.y,ball.r,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0;
  if(!started){
    ctx.fillStyle='rgba(0,0,0,0.6)';ctx.fillRect(0,0,480,500);
    ctx.fillStyle='#00d4ff';ctx.font='bold 26px Arial';ctx.textAlign='center';
    ctx.fillText(lost?'游戏结束！按空格重试':'鼠标移动挡板 · 空格开始',240,250);
    if(lost){ctx.font='20px Arial';ctx.fillStyle='#ffd700';ctx.fillText('最终分数: '+score,240,290);}
  }
}
function update(){
  if(!started)return;
  ball.x+=ball.vx;ball.y+=ball.vy;
  if(ball.x<=ball.r||ball.x>=480-ball.r)ball.vx*=-1;
  if(ball.y<=ball.r)ball.vy*=-1;
  if(ball.y>=500+ball.r){lives--;document.getElementById('lv').textContent=lives;if(lives<=0){started=false;lost=true;}else initBall();}
  if(ball.y+ball.r>=paddle.y&&ball.y-ball.r<=paddle.y+paddle.h&&ball.x>=paddle.x&&ball.x<=paddle.x+paddle.w){
    ball.vy=-Math.abs(ball.vy);ball.vx=((ball.x-(paddle.x+paddle.w/2))/(paddle.w/2))*5;
  }
  bricks.forEach(b=>{if(b.hp<=0)return;
    if(ball.x+ball.r>b.x&&ball.x-ball.r<b.x+b.w&&ball.y+ball.r>b.y&&ball.y-ball.r<b.y+b.h){
      b.hp--;if(b.hp<=0){score+=10*level;document.getElementById('sc').textContent=score;}
      ball.vy*=-1;
    }
  });
  if(bricks.every(b=>b.hp<=0)){level++;document.getElementById('lev').textContent=level;initBricks();initBall();}
}
c.addEventListener('mousemove',e=>{const r=c.getBoundingClientRect();paddle.x=Math.max(0,Math.min(480-paddle.w,e.clientX-r.left-paddle.w/2));});
document.addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();if(!started){lost=false;lives=3;score=0;level=1;document.getElementById('sc').textContent=0;document.getElementById('lv').textContent=3;document.getElementById('lev').textContent=1;initBricks();initBall();initPaddle();started=true;}}});
initBricks();initBall();initPaddle();
setInterval(()=>{update();draw();},16);
</script>
</body>
</html>`;

const whackMoleHtml = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>打地鼠</title>
<style>
  *{box-sizing:border-box;}
  body{margin:0;background:linear-gradient(180deg,#87CEEB 0%,#87CEEB 60%,#228B22 60%,#228B22 100%);display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;font-family:'Comic Sans MS',cursive;user-select:none;}
  h1{color:#fff;text-shadow:2px 2px 4px #333;font-size:2.2em;margin:0 0 5px;}
  #info{display:flex;gap:30px;font-size:1.3em;font-weight:bold;color:#fff;text-shadow:1px 1px 3px #333;margin-bottom:10px;}
  .info-box{background:rgba(0,0,0,0.3);padding:6px 18px;border-radius:20px;}
  #grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;padding:20px;background:rgba(0,0,0,0.15);border-radius:20px;}
  .hole{width:120px;height:100px;background:radial-gradient(ellipse,#5C3317 0%,#3B1F0A 70%);border-radius:50%;display:flex;align-items:flex-end;justify-content:center;overflow:hidden;cursor:pointer;position:relative;box-shadow:inset 0 -8px 20px rgba(0,0,0,0.5);}
  .mole{font-size:56px;transition:transform 0.15s;transform:translateY(100%);line-height:1;}
  .mole.up{transform:translateY(5%);}
  .mole.hit{transform:translateY(5%) scale(1.3);filter:brightness(2);}
  #btn{margin-top:15px;padding:12px 40px;font-size:1.2em;background:#ff6b6b;color:#fff;border:none;border-radius:30px;cursor:pointer;box-shadow:0 4px 15px rgba(255,107,107,0.5);font-weight:bold;transition:transform .1s;}
  #btn:hover{transform:scale(1.05);}
  #timer-bar{width:300px;height:12px;background:#ccc;border-radius:6px;margin-top:8px;overflow:hidden;}
  #timer-fill{height:100%;background:linear-gradient(90deg,#00d4ff,#ff6b6b);transition:width 0.1s;width:100%;}
</style>
</head>
<body>
<h1>🔨 打地鼠！</h1>
<div id="info">
  <div class="info-box">⭐ <span id="sc">0</span></div>
  <div class="info-box">❤️ <span id="lv">3</span></div>
  <div class="info-box">⏱ <span id="ti">30</span>s</div>
</div>
<div id="timer-bar"><div id="timer-fill"></div></div>
<div id="grid"></div>
<button id="btn" onclick="startGame()">开始游戏</button>
<script>
const emojis=['🐹','🐭','🦔','🐿️'];
const badEmoji='💣';
const grid=document.getElementById('grid');
let score=0,lives=3,timeLeft=30,running=false,moles=[],timers=[],gameTimer;
for(let i=0;i<9;i++){
  const hole=document.createElement('div');hole.className='hole';
  const mole=document.createElement('div');mole.className='mole';mole.textContent=emojis[0];
  hole.appendChild(mole);
  hole.addEventListener('click',()=>whack(i,hole,mole));
  grid.appendChild(hole);moles.push({hole,mole,active:false,isBad:false});
}
function startGame(){
  score=0;lives=3;timeLeft=30;running=true;
  document.getElementById('sc').textContent=0;document.getElementById('lv').textContent=3;document.getElementById('ti').textContent=30;
  document.getElementById('btn').style.display='none';
  document.getElementById('timer-fill').style.width='100%';
  timers.forEach(clearTimeout);timers=[];
  moles.forEach(m=>{m.active=false;m.mole.classList.remove('up','hit');});
  gameTimer=setInterval(()=>{
    timeLeft--;document.getElementById('ti').textContent=timeLeft;
    document.getElementById('timer-fill').style.width=(timeLeft/30*100)+'%';
    if(timeLeft<=0){endGame();}
  },1000);
  popMoles();
}
function popMoles(){
  if(!running)return;
  const available=moles.filter(m=>!m.active);
  if(available.length>0){
    const count=Math.min(Math.floor(Math.random()*3)+1,available.length);
    for(let i=0;i<count;i++){
      const m=available.splice(Math.floor(Math.random()*available.length),1)[0];
      m.isBad=Math.random()<0.2;m.mole.textContent=m.isBad?badEmoji:emojis[Math.floor(Math.random()*emojis.length)];
      m.active=true;m.mole.classList.add('up');
      const t=setTimeout(()=>{
        if(m.active){m.active=false;m.mole.classList.remove('up','hit');if(!m.isBad){lives--;document.getElementById('lv').textContent=lives;if(lives<=0)endGame();}}
      },Math.random()*800+700);timers.push(t);
    }
  }
  timers.push(setTimeout(popMoles,Math.random()*400+300));
}
function whack(i,hole,mole){
  const m=moles[i];if(!m.active||!running)return;
  m.active=false;
  if(m.isBad){lives--;document.getElementById('lv').textContent=lives;mole.textContent='💥';if(lives<=0){mole.classList.remove('up');endGame();return;}}
  else{score+=10;document.getElementById('sc').textContent=score;mole.textContent='✨';}
  mole.classList.add('hit');
  setTimeout(()=>{mole.classList.remove('up','hit');mole.textContent=emojis[0];},300);
}
function endGame(){
  running=false;clearInterval(gameTimer);timers.forEach(clearTimeout);
  moles.forEach(m=>{m.active=false;m.mole.classList.remove('up','hit');});
  const btn=document.getElementById('btn');btn.textContent='再玩一次 🎉';btn.style.display='block';
  alert('游戏结束！你的得分: '+score);
}
</script>
</body>
</html>`;

const puzzle2048Html = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>2048</title>
<style>
  body{margin:0;background:#1a1a2e;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;font-family:'Arial Rounded MT Bold',Arial,sans-serif;color:#eee;touch-action:none;}
  h1{color:#ffd700;text-shadow:0 0 10px #ffd700;margin:0 0 10px;font-size:2.5em;}
  #scoreboard{display:flex;gap:20px;margin-bottom:12px;}
  .sb{background:#333;padding:8px 20px;border-radius:10px;text-align:center;}
  .sb-label{font-size:.75em;color:#aaa;text-transform:uppercase;}
  .sb-val{font-size:1.4em;font-weight:bold;color:#ffd700;}
  #grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;background:#2d2d44;padding:12px;border-radius:14px;width:340px;}
  .cell{width:76px;height:76px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:1.6em;font-weight:bold;transition:all .12s;background:#3a3a55;color:#888;}
  .v2{background:#ede0c8;color:#776e65;}.v4{background:#ede0c8;color:#776e65;}
  .v8{background:#f2b179;color:#fff;}.v16{background:#f59563;color:#fff;}
  .v32{background:#f67c5f;color:#fff;}.v64{background:#f65e3b;color:#fff;}
  .v128{background:#edcf72;color:#fff;font-size:1.3em;}.v256{background:#edcc61;color:#fff;font-size:1.3em;}
  .v512{background:#edc850;color:#fff;font-size:1.3em;}.v1024{background:#edc53f;color:#fff;font-size:1.1em;}
  .v2048{background:#edc22e;color:#fff;font-size:1.1em;box-shadow:0 0 30px #ffd700;}
  #hint{margin-top:12px;color:#888;font-size:.9em;}
  #newgame{margin-top:10px;padding:10px 28px;background:#f65e3b;color:#fff;border:none;border-radius:8px;font-size:1em;cursor:pointer;font-weight:bold;transition:transform .1s;}
  #newgame:hover{transform:scale(1.05);}
</style>
</head>
<body>
<h1>2048</h1>
<div id="scoreboard">
  <div class="sb"><div class="sb-label">分数</div><div class="sb-val" id="sc">0</div></div>
  <div class="sb"><div class="sb-label">最高</div><div class="sb-val" id="hi">0</div></div>
</div>
<div id="grid"></div>
<div id="hint">↑↓←→ 或 WASD 移动 · 滑动触屏</div>
<button id="newgame" onclick="init()">新游戏</button>
<script>
let board,score,hi=0;
const grid=document.getElementById('grid');
function init(){
  board=Array.from({length:4},()=>Array(4).fill(0));score=0;
  document.getElementById('sc').textContent=0;addTile();addTile();render();
}
function addTile(){
  const empty=[];
  board.forEach((r,ri)=>r.forEach((v,ci)=>{if(!v)empty.push([ri,ci]);}));
  if(!empty.length)return;
  const [r,c]=empty[Math.floor(Math.random()*empty.length)];
  board[r][c]=Math.random()<.9?2:4;
}
function render(){
  grid.innerHTML='';
  board.forEach(row=>row.forEach(v=>{
    const d=document.createElement('div');d.className='cell'+(v?' v'+v:'');
    d.textContent=v||'';grid.appendChild(d);
  }));
}
function move(dir){
  const prev=JSON.stringify(board);
  const rotate=(b)=>b[0].map((_,i)=>b.map(r=>r[i]).reverse());
  const slideLeft=(b)=>b.map(row=>{
    let r=row.filter(x=>x);
    for(let i=0;i<r.length-1;i++)if(r[i]===r[i+1]){r[i]*=2;score+=r[i];r.splice(i+1,1);}
    while(r.length<4)r.push(0);return r;
  });
  let b=board.map(r=>[...r]);
  if(dir===1)b=b.map(r=>[...r].reverse()).map(r=>slideLeft([r])[0]).map(r=>[...r].reverse());
  else if(dir===0)b=slideLeft(b);
  else if(dir===2){b=rotate(b);b=slideLeft(b);b=rotate(rotate(rotate(b)));}
  else{b=rotate(rotate(rotate(b)));b=slideLeft(b);b=rotate(b);}
  if(JSON.stringify(b)!==prev){board=b;addTile();}
  if(score>hi){hi=score;document.getElementById('hi').textContent=hi;}
  document.getElementById('sc').textContent=score;render();
  if(board.some(r=>r.includes(2048))){setTimeout(()=>alert('🎉 恭喜！达到2048！'),100);}
}
document.addEventListener('keydown',e=>{
  const m={ArrowLeft:0,ArrowRight:1,ArrowUp:2,ArrowDown:3,a:0,d:1,w:2,s:3};
  if(m[e.key]!==undefined){e.preventDefault();move(m[e.key]);}
});
let tx,ty;
document.getElementById('grid').addEventListener('touchstart',e=>{tx=e.touches[0].clientX;ty=e.touches[0].clientY;});
document.getElementById('grid').addEventListener('touchend',e=>{
  const dx=e.changedTouches[0].clientX-tx,dy=e.changedTouches[0].clientY-ty;
  if(Math.abs(dx)>Math.abs(dy)){move(dx<0?0:1);}else{move(dy<0?2:3);}
});
init();
</script>
</body>
</html>`;

const flappyBirdHtml = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>飞翔小鸟</title>
<style>
  body{margin:0;overflow:hidden;font-family:Arial,sans-serif;}
  canvas{display:block;}
</style>
</head>
<body>
<canvas id="c"></canvas>
<script>
const c=document.getElementById('c'),ctx=c.getContext('2d');
c.width=window.innerWidth>480?480:window.innerWidth;c.height=640;
const W=c.width,H=c.height;
let bird,pipes,score,hi=0,state='idle',frame=0;
const GRAVITY=0.35,FLAP=-7,PIPE_GAP=160,PIPE_W=60,PIPE_SPEED=2.5;
const SKY=['#87CEEB','#98D8F5'];const GROUND_H=80;
function initGame(){
  bird={x:W*0.25,y:H/2,vy:0,r:20,frame:0};
  pipes=[];score=0;frame=0;
}
function addPipe(){
  const min=80,max=H-GROUND_H-PIPE_GAP-80;
  const top=Math.floor(Math.random()*(max-min)+min);
  pipes.push({x:W,top,bot:top+PIPE_GAP});
}
function drawBird(){
  const b=bird;
  // Body
  ctx.save();ctx.translate(b.x,b.y);ctx.rotate(Math.max(-0.5,Math.min(1,b.vy*0.06)));
  ctx.fillStyle='#FFD700';ctx.shadowColor='#FF8C00';ctx.shadowBlur=8;
  ctx.beginPath();ctx.ellipse(0,0,b.r,b.r*0.8,0,0,Math.PI*2);ctx.fill();
  // Wing
  ctx.fillStyle='#FFA500';ctx.beginPath();
  const wf=Math.sin(frame*0.3)*10;ctx.ellipse(-5,5+wf,12,6,0.5,0,Math.PI*2);ctx.fill();
  // Eye
  ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(8,-4,6,0,Math.PI*2);ctx.fill();
  ctx.fillStyle='#333';ctx.beginPath();ctx.arc(10,-4,3,0,Math.PI*2);ctx.fill();
  // Beak
  ctx.fillStyle='#FF6347';ctx.beginPath();ctx.moveTo(18,-2);ctx.lineTo(26,0);ctx.lineTo(18,4);ctx.fill();
  ctx.shadowBlur=0;ctx.restore();
}
function drawPipes(){
  pipes.forEach(p=>{
    const g=ctx.createLinearGradient(p.x,0,p.x+PIPE_W,0);
    g.addColorStop(0,'#5cb85c');g.addColorStop(0.4,'#7ed957');g.addColorStop(1,'#3a7a3a');
    ctx.fillStyle=g;ctx.shadowColor='#2d6a2d';ctx.shadowBlur=6;
    // Top pipe
    ctx.fillRect(p.x,0,PIPE_W,p.top);
    ctx.fillStyle='#4cae4c';ctx.fillRect(p.x-5,p.top-20,PIPE_W+10,20);
    // Bottom pipe
    ctx.fillStyle=g;ctx.fillRect(p.x,p.bot,PIPE_W,H-GROUND_H-p.bot);
    ctx.fillStyle='#4cae4c';ctx.fillRect(p.x-5,p.bot,PIPE_W+10,20);
    ctx.shadowBlur=0;
  });
}
function drawBg(){
  const sky=ctx.createLinearGradient(0,0,0,H-GROUND_H);
  sky.addColorStop(0,'#1a6db5');sky.addColorStop(1,'#87CEEB');
  ctx.fillStyle=sky;ctx.fillRect(0,0,W,H-GROUND_H);
  // Clouds
  [0.15,0.45,0.75].forEach((t,i)=>{
    const cx=((frame*0.5+i*W/3)%W+W)%W,cy=60+i*40;
    ctx.fillStyle='rgba(255,255,255,0.85)';ctx.shadowBlur=0;
    ctx.beginPath();ctx.arc(cx,cy,28,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(cx+25,cy-5,20,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(cx-20,cy+5,18,0,Math.PI*2);ctx.fill();
  });
  // Ground
  ctx.fillStyle='#8B6914';ctx.fillRect(0,H-GROUND_H,W,GROUND_H);
  ctx.fillStyle='#5D8A2E';ctx.fillRect(0,H-GROUND_H,W,18);
}
function loop(){
  frame++;drawBg();drawPipes();drawBird();
  // HUD
  ctx.fillStyle='rgba(0,0,0,0.5)';ctx.fillRect(0,0,W,44);
  ctx.fillStyle='#fff';ctx.font='bold 22px Arial';ctx.textAlign='center';
  if(state==='playing')ctx.fillText('⭐ '+score,W/2,30);
  else if(state==='dead'){
    ctx.fillStyle='rgba(0,0,0,0.55)';ctx.fillRect(0,0,W,H);
    ctx.fillStyle='#ff4757';ctx.font='bold 36px Arial';ctx.fillText('游戏结束!',W/2,H/2-60);
    ctx.fillStyle='#ffd700';ctx.font='bold 28px Arial';ctx.fillText('得分: '+score,W/2,H/2-10);
    ctx.fillStyle='#ccc';ctx.font='22px Arial';ctx.fillText('最高: '+hi,W/2,H/2+30);
    ctx.fillStyle='#00d4ff';ctx.font='20px Arial';ctx.fillText('点击或空格重新开始',W/2,H/2+80);
  } else {
    ctx.fillStyle='#fff';ctx.font='bold 28px Arial';ctx.fillText('🐦 飞翔小鸟',W/2,30);
    ctx.fillStyle='rgba(0,0,0,0.5)';ctx.fillRect(W/2-140,H/2-60,280,100);
    ctx.fillStyle='#ffd700';ctx.font='22px Arial';ctx.fillText('点击屏幕或按空格开始',W/2,H/2-15);
    ctx.fillStyle='#ccc';ctx.font='16px Arial';ctx.fillText('最高分: '+hi,W/2,H/2+30);
  }
  if(state==='playing'){
    bird.vy+=GRAVITY;bird.y+=bird.vy;
    if(frame%90===0)addPipe();
    pipes.forEach(p=>p.x-=PIPE_SPEED);
    pipes=pipes.filter(p=>p.x+PIPE_W>0);
    pipes.forEach(p=>{
      if(bird.x-bird.r<p.x+PIPE_W&&bird.x+bird.r>p.x&&(bird.y-bird.r<p.top||bird.y+bird.r>p.bot)){
        state='dead';if(score>hi)hi=score;
      }
      if(p.x+PIPE_W===Math.floor(bird.x)){score++;}
    });
    if(bird.y+bird.r>=H-GROUND_H||bird.y-bird.r<=0){state='dead';if(score>hi)hi=score;}
  }
  requestAnimationFrame(loop);
}
function flap(){
  if(state==='idle'||state==='dead'){initGame();state='playing';}
  else if(state==='playing'){bird.vy=FLAP;}
}
c.addEventListener('click',flap);
document.addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();flap();}});
c.addEventListener('touchstart',e=>{e.preventDefault();flap();});
initGame();loop();
</script>
</body>
</html>`;

const memoryCardHtml = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>记忆翻牌</title>
<style>
  body{margin:0;background:linear-gradient(135deg,#667eea,#764ba2);display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;font-family:Arial,sans-serif;color:#fff;}
  h1{font-size:2em;margin:0 0 5px;text-shadow:0 2px 8px rgba(0,0,0,0.3);}
  #info{display:flex;gap:25px;margin-bottom:15px;font-size:1.1em;}
  .ib{background:rgba(255,255,255,0.2);padding:6px 20px;border-radius:20px;backdrop-filter:blur(5px);}
  #grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:20px;background:rgba(255,255,255,0.1);border-radius:20px;backdrop-filter:blur(5px);}
  .card{width:90px;height:90px;cursor:pointer;perspective:600px;}
  .card-inner{width:100%;height:100%;transform-style:preserve-3d;transition:transform .4s;position:relative;}
  .card.flip .card-inner{transform:rotateY(180deg);}
  .card-front,.card-back{position:absolute;width:100%;height:100%;backface-visibility:hidden;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:2.4em;box-shadow:0 4px 15px rgba(0,0,0,0.2);}
  .card-front{background:linear-gradient(135deg,#a18cd1,#fbc2eb);}
  .card-back{background:linear-gradient(135deg,#fff9c4,#fff);transform:rotateY(180deg);}
  .card.matched .card-inner{animation:pulse .4s;}
  @keyframes pulse{0%{transform:rotateY(180deg) scale(1)}50%{transform:rotateY(180deg) scale(1.15)}100%{transform:rotateY(180deg) scale(1)}}
  #result{margin-top:15px;font-size:1.2em;min-height:28px;text-shadow:0 1px 4px rgba(0,0,0,0.3);}
  button{margin-top:10px;padding:10px 30px;font-size:1em;background:#ff6b6b;border:none;border-radius:20px;color:#fff;cursor:pointer;font-weight:bold;box-shadow:0 4px 15px rgba(255,107,107,0.4);transition:transform .1s;}
  button:hover{transform:scale(1.05);}
</style>
</head>
<body>
<h1>🃏 记忆翻牌</h1>
<div id="info">
  <div class="ib">翻牌: <span id="mv">0</span></div>
  <div class="ib">配对: <span id="mt">0</span>/8</div>
  <div class="ib">时间: <span id="ti">0</span>s</div>
</div>
<div id="grid"></div>
<div id="result"></div>
<button onclick="startGame()">🔄 重新开始</button>
<script>
const emojis='🐶🐱🦊🐸🐻🦁🐯🐼'.split('');
let cards,flipped=[],matched=0,moves=0,lock=false,timer,seconds=0,started=false;
const grid=document.getElementById('grid');
function startGame(){
  clearInterval(timer);matched=0;moves=0;seconds=0;flipped=[];lock=false;started=false;
  document.getElementById('mv').textContent=0;document.getElementById('mt').textContent=0;document.getElementById('ti').textContent=0;document.getElementById('result').textContent='';
  const deck=[...emojis,...emojis].sort(()=>Math.random()-.5);
  grid.innerHTML='';cards=[];
  deck.forEach((e,i)=>{
    const card=document.createElement('div');card.className='card';
    card.innerHTML=\`<div class="card-inner"><div class="card-front">❓</div><div class="card-back">\${e}</div></div>\`;
    card.dataset.emoji=e;card.dataset.idx=i;
    card.addEventListener('click',()=>flip(card));
    grid.appendChild(card);cards.push(card);
  });
}
function flip(card){
  if(lock||card.classList.contains('flip')||card.classList.contains('matched'))return;
  if(!started){started=true;timer=setInterval(()=>{seconds++;document.getElementById('ti').textContent=seconds;},1000);}
  card.classList.add('flip');flipped.push(card);
  if(flipped.length===2){
    moves++;document.getElementById('mv').textContent=moves;lock=true;
    if(flipped[0].dataset.emoji===flipped[1].dataset.emoji){
      flipped.forEach(c=>c.classList.add('matched'));matched++;document.getElementById('mt').textContent=matched;flipped=[];lock=false;
      if(matched===8){clearInterval(timer);document.getElementById('result').textContent=\`🎉 完成！\${moves}步 · \${seconds}秒\`;}
    } else {
      setTimeout(()=>{flipped.forEach(c=>c.classList.remove('flip'));flipped=[];lock=false;},900);
    }
  }
}
startGame();
</script>
</body>
</html>`;

async function main() {
  console.log('🌱 开始填充数据库...\n');

  // ============================================================
  // Step 1: Create Users
  // ============================================================
  console.log('👤 创建测试用户...');
  const passwordHash = await bcrypt.hash('password123', 10);

  const [alice, bob, carol, david, emma] = await Promise.all([
    prisma.user.upsert({
      where: { username: 'alice' },
      update: {},
      create: {
        id: randomUUID(),
        username: 'alice',
        email: 'alice@gamevallies.com',
        displayName: 'Alice Chen',
        passwordHash,
        bio: '独立游戏开发者，热爱创造有趣的游戏体验',
        avatarUrl: 'https://api.dicebear.com/7.x/avataaars/svg?seed=alice',
        role: 'creator',
        followerCount: 1250,
        followingCount: 180,
        gameCount: 6,
        totalPlays: BigInt(82560),
        createdAt: new Date(Date.now() - 180 * 86400000),
      },
    }),
    prisma.user.upsert({
      where: { username: 'bobgamer' },
      update: {},
      create: {
        id: randomUUID(),
        username: 'bobgamer',
        email: 'bob@gamevallies.com',
        displayName: 'Bob Johnson',
        passwordHash,
        bio: '休闲玩家，最爱益智解谜游戏',
        avatarUrl: 'https://api.dicebear.com/7.x/avataaars/svg?seed=bob',
        role: 'user',
        followerCount: 42,
        followingCount: 120,
        gameCount: 0,
        createdAt: new Date(Date.now() - 120 * 86400000),
      },
    }),
    prisma.user.upsert({
      where: { username: 'caroldev' },
      update: {},
      create: {
        id: randomUUID(),
        username: 'caroldev',
        email: 'carol@gamevallies.com',
        displayName: 'Carol Adams',
        passwordHash,
        bio: 'AI游戏设计师，探索人机协作的无限可能',
        avatarUrl: 'https://api.dicebear.com/7.x/avataaars/svg?seed=carol',
        role: 'creator',
        followerCount: 680,
        followingCount: 250,
        gameCount: 3,
        totalPlays: BigInt(28900),
        createdAt: new Date(Date.now() - 90 * 86400000),
      },
    }),
    prisma.user.upsert({
      where: { username: 'davidy' },
      update: {},
      create: {
        id: randomUUID(),
        username: 'davidy',
        email: 'david@gamevallies.com',
        displayName: 'David Lee',
        passwordHash,
        bio: '动作游戏爱好者',
        avatarUrl: 'https://api.dicebear.com/7.x/avataaars/svg?seed=david',
        role: 'user',
        followerCount: 15,
        followingCount: 88,
        gameCount: 0,
        createdAt: new Date(Date.now() - 30 * 86400000),
      },
    }),
    prisma.user.upsert({
      where: { username: 'emmacraft' },
      update: {},
      create: {
        id: randomUUID(),
        username: 'emmacraft',
        email: 'emma@gamevallies.com',
        displayName: 'Emma Wilson',
        passwordHash,
        bio: '策略游戏达人，喜欢挑战脑力极限',
        avatarUrl: 'https://api.dicebear.com/7.x/avataaars/svg?seed=emma',
        role: 'creator',
        followerCount: 890,
        followingCount: 160,
        gameCount: 2,
        totalPlays: BigInt(19100),
        createdAt: new Date(Date.now() - 60 * 86400000),
      },
    }),
  ]);

  console.log(`  ✅ 创建了 5 个用户`);

  // ============================================================
  // Step 2: Create 6 Games with GameBundles
  // ============================================================
  console.log('\n🎮 创建 6 个精品小游戏...');

  const gameDefs = [
    {
      title: '🐍 贪吃蛇进化版',
      slug: 'snake-evolution',
      description: '经典贪吃蛇重制！霓虹发光效果，流畅操控，吃食物变长、加速，挑战最高分。支持键盘WASD/方向键，空格暂停。',
      gameType: 'arcade',
      tags: ['arcade', 'snake', 'classic', 'neon'],
      authorId: alice.id,
      playCount: BigInt(25680),
      likeCount: BigInt(1842),
      forkCount: BigInt(234),
      commentCount: 156,
      qualityScore: 9.2,
      avgPlayTime: 4.5,
      htmlCode: snakeGameHtml,
    },
    {
      title: '🧱 打砖块大师',
      slug: 'breakout-master',
      description: '打砖块游戏升级版！多关卡、血条砖块、霓虹视觉效果。鼠标控制挡板，空格开始。随关卡增加砖块行数，考验你的反应速度！',
      gameType: 'arcade',
      tags: ['arcade', 'breakout', 'retro', 'challenge'],
      authorId: alice.id,
      playCount: BigInt(18420),
      likeCount: BigInt(1356),
      forkCount: BigInt(167),
      commentCount: 98,
      qualityScore: 8.8,
      avgPlayTime: 6.2,
      htmlCode: breakoutGameHtml,
    },
    {
      title: '🔨 打地鼠！',
      slug: 'whack-a-mole',
      description: '萌趣打地鼠！9个地洞随机弹出小动物，快速点击得分，小心炸弹！限时30秒，比拼谁的反应最快。支持触屏和鼠标。',
      gameType: 'casual',
      tags: ['casual', 'whack-a-mole', 'funny', 'kids'],
      authorId: carol.id,
      playCount: BigInt(31200),
      likeCount: BigInt(2104),
      forkCount: BigInt(312),
      commentCount: 245,
      qualityScore: 9.5,
      avgPlayTime: 3.1,
      htmlCode: whackMoleHtml,
    },
    {
      title: '🔢 2048 极限挑战',
      slug: '2048-challenge',
      description: '经典2048数字游戏！合并相同数字方块，目标达到2048。支持键盘方向键/WASD和触屏滑动，挑战你的最高分！',
      gameType: 'puzzle',
      tags: ['puzzle', '2048', 'math', 'strategy'],
      authorId: emma.id,
      playCount: BigInt(22800),
      likeCount: BigInt(1567),
      forkCount: BigInt(198),
      commentCount: 134,
      qualityScore: 9.0,
      avgPlayTime: 8.4,
      htmlCode: puzzle2048Html,
    },
    {
      title: '🐦 飞翔小鸟',
      slug: 'flappy-bird-clone',
      description: '像素风飞翔小鸟！点击或按空格让小鸟跳跃，穿越绿色管道。手绘风格背景，流畅动画，挑战你能飞多远！',
      gameType: 'casual',
      tags: ['casual', 'flappy', 'endless', 'challenging'],
      authorId: carol.id,
      playCount: BigInt(44600),
      likeCount: BigInt(3201),
      forkCount: BigInt(521),
      commentCount: 387,
      qualityScore: 9.7,
      avgPlayTime: 2.8,
      htmlCode: flappyBirdHtml,
    },
    {
      title: '🃏 记忆翻牌王',
      slug: 'memory-card-game',
      description: '经典记忆翻牌游戏！16张卡牌中找到8对动物表情。计步数、计时，挑战最少步骤完成。支持触屏，适合全年龄！',
      gameType: 'puzzle',
      tags: ['puzzle', 'memory', 'cards', 'brain'],
      authorId: emma.id,
      playCount: BigInt(16900),
      likeCount: BigInt(1234),
      forkCount: BigInt(145),
      commentCount: 112,
      qualityScore: 8.6,
      avgPlayTime: 5.7,
      htmlCode: memoryCardHtml,
    },
  ];

  const games = [];
  for (const def of gameDefs) {
    const gameId = randomUUID();
    const bundleId = randomUUID();
    const publishedAt = new Date(Date.now() - Math.floor(Math.random() * 90 + 10) * 86400000);

    const game = await prisma.game.upsert({
      where: { slug: def.slug },
      update: {
        playCount: def.playCount,
        likeCount: def.likeCount,
        qualityScore: def.qualityScore,
      },
      create: {
        id: gameId,
        authorId: def.authorId,
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
        commentCount: def.commentCount,
        qualityScore: def.qualityScore,
        avgPlayTime: def.avgPlayTime,
        publishedAt,
        createdAt: publishedAt,
      },
    });

    // Create GameBundle with actual HTML code
    await prisma.gameBundle.upsert({
      where: { uk_game_version: { gameId: game.id, version: 1 } },
      update: {},
      create: {
        id: bundleId,
        gameId: game.id,
        version: 1,
        htmlCode: def.htmlCode,
        codeSizeBytes: Buffer.byteLength(def.htmlCode, 'utf8'),
        spec: { gameType: def.gameType, tags: def.tags },
        generationMeta: { method: 'manual', aiAssisted: false },
        metadata: { title: def.title, description: def.description },
      },
    });

    games.push(game);
    console.log(`  ✅ ${def.title}`);
  }

  // ============================================================
  // Step 3: Social Interactions (likes & follows)
  // ============================================================
  console.log('\n👥 创建社交互动数据...');

  // Follow relationships
  const follows = [
    { followerId: bob.id, followingId: alice.id },
    { followerId: bob.id, followingId: carol.id },
    { followerId: david.id, followingId: alice.id },
    { followerId: david.id, followingId: emma.id },
    { followerId: carol.id, followingId: alice.id },
    { followerId: emma.id, followingId: alice.id },
  ];

  for (const f of follows) {
    await prisma.userFollow.upsert({
      where: { unique_follow: f },
      update: {},
      create: { ...f, createdAt: new Date(Date.now() - Math.random() * 60 * 86400000) },
    });
  }

  // Game likes
  const likes = [
    { userId: bob.id, gameIdx: 0 },
    { userId: bob.id, gameIdx: 2 },
    { userId: bob.id, gameIdx: 4 },
    { userId: david.id, gameIdx: 0 },
    { userId: david.id, gameIdx: 3 },
    { userId: david.id, gameIdx: 4 },
    { userId: carol.id, gameIdx: 1 },
    { userId: carol.id, gameIdx: 3 },
    { userId: emma.id, gameIdx: 0 },
    { userId: emma.id, gameIdx: 2 },
    { userId: alice.id, gameIdx: 4 },
  ];

  for (const l of likes) {
    await prisma.socialInteraction.upsert({
      where: {
        unique_interaction: {
          userId: l.userId,
          targetType: 'game',
          targetId: games[l.gameIdx].id,
          action: 'like',
        },
      },
      update: {},
      create: {
        userId: l.userId,
        targetType: 'game',
        targetId: games[l.gameIdx].id,
        action: 'like',
        createdAt: new Date(Date.now() - Math.random() * 30 * 86400000),
      },
    });
  }

  console.log(`  ✅ ${follows.length} 个关注关系，${likes.length} 个点赞`);

  // ============================================================
  // Step 4: Comments
  // ============================================================
  console.log('\n💬 创建游戏评论...');

  const commentData = [
    { gameIdx: 0, userId: bob.id, content: '太好玩了！霓虹效果超酷，操控很流畅。贪吃蛇的经典玩法加上现代视觉，完美！' },
    { gameIdx: 0, userId: david.id, content: '已经玩了好几个小时了，停不下来。最高分破100了！' },
    { gameIdx: 0, userId: carol.id, content: '开源的话可以研究下代码，感觉实现得很精妙。' },
    { gameIdx: 1, userId: bob.id, content: '打砖块游戏的多关卡设计很赞，越往后越难，挑战感十足！' },
    { gameIdx: 1, userId: david.id, content: '鼠标控制挡板很精准，视觉效果也好看。强推！' },
    { gameIdx: 2, userId: alice.id, content: '超级萌！小动物们的表情包太可爱了。适合全家一起玩。' },
    { gameIdx: 2, userId: bob.id, content: '炸弹机制加的好，会让人不由自主地紧张起来，哈哈。' },
    { gameIdx: 3, userId: carol.id, content: '2048经典之作，这个实现很干净，动画流畅，UI也好看。' },
    { gameIdx: 3, userId: david.id, content: '终于合到2048了！！激动！！！' },
    { gameIdx: 4, userId: bob.id, content: '飞翔小鸟永远的神！这个版本画面比原版好看多了。' },
    { gameIdx: 4, userId: david.id, content: '最高15分，求大佬攻略！这游戏真的一点容错率都没有哈哈。' },
    { gameIdx: 4, userId: carol.id, content: '云彩的动画效果做得很细腻，感觉作者很用心。' },
    { gameIdx: 5, userId: alice.id, content: '记忆游戏对老年人很友好，推荐给家里的爷爷奶奶一起玩！' },
    { gameIdx: 5, userId: bob.id, content: '16步完成了！这个卡牌翻牌做得很精致。' },
  ];

  for (const cd of commentData) {
    await prisma.comment.create({
      data: {
        id: randomUUID(),
        gameId: games[cd.gameIdx].id,
        userId: cd.userId,
        content: cd.content,
        status: 'visible',
        createdAt: new Date(Date.now() - Math.random() * 20 * 86400000),
      },
    });
  }

  console.log(`  ✅ ${commentData.length} 条评论`);

  // ============================================================
  // Step 5: Notifications
  // ============================================================
  console.log('\n🔔 创建系统通知...');

  const notifications = [
    { userId: alice.id, actorId: bob.id, type: 'follow' as const, content: 'Bob Johnson 开始关注了你', targetId: null },
    { userId: alice.id, actorId: david.id, type: 'like' as const, content: 'David Lee 点赞了你的游戏《贪吃蛇进化版》', targetId: games[0].id },
    { userId: alice.id, actorId: emma.id, type: 'like' as const, content: 'Emma Wilson 点赞了你的游戏《飞翔小鸟》', targetId: games[4].id },
    { userId: carol.id, actorId: alice.id, type: 'follow' as const, content: 'Alice Chen 开始关注了你', targetId: null },
    { userId: carol.id, actorId: bob.id, type: 'comment' as const, content: 'Bob Johnson 评论了你的游戏《打地鼠！》', targetId: games[2].id },
    { userId: emma.id, actorId: carol.id, type: 'fork' as const, content: 'Carol Adams fork了你的游戏《2048 极限挑战》', targetId: games[3].id },
    { userId: alice.id, actorId: null, type: 'system' as const, content: '🎉 恭喜！你的游戏《飞翔小鸟》累计游玩次数突破 40,000 次！', targetId: games[4].id },
    { userId: emma.id, actorId: null, type: 'earning' as const, content: '💰 本周收益结算：你获得了 ¥286.50 创作收益', targetId: null },
  ];

  for (const n of notifications) {
    await prisma.notification.create({
      data: {
        id: randomUUID(),
        userId: n.userId,
        actorId: n.actorId ?? undefined,
        type: n.type,
        content: n.content,
        targetId: n.targetId ?? undefined,
        isRead: Math.random() > 0.5,
        createdAt: new Date(Date.now() - Math.random() * 7 * 86400000),
      },
    });
  }

  console.log(`  ✅ ${notifications.length} 条通知`);

  // ============================================================
  // Step 6: Creator Earnings
  // ============================================================
  console.log('\n💰 创建创作者收益记录...');

  const earningEntries = [
    { creatorId: alice.id, gameId: games[0].id, amount: '1250.50', earningType: 'ad_revenue' as const },
    { creatorId: alice.id, gameId: games[1].id, amount: '856.30', earningType: 'ad_revenue' as const },
    { creatorId: alice.id, gameId: games[4].id, amount: '2340.80', earningType: 'ad_revenue' as const },
    { creatorId: carol.id, gameId: games[2].id, amount: '1680.00', earningType: 'ad_revenue' as const },
    { creatorId: carol.id, gameId: games[2].id, amount: '120.00', earningType: 'tip' as const },
    { creatorId: emma.id, gameId: games[3].id, amount: '980.75', earningType: 'ad_revenue' as const },
    { creatorId: emma.id, gameId: games[5].id, amount: '450.20', earningType: 'ad_revenue' as const },
  ];

  for (const e of earningEntries) {
    const weekStart = new Date(Date.now() - 7 * 86400000);
    weekStart.setHours(0, 0, 0, 0);
    await prisma.creatorEarning.create({
      data: {
        creatorId: e.creatorId,
        gameId: e.gameId,
        earningType: e.earningType,
        amount: e.amount,
        currency: 'CNY',
        status: 'settled',
        periodStart: weekStart,
        periodEnd: new Date(),
        settledAt: new Date(),
      },
    });
  }

  console.log(`  ✅ ${earningEntries.length} 条收益记录`);

  // ============================================================
  // Summary
  // ============================================================
  const [userCnt, gameCnt, bundleCnt, commentCnt, notifCnt] = await Promise.all([
    prisma.user.count(),
    prisma.game.count(),
    prisma.gameBundle.count(),
    prisma.comment.count(),
    prisma.notification.count(),
  ]);

  console.log(`
╔══════════════════════════════════════════╗
║        数据库填充完成 ✅                  ║
╠══════════════════════════════════════════╣
║  用户数:      ${String(userCnt).padEnd(26)}║
║  游戏数:      ${String(gameCnt).padEnd(26)}║
║  代码包数:    ${String(bundleCnt).padEnd(26)}║
║  评论数:      ${String(commentCnt).padEnd(26)}║
║  通知数:      ${String(notifCnt).padEnd(26)}║
╠══════════════════════════════════════════╣
║  测试账号 (密码均为 password123):         ║
║  alice@gamevallies.com    (creator)      ║
║  bob@gamevallies.com      (user)         ║
║  carol@gamevallies.com    (creator)      ║
║  david@gamevallies.com    (user)         ║
║  emma@gamevallies.com     (creator)      ║
╚══════════════════════════════════════════╝
`);
}

main()
  .catch((e) => { console.error('❌ 填充失败:', e); process.exit(1); })
  .finally(() => prisma.$disconnect());
