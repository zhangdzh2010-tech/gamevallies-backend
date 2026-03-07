/**
 * 移动端游戏测试数据 v2
 * 6 款专为触摸屏手机设计的 HTML5 游戏
 * 运行前会清除 demo_creator 的旧游戏数据，重新写入新游戏
 */

import { PrismaClient } from '@prisma/client';
import { MongoClient } from 'mongodb';
import { randomUUID } from 'crypto';
import { createHash } from 'crypto';

const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://playforge:playforge_dev_2026@localhost:5433/playforge';
const MONGO_URL = process.env.MONGO_URL || 'mongodb://playforge:playforge_dev_2026@localhost:27017/playforge?authSource=admin';
const APP_URL = process.env.APP_URL || 'http://localhost:3002';

const prisma = new PrismaClient({ datasources: { db: { url: DATABASE_URL } } });

// ── 6 Mobile-First HTML5 Games ──────────────────────────────────────────────

const GAMES = [
  // ── Game 1: 叠叠高塔 ──────────────────────────────────────────────────────
  {
    title: '叠叠高塔',
    description: '触摸放下移动的方块，只保留与下方重叠的部分，看你能叠多高！方块越叠越窄，失误即倒塌。挑战自己的反应极限！',
    gameType: 'casual',
    tags: ['休闲', '触控', '技巧', '单人'],
    qualityScore: 91,
    playCount: 5230,
    likeCount: 734,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>叠叠高塔</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;font-family:Arial,sans-serif;color:#fff;overflow:hidden;user-select:none;-webkit-user-select:none}
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
var W=Math.min(window.innerWidth-16,340);
var H=Math.min(window.innerHeight-180,460);
C.width=W;C.height=H;
var BH=24,COLS=['#16c79a','#58a6ff','#f5a623','#f06292','#ab47bc','#26c6da','#66bb6a'];
var blocks,mov,score,best=0,running=false,camY=0,raf;

function rr(x,y,w,h,r){
  ctx.beginPath();
  ctx.moveTo(x+r,y);
  ctx.lineTo(x+w-r,y);
  ctx.arcTo(x+w,y,x+w,y+r,r);
  ctx.lineTo(x+w,y+h-r);
  ctx.arcTo(x+w,y+h,x+w-r,y+h,r);
  ctx.lineTo(x+r,y+h);
  ctx.arcTo(x,y+h,x,y+h-r,r);
  ctx.lineTo(x,y+r);
  ctx.arcTo(x,y,x+r,y,r);
  ctx.closePath();
}

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
    var dy=b.y+camY;
    if(dy>H+BH||dy<-BH)return;
    ctx.fillStyle=b.c;rr(b.x,dy,b.w,BH-2,3);ctx.fill();
    ctx.fillStyle='rgba(255,255,255,0.25)';ctx.fillRect(b.x,dy,b.w,5);
  });
  if(running){
    mov.x+=mov.dir*mov.spd;
    if(mov.x+mov.w>W+30)mov.dir=-1;
    if(mov.x<-30)mov.dir=1;
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
  ctx.textAlign='left';
  document.getElementById('btn').style.display='block';
  document.getElementById('msg').textContent='';
}

