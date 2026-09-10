"""Measure text actually drawn by Canvas instead of trusting source comments."""

CANVAS_TEXT_PROBE = r"""<script>(function(){
const p=CanvasRenderingContext2D.prototype,frames=new Map();
const fill=p.fillText,stroke=p.strokeText,clear=p.clearRect,rect=p.fillRect;
function records(c){if(!frames.has(c)&&frames.size<20)frames.set(c,[]);return frames.get(c);}
function reset(c,x,y,w,h){const t=c.getTransform();
 if(Math.abs(t.b)+Math.abs(t.c)>1e-6)return;
 const x1=t.a*x+t.e,x2=t.a*(x+w)+t.e,y1=t.d*y+t.f,y2=t.d*(y+h)+t.f;
 if(Math.min(x1,x2)<=0&&Math.max(x1,x2)>=c.canvas.width&&Math.min(y1,y2)<=0&&Math.max(y1,y2)>=c.canvas.height)
  frames.set(c,[]);
}
p.clearRect=function(x,y,w,h){reset(this,x,y,w,h);return clear.apply(this,arguments)};
p.fillRect=function(x,y,w,h){if(this.globalAlpha===1)reset(this,x,y,w,h);return rect.apply(this,arguments)};
function record(c,text,x,y,maxWidth){
 text=String(text);if(!text.trim()||c.globalAlpha<.5)return;
 const t=c.getTransform();if(Math.abs(t.b)+Math.abs(t.c)>1e-6)return;
 const a=records(c);if(!a||a.length>=250)return;
 const m=c.measureText(text),scale=Number.isFinite(maxWidth)&&maxWidth>0&&m.width>maxWidth?maxWidth/m.width:1;
 const left=x-m.actualBoundingBoxLeft*scale,right=x+m.actualBoundingBoxRight*scale;
 const top=y-m.actualBoundingBoxAscent,bottom=y+m.actualBoundingBoxDescent;
 const box={left:Math.min(t.a*left+t.e,t.a*right+t.e),right:Math.max(t.a*left+t.e,t.a*right+t.e),
  top:Math.min(t.d*top+t.f,t.d*bottom+t.f),bottom:Math.max(t.d*top+t.f,t.d*bottom+t.f)};
 if(!Object.values(box).every(Number.isFinite)||box.right-box.left<2||box.bottom-box.top<5)return;
 a.push({text:text.slice(0,100),...box});
}
p.fillText=function(text,x,y,maxWidth){record(this,text,x,y,maxWidth);return fill.apply(this,arguments)};
p.strokeText=function(text,x,y,maxWidth){record(this,text,x,y,maxWidth);return stroke.apply(this,arguments)};
Object.defineProperty(window,'__workTextEvidence',{value:()=>{
 const issues=[];
 for(const [ctx,items] of frames){
  if(!ctx.canvas.isConnected)continue;
  const bounds=ctx.canvas.getBoundingClientRect();if(!bounds.width||!bounds.height)continue;
  for(let i=0;i<items.length&&issues.length<20;i++){
   const a=items[i];
   if(a.left < -2||a.top < -2||a.right>ctx.canvas.width+2||a.bottom>ctx.canvas.height+2)
    issues.push({type:'canvas_text_clipped',canvas:ctx.canvas.id,text:a.text,box:a,width:ctx.canvas.width,height:ctx.canvas.height});
   for(let j=0;j<i&&issues.length<20;j++){
    const b=items[j];if(a.text===b.text)continue; // same-text outline/shadow is intentional
    const area=Math.max(0,Math.min(a.right,b.right)-Math.max(a.left,b.left))*Math.max(0,Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top));
    const smaller=Math.min((a.right-a.left)*(a.bottom-a.top),(b.right-b.left)*(b.bottom-b.top));
    if(area/smaller>.65)issues.push({type:'canvas_text_overlap',canvas:ctx.canvas.id,texts:[b.text,a.text],
      overlapRatio:Math.round(area/smaller*100)/100,boxes:[b,a]});
   }
  }
 }
 return issues.slice(0,20);
}});
})();</script>"""