C.addEventListener('touchstart',function(e){e.preventDefault();drop();},{passive:false});
C.addEventListener('click',function(){drop();});
ctx.fillStyle='#0a0a1a';ctx.fillRect(0,0,W,H);
ctx.textAlign='center';ctx.fillStyle='#f5a623';ctx.font='bold 22px Arial';ctx.fillText('触摸开始',W/2,H/2);ctx.textAlign='left';
</script>
</body>
</html>`,
  },

  // ── Game 2: 水果忍者 ──────────────────────────────────────────────────────
  {
    title: '水果忍者',
    description: '手指滑动切开飞来的水果，小心别碰到炸弹！连切多个水果触发连击加分。60秒内追求最高分，体验畅快的切割快感！',
    gameType: 'action',
    tags: ['动作', '滑动', '反应', '休闲'],
    qualityScore: 94,
    playCount: 8910,
    likeCount: 1203,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>水果忍者</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1b2a;height:100vh;overflow:hidden;font-family:Arial,sans-serif;user-select:none;-webkit-user-select:none}
#hud{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-between;padding:12px 20px;background:rgba(0,0,0,0.6);color:#fff;font-size:1rem;z-index:10}
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
  document.getElementById('sc').textContent=0;
  document.getElementById('hp').textContent=3;
  document.getElementById('tm').textContent=60;
  clearInterval(gameInt);clearTimeout(spawnT);
  gameInt=setInterval(function(){
    if(!running)return;
    timeLeft--;document.getElementById('tm').textContent=timeLeft;
    if(timeLeft<=0)endGame();
  },1000);
  doSpawn();
  cancelAnimationFrame(raf);loop();
}

function doSpawn(){
  if(!running)return;
  var bomb=Math.random()<0.18;
  var r=26+Math.random()*18;
  var x=r+Math.random()*(W-r*2);
  var vy=-(H*0.017+Math.random()*H*0.007);
  items.push({x:x,y:H+r,vx:(Math.random()-0.5)*5,vy:vy,r:r,
    emoji:bomb?'💣':FRUITS[Math.floor(Math.random()*10)],bomb:bomb,cut:false,alpha:1});
  var delay=Math.max(250,850-score*4);
  spawnT=setTimeout(doSpawn,delay);
}

function segCircleDist(x1,y1,x2,y2,cx,cy){
  var dx=x2-x1,dy=y2-y1,len2=dx*dx+dy*dy;
  if(len2<1)return Math.sqrt((x1-cx)*(x1-cx)+(y1-cy)*(y1-cy));
  var t=Math.max(0,Math.min(1,((cx-x1)*dx+(cy-y1)*dy)/len2));
  return Math.sqrt((x1+dx*t-cx)*(x1+dx*t-cx)+(y1+dy*t-cy)*(y1+dy*t-cy));
}

function loop(){
  raf=requestAnimationFrame(loop);
  ctx.clearRect(0,0,W,H);
  if(trail.length>1){
    for(var i=1;i<trail.length;i++){
      var a=(i/trail.length)*0.9;
      ctx.strokeStyle='rgba(255,255,255,'+a+')';
      ctx.lineWidth=3+a*2;ctx.lineCap='round';
      ctx.beginPath();ctx.moveTo(trail[i-1].x,trail[i-1].y);ctx.lineTo(trail[i].x,trail[i].y);ctx.stroke();
    }
  }
  items=items.filter(function(it){
    it.vy+=0.3;it.x+=it.vx;it.y+=it.vy;
    if(it.cut){it.y+=it.vy*0.5;it.alpha-=0.07;if(it.alpha<=0)return false;}
    else if(it.y>H+60){
      if(!it.bomb){
        lives--;document.getElementById('hp').textContent=Math.max(0,lives);
        if(lives<=0)endGame();
      }
      return false;
    }
    ctx.save();ctx.globalAlpha=it.alpha;
    ctx.font=(it.r*2)+'px serif';ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.fillText(it.emoji,it.x,it.y);
    ctx.restore();
    return true;
  });
}

function slash(x1,y1,x2,y2){
  if(!running)return;
  var len=Math.sqrt((x2-x1)*(x2-x1)+(y2-y1)*(y2-y1));
  if(len<10)return;
  for(var i=items.length-1;i>=0;i--){
    var it=items[i];
    if(it.cut)continue;
    if(segCircleDist(x1,y1,x2,y2,it.x,it.y)<it.r){
      it.cut=true;
      if(it.bomb){
        lives--;document.getElementById('hp').textContent=Math.max(0,lives);
        if(lives<=0){endGame();return;}
      }else{
        score+=10;document.getElementById('sc').textContent=score;
      }
    }
  }
}

function endGame(){
  running=false;clearInterval(gameInt);clearTimeout(spawnT);
  var ov=document.getElementById('overlay');ov.style.display='flex';
  ov.innerHTML='<h2>🎉 游戏结束</h2><p style="font-size:1.5rem;color:#ffd700;margin:12px">得分: '+score+'</p><button onclick="startGame()" style="margin-top:20px;padding:14px 44px;background:#ffd700;color:#000;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer">再来一次</button>';
}

C.addEventListener('touchstart',function(e){
  e.preventDefault();
  var t=e.touches[0];pp=null;tp={x:t.clientX,y:t.clientY};trail=[tp];
},{passive:false});
C.addEventListener('touchmove',function(e){
  e.preventDefault();
  var t=e.touches[0];
  pp=tp;tp={x:t.clientX,y:t.clientY};
  trail.push(tp);if(trail.length>20)trail.shift();
  if(pp)slash(pp.x,pp.y,tp.x,tp.y);
},{passive:false});
C.addEventListener('touchend',function(){tp=null;pp=null;trail=[];});
</script>
</body>
</html>`,
  },

  // ── Game 3: 泡泡消消 ──────────────────────────────────────────────────────
  {
    title: '泡泡消消',
    description: '五颜六色的气泡不断上升，快速点击让它们爆掉！连续点击触发连击倍数，红色炸弹气泡会扣分。60秒内能拿多少分？',
    gameType: 'casual',
    tags: ['休闲', '触控', '连击', '单人'],
    qualityScore: 88,
    playCount: 4650,
    likeCount: 612,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>泡泡消消</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:linear-gradient(180deg,#0d1b3e,#1a0533);height:100vh;overflow:hidden;font-family:Arial,sans-serif;user-select:none;-webkit-user-select:none}
#hud{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-around;padding:12px;background:rgba(0,0,0,0.5);color:#fff;z-index:10}
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
var BCOLORS=[['#a855f7','#7c3aed'],['#3b82f6','#1d4ed8'],['#06b6d4','#0891b2'],
  ['#10b981','#059669'],['#f59e0b','#d97706'],['#ec4899','#db2777']];
var bubbles=[],score=0,combo=1,comboT=null,timeLeft=60,running=false,raf,gameInt,spawnT;

function startGame(){
  document.getElementById('overlay').style.display='none';
  bubbles=[];score=0;combo=1;timeLeft=60;running=true;
  document.getElementById('sc').textContent=0;
  document.getElementById('combo').textContent='x1';
  document.getElementById('tm').textContent=60;
  clearInterval(gameInt);clearTimeout(spawnT);
  gameInt=setInterval(function(){
    if(!running)return;
    timeLeft--;document.getElementById('tm').textContent=timeLeft;
    if(timeLeft<=0)endGame();
  },1000);
  doSpawn();cancelAnimationFrame(raf);loop();
}

function doSpawn(){
  if(!running)return;
  var bomb=Math.random()<0.14;
  var r=22+Math.random()*24;
  var ci=Math.floor(Math.random()*BCOLORS.length);
  bubbles.push({
    x:r+Math.random()*(W-r*2),y:H+r,r:r,
    spd:0.7+Math.random()*1.1,bomb:bomb,ci:ci,
    alpha:1,popped:false,pa:0,
    wobble:Math.random()*6.28,ws:0.03+Math.random()*0.02
  });
  var delay=Math.max(180,800-score*2);
  spawnT=setTimeout(doSpawn,delay);
}

function loop(){
  raf=requestAnimationFrame(loop);
  ctx.clearRect(0,0,W,H);
  bubbles=bubbles.filter(function(b){
    if(b.popped){
      b.pa+=0.12;
      if(b.pa>1)return false;
      var cl=b.bomb?'#ef4444':BCOLORS[b.ci][0];
      ctx.save();ctx.globalAlpha=1-b.pa;
      ctx.strokeStyle=cl;ctx.lineWidth=3;
      ctx.beginPath();ctx.arc(b.x,b.y,b.r*(1+b.pa*0.8),0,Math.PI*2);ctx.stroke();
      ctx.restore();return true;
    }
    b.wobble+=b.ws;b.y-=b.spd;
    var wx=Math.sin(b.wobble)*2;
    if(b.y+b.r<HH)return false;
    var cl=b.bomb?['#ef4444','#b91c1c']:BCOLORS[b.ci];
    var g=ctx.createRadialGradient(b.x+wx-b.r*0.3,b.y-b.r*0.3,b.r*0.1,b.x+wx,b.y,b.r);
    g.addColorStop(0,cl[0]+'cc');g.addColorStop(1,cl[1]+'99');
    ctx.beginPath();ctx.arc(b.x+wx,b.y,b.r,0,Math.PI*2);
    ctx.fillStyle=g;ctx.fill();
    ctx.strokeStyle=cl[0];ctx.lineWidth=2;ctx.stroke();
    ctx.beginPath();ctx.arc(b.x+wx-b.r*0.25,b.y-b.r*0.3,b.r*0.3,0,Math.PI*2);
    ctx.fillStyle='rgba(255,255,255,0.25)';ctx.fill();
    return true;
  });
}

function handleTap(px,py){
  if(!running)return;
  for(var i=bubbles.length-1;i>=0;i--){
    var b=bubbles[i];if(b.popped)continue;
    var wx=Math.sin(b.wobble)*2;
    if(Math.sqrt((px-b.x-wx)*(px-b.x-wx)+(py-b.y)*(py-b.y))<b.r){
      b.popped=true;
      if(b.bomb){
        score=Math.max(0,score-20*combo);combo=1;
        document.getElementById('combo').textContent='x1';
      }else{
        score+=10*combo*(b.r>36?2:1);
        combo=Math.min(combo+1,10);
        clearTimeout(comboT);
        comboT=setTimeout(function(){combo=1;document.getElementById('combo').textContent='x1';},1500);
        document.getElementById('combo').textContent='x'+combo;
      }
      document.getElementById('sc').textContent=score;break;
    }
  }
}

function endGame(){
  running=false;clearInterval(gameInt);clearTimeout(spawnT);
  var ov=document.getElementById('overlay');ov.style.display='flex';
  ov.innerHTML='<h2>🎉 时间到！</h2><p style="font-size:1.5rem;color:#a855f7;margin:12px">得分: '+score+'</p><button onclick="startGame()" style="margin-top:20px;padding:14px 44px;background:linear-gradient(135deg,#a855f7,#3b82f6);color:#fff;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer">再玩一次</button>';
}

C.addEventListener('touchstart',function(e){
  e.preventDefault();
  Array.from(e.changedTouches).forEach(function(t){handleTap(t.clientX,t.clientY);});
},{passive:false});
C.addEventListener('click',function(e){handleTap(e.clientX,e.clientY);});
</script>
</body>
</html>`,
  },

  // ── Game 4: 消消星 ──────────────────────────────────────────────────────
  {
    title: '消消星',
    description: '点击 2 个以上相邻的同色星星将其消除！消除的星星越多，得分越高。星星会自动下落填补空缺，60秒内追求最高分！',
    gameType: 'puzzle',
    tags: ['益智', '消除', '策略', '触控'],
    qualityScore: 89,
    playCount: 3870,
    likeCount: 521,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>消消星</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a0533;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;font-family:Arial,sans-serif;color:#fff;user-select:none;-webkit-user-select:none;padding-top:10px}
h1{font-size:1.4rem;color:#f59e0b;margin-bottom:6px}
#hud{display:flex;gap:20px;margin-bottom:10px;background:rgba(255,255,255,0.08);padding:8px 20px;border-radius:20px}
.hl{font-size:0.7rem;color:#aaa;display:block;text-align:center}
.hv{font-size:1.4rem;font-weight:bold;color:#ffd700;display:block;text-align:center}
#board{display:grid;gap:3px;margin-bottom:8px}
.cell{width:46px;height:46px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1.4rem;cursor:pointer;transition:transform 0.12s,opacity 0.25s;-webkit-tap-highlight-color:transparent;border:2px solid transparent}
.cell.removing{transform:scale(0);opacity:0}
#msg{font-size:0.85rem;color:#aaa;min-height:20px;text-align:center;margin-bottom:6px}
#btn{padding:12px 36px;background:#f59e0b;color:#000;border:none;border-radius:25px;font-size:1rem;font-weight:bold;cursor:pointer;display:none;margin-top:8px}
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

function init(){
  score=0;timeLeft=60;running=true;animating=false;
  document.getElementById('sc').textContent=0;
  document.getElementById('tm').textContent=60;
  document.getElementById('msg').textContent='点击 2 个以上相邻同色星星消除';
  document.getElementById('btn').style.display='none';
  clearInterval(gameInt);
  gameInt=setInterval(function(){
    if(!running)return;
    timeLeft--;document.getElementById('tm').textContent=timeLeft;
    if(timeLeft<=0)endGame();
  },1000);
  grid=[];
  for(var r=0;r<ROWS;r++){
    grid.push([]);
    for(var c=0;c<COLS;c++)grid[r].push(Math.floor(Math.random()*COLORS.length));
  }
  render();
}

function render(){
  var board=document.getElementById('board');
  board.style.gridTemplateColumns='repeat('+COLS+', 46px)';
  board.innerHTML='';
  for(var r=0;r<ROWS;r++){
    for(var c=0;c<COLS;c++){
      var cell=document.createElement('div');
      var ci=grid[r][c];
      cell.className='cell';
      if(ci>=0){
        cell.style.background=COLORS[ci]+'33';
        cell.style.borderColor=COLORS[ci];
        cell.textContent=EMOJIS[ci];
        (function(rr,cc){
          cell.addEventListener('touchstart',function(e){e.preventDefault();tap(rr,cc);},{passive:false});
          cell.addEventListener('click',function(){tap(rr,cc);});
        })(r,c);
      }else{
        cell.style.background='rgba(255,255,255,0.04)';
      }
      board.appendChild(cell);
    }
  }
}

function bfs(r,c,col){
  var visited=[];
  for(var i=0;i<ROWS;i++){visited.push([]);for(var j=0;j<COLS;j++)visited[i].push(false);}
  var q=[[r,c]],group=[];
  visited[r][c]=true;
  while(q.length){
    var cur=q.shift();var cr=cur[0],cc=cur[1];
    group.push([cr,cc]);
    var dirs=[[-1,0],[1,0],[0,-1],[0,1]];
    for(var d=0;d<dirs.length;d++){
      var nr=cr+dirs[d][0],nc=cc+dirs[d][1];
      if(nr>=0&&nr<ROWS&&nc>=0&&nc<COLS&&!visited[nr][nc]&&grid[nr][nc]===col){
        visited[nr][nc]=true;q.push([nr,nc]);
      }
    }
  }
  return group;
}

function tap(r,c){
  if(animating||!running||grid[r][c]<0)return;
  var col=grid[r][c];
  var group=bfs(r,c,col);
  if(group.length<2){
    document.getElementById('msg').textContent='需要相邻的 2 个以上同色星星！';return;
  }
  animating=true;
  var pts=group.length*group.length*10;
  score+=pts;
  if(score>best){best=score;document.getElementById('best').textContent=best;}
  document.getElementById('sc').textContent=score;
  document.getElementById('msg').textContent='+'+pts+' 分！连续消除得更多！';
  var cells=document.getElementById('board').children;
  for(var i=0;i<group.length;i++){
    cells[group[i][0]*COLS+group[i][1]].classList.add('removing');
  }
  setTimeout(function(){
    for(var i=0;i<group.length;i++)grid[group[i][0]][group[i][1]]=-1;
    for(var cc=0;cc<COLS;cc++){
      var wr=ROWS-1;
      for(var rr=ROWS-1;rr>=0;rr--){
        if(grid[rr][cc]>=0){grid[wr][cc]=grid[rr][cc];if(wr!==rr)grid[rr][cc]=-1;wr--;}
      }
      while(wr>=0){grid[wr][cc]=Math.floor(Math.random()*COLORS.length);wr--;}
    }
    render();animating=false;
  },280);
}

function endGame(){
  running=false;clearInterval(gameInt);
  document.getElementById('msg').textContent='时间到！最终得分: '+score;
  document.getElementById('btn').style.display='block';
}

init();
</script>
</body>
</html>`,
  },

  // ── Game 5: 节奏达人 ──────────────────────────────────────────────────────
  {
    title: '节奏达人',
    description: '彩色圆圈出现并缩小，在圆圈消失前点击它！连续点击触发连击加成。圆圈越来越多越来越快，手速和反应力的终极考验！',
    gameType: 'casual',
    tags: ['反应', '触控', '连击', '挑战'],
    qualityScore: 92,
    playCount: 6340,
    likeCount: 891,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>节奏达人</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a1a;height:100vh;overflow:hidden;font-family:Arial,sans-serif;user-select:none;-webkit-user-select:none}
#hud{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-around;padding:12px;background:rgba(0,0,0,0.6);color:#fff;z-index:10}
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
var circles=[],score=0,combo=0,lives=5,timeLeft=60,running=false,raf,gameInt,spawnT;
var lastTime=0;

function startGame(){
  document.getElementById('overlay').style.display='none';
  circles=[];score=0;combo=0;lives=5;timeLeft=60;running=true;
  document.getElementById('sc').textContent=0;
  document.getElementById('combo').textContent='x0';
  document.getElementById('hp').textContent=5;
  document.getElementById('tm').textContent=60;
  clearInterval(gameInt);clearTimeout(spawnT);
  gameInt=setInterval(function(){
    if(!running)return;
    timeLeft--;document.getElementById('tm').textContent=timeLeft;
    if(timeLeft<=0)endGame();
  },1000);
  scheduleSpawn();
  cancelAnimationFrame(raf);lastTime=performance.now();loop(lastTime);
}

function scheduleSpawn(){
  if(!running)return;
  var delay=Math.max(350,1100-score*1.5);
  spawnT=setTimeout(function(){
    if(!running)return;
    if(circles.length<6){
      var r=32+Math.random()*22;
      var margin=r+50;
      circles.push({
        x:margin+Math.random()*(W-margin*2),
        y:HH+margin+Math.random()*(H-HH-margin*2),
        r:r,life:0,maxLife:1.6+Math.random()*1.4,
        color:PAL[Math.floor(Math.random()*PAL.length)],
        popping:false,pa:0
      });
    }
    scheduleSpawn();
  },delay);
}

function loop(now){
  raf=requestAnimationFrame(loop);
  var dt=Math.min((now-lastTime)/1000,0.05);lastTime=now;
  ctx.clearRect(0,0,W,H);
  circles=circles.filter(function(c){
    if(c.popping){
      c.pa+=dt*5;
      if(c.pa>1)return false;
      ctx.save();ctx.globalAlpha=1-c.pa;
      ctx.strokeStyle=c.color;ctx.lineWidth=3;
      ctx.beginPath();ctx.arc(c.x,c.y,c.r*(1+c.pa),0,Math.PI*2);ctx.stroke();
      ctx.restore();return true;
    }
    c.life+=dt;
    if(c.life>=c.maxLife){
      lives--;document.getElementById('hp').textContent=Math.max(0,lives);
      combo=0;document.getElementById('combo').textContent='x'+combo;
      if(lives<=0){endGame();return false;}
      return false;
    }
    var prog=c.life/c.maxLife;
    var shrink=1-prog*0.55;
    var cr=c.r*shrink;
    var pulse=1+Math.sin(c.life*7)*0.03;
    ctx.strokeStyle=c.color;ctx.lineWidth=5;ctx.globalAlpha=0.35;
    ctx.beginPath();ctx.arc(c.x,c.y,cr*1.5,0,Math.PI*2);ctx.stroke();
    ctx.globalAlpha=1;
    ctx.strokeStyle=c.color;ctx.lineWidth=5;
    ctx.beginPath();
    ctx.arc(c.x,c.y,cr*1.5,-Math.PI/2,-Math.PI/2+Math.PI*2*(1-prog));ctx.stroke();
    var g=ctx.createRadialGradient(c.x-cr*0.3,c.y-cr*0.3,cr*0.1,c.x,c.y,cr*pulse);
    g.addColorStop(0,c.color+'dd');g.addColorStop(1,c.color+'55');
    ctx.beginPath();ctx.arc(c.x,c.y,cr*pulse,0,Math.PI*2);
    ctx.fillStyle=g;ctx.fill();
    var secs=Math.ceil(c.maxLife-c.life);
    ctx.fillStyle='#fff';ctx.font='bold '+(cr*0.65)+'px Arial';
    ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.fillText(secs>0?secs:'',c.x,c.y);
    ctx.textAlign='left';ctx.textBaseline='alphabetic';
    return true;
  });
}

function handleTap(px,py){
  if(!running)return;
  for(var i=circles.length-1;i>=0;i--){
    var c=circles[i];if(c.popping)continue;
    var prog=c.life/c.maxLife,cr=c.r*(1-prog*0.55);
    if(Math.sqrt((px-c.x)*(px-c.x)+(py-c.y)*(py-c.y))<cr*1.5){
      c.popping=true;
      combo++;var pts=10*(1+Math.min(combo,10));score+=pts;
      document.getElementById('sc').textContent=score;
      document.getElementById('combo').textContent='x'+combo;
      break;
    }
  }
}

function endGame(){
  running=false;clearInterval(gameInt);clearTimeout(spawnT);
  var ov=document.getElementById('overlay');ov.style.display='flex';
  ov.innerHTML='<h2>🎵 游戏结束</h2><p style="font-size:1.5rem;color:#a855f7;margin:12px">得分: '+score+'</p><p style="color:#ffd700">最高连击: x'+combo+'</p><button onclick="startGame()" style="margin-top:20px;padding:14px 44px;background:#a855f7;color:#fff;border:none;border-radius:30px;font-size:1.1rem;font-weight:bold;cursor:pointer">再来一次</button>';
}

C.addEventListener('touchstart',function(e){
  e.preventDefault();
  Array.from(e.changedTouches).forEach(function(t){handleTap(t.clientX,t.clientY);});
},{passive:false});
C.addEventListener('click',function(e){handleTap(e.clientX,e.clientY);});
</script>
</body>
</html>`,
  },

  // ── Game 6: 接水果 ──────────────────────────────────────────────────────
  {
    title: '接水果',
    description: '拖动篮子接住从天而降的水果！水果越接越多越快，小心炸弹会扣掉宝贵的生命。60秒内接越多越好，挑战高分！',
    gameType: 'casual',
    tags: ['休闲', '拖拽', '触控', '反应'],
    qualityScore: 87,
    playCount: 4120,
    likeCount: 563,
    html: `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>接水果</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a4a0a;height:100vh;overflow:hidden;font-family:Arial,sans-serif;user-select:none;-webkit-user-select:none}
#hud{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-around;padding:12px;background:rgba(0,0,0,0.55);color:#fff;z-index:10}
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
var basketX=W/2;
var BW=90,BH2=44;
var floats=[];

function startGame(){
  document.getElementById('overlay').style.display='none';
  items=[];floats=[];score=0;lives=5;timeLeft=60;running=true;basketX=W/2;
  document.getElementById('sc').textContent=0;
  document.getElementById('hp').textContent=5;
  document.getElementById('tm').textContent=60;
  clearInterval(gameInt);clearTimeout(spawnT);
  gameInt=setInterval(function(){
    if(!running)return;
    timeLeft--;document.getElementById('tm').textContent=timeLeft;
    if(timeLeft<=0)endGame();
  },1000);
  doSpawn();cancelAnimationFrame(raf);loop();
}

function doSpawn(){
  if(!running)return;
  var bomb=Math.random()<0.18;
  var r=20+Math.random()*14;
  var x=r+Math.random()*(W-r*2);
  var spd=2.5+Math.random()*2+score*0.015;
  items.push({x:x,y:HH,r:r,spd:spd,
    emoji:bomb?'💣':FRUITS[Math.floor(Math.random()*10)],bomb:bomb});
  var delay=Math.max(280,900-score*4);
  spawnT=setTimeout(doSpawn,delay);
}

function loop(){
  raf=requestAnimationFrame(loop);
  ctx.clearRect(0,0,W,H);
  var by=H-BH2-36;
  var bx=basketX-BW/2;
  ctx.fillStyle='#2d5a1b';ctx.fillRect(0,H-50,W,50);
  ctx.strokeStyle='#4a8f2f';ctx.lineWidth=2;
  for(var i=0;i<W;i+=30){
    ctx.beginPath();ctx.moveTo(i,H-50);ctx.lineTo(i+15,H);ctx.stroke();
  }
  ctx.fillStyle='#8b4513';
  ctx.beginPath();
  ctx.moveTo(bx,by);
  ctx.lineTo(bx+BW,by);
  ctx.lineTo(bx+BW-12,by+BH2);
  ctx.lineTo(bx+12,by+BH2);
  ctx.closePath();ctx.fill();
  ctx.strokeStyle='#a0522d';ctx.lineWidth=3;ctx.stroke();
  ctx.strokeStyle='#6b3410';ctx.lineWidth=1.5;
  for(var j=0;j<4;j++){
    ctx.beginPath();ctx.moveTo(bx+10+j*18,by);ctx.lineTo(bx+14+j*18,by+BH2);ctx.stroke();
  }
  ctx.strokeStyle='#8b4513';ctx.lineWidth=4;
  ctx.beginPath();ctx.arc(basketX,by-14,BW/3,Math.PI,0);ctx.stroke();
  floats=floats.filter(function(f){
    f.y-=2;f.alpha-=0.04;
    if(f.alpha<=0)return false;
    ctx.save();ctx.globalAlpha=f.alpha;
    ctx.fillStyle='#ffd700';ctx.font='bold 18px Arial';
    ctx.textAlign='center';ctx.fillText(f.txt,f.x,f.y);
    ctx.restore();return true;
  });
  items=items.filter(function(it){
    it.y+=it.spd;
    if(it.y>H+it.r)return false;
    if(it.y+it.r>by&&it.y<by+BH2&&it.x>bx-8&&it.x<bx+BW+8){
      if(it.bomb){
        lives--;document.getElementById('hp').textContent=Math.max(0,lives);
        floats.push({x:it.x,y:by,txt:'-💣',alpha:1});
        if(lives<=0)endGame();
      }else{
        score+=10;document.getElementById('sc').textContent=score;
        floats.push({x:it.x,y:by,txt:'+10',alpha:1});
      }
      return false;
    }
    ctx.font=(it.r*2)+'px serif';
    ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.fillText(it.emoji,it.x,it.y);
    return true;
  });
  ctx.textAlign='left';ctx.textBaseline='alphabetic';
}

function handleMove(px){
  basketX=Math.max(BW/2,Math.min(W-BW/2,px));
}

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
</html>`,
  },
];

// ── Seed Logic ───────────────────────────────────────────────────────────────

async function seed() {
  console.log('🌱 开始写入移动端游戏数据...\n');

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
        bio: '热爱游戏创作的开发者，专注制作有趣的手机 HTML5 小游戏。',
        role: 'creator',
        gameCount: 0,
        totalPlays: 0,
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

  // 3. 清除旧游戏数据
  console.log('🗑️  清除旧游戏数据...');
  const oldGames = await prisma.game.findMany({
    where: { authorId: testUser.id },
    select: { id: true, title: true },
  });

  if (oldGames.length > 0) {
    const oldIds = oldGames.map(g => g.id);
    // 删除 MongoDB bundles
    const mongoResult = await bundles.deleteMany({ game_id: { $in: oldIds } });
    console.log(`  ▸ 删除 MongoDB bundles: ${mongoResult.deletedCount} 条`);
    // 删除 PostgreSQL records
    await prisma.game.deleteMany({ where: { authorId: testUser.id } });
    console.log(`  ▸ 删除 PostgreSQL games: ${oldGames.length} 条`);
    oldGames.forEach(g => console.log(`    - ${g.title} (${g.id})`));
  } else {
    console.log('  ▸ 无旧数据');
  }
  console.log();

  // 4. 插入 6 个新游戏
  const insertedGames = [];
  const totalPlays = GAMES.reduce((s, g) => s + g.playCount, 0);

  for (const g of GAMES) {
    const gameId = randomUUID();
    const previewUrl = `${APP_URL}/games/${gameId}/preview`;

    // PostgreSQL
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
        publishedAt: new Date(Date.now() - Math.random() * 14 * 24 * 3600 * 1000),
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
    console.log(`   ▸ 标签: ${g.tags.join(', ')}`);
    console.log(`   ▸ 预览: ${previewUrl}\n`);
    insertedGames.push({ title: g.title, id: gameId, previewUrl });
  }

  // 5. 更新用户计数
  await prisma.user.update({
    where: { id: testUser.id },
    data: {
      gameCount: GAMES.length,
      totalPlays: BigInt(totalPlays),
    },
  });

  await mongo.close();
  await prisma.$disconnect();

  console.log('═'.repeat(60));
  console.log('🎉 6 款手机游戏写入完成！\n');
  console.log('📱 游戏列表 (触摸屏专属):');
  insertedGames.forEach((g, i) => console.log(`  ${i + 1}. ${g.title} → GET ${g.previewUrl}`));
  console.log('\n🔍 探索接口:');
  console.log('  GET http://localhost:3002/api/v1/games/explore/published');
  console.log('  GET http://localhost:3002/api/v1/games/{id}');
  console.log('  GET http://localhost:3002/api/v1/games/{id}/bundle');
  console.log('  GET http://localhost:3004/api/v1/feed/trending');
}

seed().catch(err => {
  console.error('❌ 种子脚本失败:', err.message);
  console.error(err.stack);
  process.exit(1);
});
