/* Offline view: one RAF clock; all assets decoded before playback. */
'use strict';
const {Flow,wakeAt,smooth}=WatcherPreview;
const flow=new Flow(),el=id=>document.getElementById(id),ctx=el('screen').getContext('2d');
const images={};let speed=1,last=0,signature='',messageSignature='',smile=0,pan=0,tilt=113,auto=false;
const ink='#a5f339';
function round(x,y,w,h,r,color=ink){ctx.fillStyle=color;ctx.beginPath();ctx.roundRect(x-w/2,y-h/2,w,h,Math.min(r,w/2,h/2));ctx.fill();}
function face(p,speaking,time){
 ctx.save();ctx.scale(2,2);
 const centers=[59.35,146.65],dx=[-30.69,-18.42,-6.14,6.14,18.42,30.69],hs=[31.31,58.16,71.98,71.98,58.16,31.31],hy=[115.5,106.5,101,101,106.5,115.5],hh=[12,20,23,23,20,12];
 const gy=p.gaze_y*16,bp=time%5300,blink=flow.state==='wake'?0:bp>5020?(bp<5110?(bp-5020)/90:bp<5150?1:1-(bp-5150)/150):0;
 centers.forEach((cx,i)=>{const cy=103+gy;ctx.save();ctx.beginPath();ctx.rect(i?103:0,0,103,206);ctx.clip();
  for(let b=0;b<6;b++){const h=(hs[b]+(hh[b]-hs[b])*smile)*(1-blink);if(h>.01)round(cx+dx[b],cy+(hy[b]-103)*smile,6.14,h,3.07);}
  // Match draw_chat_eyes: pupil disappears at happy >= .65, not at 1.
  const pupilCollapse=smooth(smile/.65),rx=13.67*(1-.15*pupilCollapse),ry=13.67*(1-pupilCollapse);
  ctx.fillStyle='black';
  if(ry>0){ctx.beginPath();ctx.ellipse(cx,cy,rx,ry,0,0,Math.PI*2);ctx.fill();}
  ctx.fillRect(cx-56,cy+(i?p.right_upper:p.left_upper)-32,112,64);ctx.fillRect(cx-56,cy+p.lower-32,112,64);ctx.restore();
 });
 const open=speaking?(.16+.84*Math.abs(Math.sin(time*.016)*Math.sin(time*.007))):0;
 ctx.globalAlpha=p.mouth_opacity;const h=Math.max(.7,(1+14*open)*.65),w=24-3*open,r=Math.max(.35,(1+14*open)*.3);
 round(103,165.817,w+3,h+3,r+1.5);if(h>3)round(103,165.817,w-3,h-3,Math.max(.1,r-1.5),'black');ctx.restore();
}
function draw(dt){
 ctx.globalAlpha=1;ctx.fillStyle='black';ctx.fillRect(0,0,412,412);
 let p={left_upper:-80,right_upper:-80,lower:80,mouth_opacity:1,gaze_y:0,pan:0,tilt:113};
 const s=flow.state,t=flow.elapsed;
 const targetSmile=['hello','invite','photoDone','skip','chat','reply'].includes(s)?.72:0;
 smile+=(targetSmile-smile)*(1-Math.exp(-dt/220));
 if(['scan','network','failed'].includes(s))ctx.drawImage(images[s],0,0,412,412);
 else if(s==='fade'){ctx.globalAlpha=1-smooth(t/1200);ctx.drawImage(images.network,0,0,412,412);ctx.globalAlpha=1;}
 else if(s==='photo'||s==='thinking'){
  const duration=s==='photo'?3500:2000;
  const alpha=Math.min(smooth(t/250),smooth((duration-t)/250));
  face(p,false,flow.clock);
  ctx.globalAlpha=alpha;
  ctx.drawImage(s==='photo'?images.photo[Math.min(34,Math.floor(t/100))]:images.processing[Math.floor(t/100)%40],0,0,412,412);
  ctx.globalAlpha=1;
 }
 else{if(s==='wake')p=wakeAt(t);face(p,flow.speaking,flow.clock);}
 let tp=p.pan,tt=p.tilt;
 if(s==='peek'){tp=Math.sin(t/700)*2;tt=116-5*Math.cos(t/900);}if(s==='left')tp=15;if(s==='right')tp=-12*(1-smooth(t/2400));if(s==='hello')tt=120-4*Math.sin(Math.min(1,t/2300)*Math.PI);
 pan+=(tp-pan)*(1-Math.exp(-dt/650));tilt+=(tt-tilt)*(1-Math.exp(-dt/650));
 el('device').style.transform=`rotateY(${pan*.45}deg) rotateX(${(113-tilt)*.35}deg)`;
 el('time').textContent=`${String(Math.floor(flow.clock/60000)).padStart(2,'0')}:${String(Math.floor(flow.clock/1000)%60).padStart(2,'0')}`;
 el('progress').style.width=Number.isFinite(flow.duration)?`${Math.min(100,t/flow.duration*100)}%`:'100%';
}
function button(text,fn,primary=false){const b=document.createElement('button');b.textContent=text;b.type='button';b.className=primary?'primary':'';b.disabled=flow.paused;b.onclick=fn;return b;}
function renderUI(){
 const sig=flow.state+'|'+flow.paused+'|'+flow.captures+'|'+auto;
 if(sig!==signature){signature=sig;el('title').textContent=flow.title;el('screen-title').textContent=flow.title;el('detail').textContent=flow.detail;el('status').textContent=flow.paused?'已暂停':auto?'自动演示':'互动预览';el('pause').textContent=flow.paused?'继续':'暂停';el('pause').setAttribute('aria-label',flow.paused?'继续预览':'暂停预览');
  el('motion').textContent=flow.state==='wake'?'低头闭眼 → 试着睁眼 → 再次闭眼 → 抬眼醒来':flow.speaking?'文字播放 · 嘴巴随节奏张合':flow.state==='photo'?'拍照表情 · 只播放一次':flow.state==='daily'?'日常待机 · 不自动收音':'屏幕显示与当前流程同步';
  const a=el('actions');a.replaceChildren();
  if(flow.state==='scan'){a.append(button('模拟手机扫码',()=>flow.next(),true),button('自动演示全程',()=>{auto=true;flow.next();},false));}
  if(flow.state==='network')a.append(button('输入成功，连接网络',()=>flow.next(),true),button('模拟输入失败',()=>{auto=false;flow.fail();}));
  if(flow.state==='failed')a.append(button('返回输入密码',()=>flow.next(),true));
  if(flow.state==='chat')a.append(button('查看日常开机',()=>{auto=false;flow.enter('daily');}));
  if(auto)a.append(button('改为手动体验',()=>{auto=false;}));
  const ready=['name','consent','chat'].includes(flow.state)&&!flow.paused;el('input').disabled=!ready;el('send').disabled=!ready;el('input').placeholder=flow.state==='name'?'我叫小明':flow.state==='consent'?'可以 / 不拍照':flow.state==='chat'?'说说你喜欢做什么…':'等它说完，再回复…';
  const suggestions=el('suggestions');suggestions.replaceChildren();const quick=flow.state==='name'?['我叫小明']:flow.state==='consent'?['可以','不拍照']:flow.state==='chat'?['我喜欢旅行']:[];quick.forEach(text=>suggestions.append(button(text,()=>{auto=false;flow.input(text);})));
  el('photo').classList.toggle('visible',flow.captures>0);
  const index=['scan','network','failed','fade'].includes(flow.state)?0:flow.state==='wake'?1:['peek','left','right','hello','ask','name'].includes(flow.state)?2:['invite','consent','clarify','photo','photoDone','skip'].includes(flow.state)?3:4;
  document.querySelectorAll('.step').forEach((n,i)=>{n.className='step '+(i===index?'active':i<index?'done':'');});
 }
 const ms=flow.messages.length+'|'+flow.state+'|'+(flow.line?Math.floor(Math.max(0,flow.elapsed-350)/130):0);
 if(ms!==messageSignature){messageSignature=ms;const box=el('transcript'),nearBottom=box.scrollHeight-box.scrollTop-box.clientHeight<60;box.replaceChildren();
  if(!flow.messages.length){const empty=document.createElement('div');empty.className='empty';empty.innerHTML='<span>一段新的相遇</span>先完成连接。它醒来后，对话将在这里展开。';box.append(empty);}
  flow.messages.forEach((m,i)=>{const row=document.createElement('div');row.className='message '+(m.role==='user'?'user':'');const label=document.createElement('small');label.textContent=m.role==='user'?'你':'WATCHER';const b=document.createElement('div');b.className='bubble';const active=i===flow.messages.length-1&&m.role==='watcher'&&m.state===flow.state;b.textContent=active?m.text.slice(0,Math.max(1,Math.floor((flow.elapsed-350)/130))):m.text;row.append(label,b);box.append(row);});if(nearBottom||flow.elapsed<100)box.scrollTop=box.scrollHeight;
 }
}
function reset(state='scan'){auto=false;flow.reset();if(state!=='scan')flow.enter(state);smile=0;pan=0;tilt=state==='wake'?100:113;signature='';messageSignature='';el('input').value='';}
el('reset').onclick=()=>reset();el('pause').onclick=()=>{flow.paused=!flow.paused;};el('speed').onchange=e=>{speed=Number(e.target.value);};
el('form').onsubmit=e=>{e.preventDefault();auto=false;flow.input(el('input').value);el('input').value='';};
document.querySelectorAll('[data-jump]').forEach(b=>b.onclick=()=>reset(b.dataset.jump));
function frame(now){const dt=last?Math.min(100,now-last)*speed:0;last=now;flow.tick(dt);
 if(auto&&!flow.paused&&flow.elapsed>1600){if(flow.state==='network')flow.next();else if(flow.state==='name')flow.input('我叫小明');else if(flow.state==='consent')flow.input('可以');else if(flow.state==='chat')auto=false;}
 renderUI();draw(flow.paused?0:dt);requestAnimationFrame(frame);
}
document.addEventListener('visibilitychange',()=>{last=0;});
async function decode(src){const img=new Image();img.src=src;await img.decode();return img;}
async function start(){try{for(const [key,value]of Object.entries(ASSETS)){images[key]=Array.isArray(value)?await Promise.all(value.map(decode)):await decode(value);}el('souvenir').src=ASSETS.souvenir;el('loading').hidden=true;requestAnimationFrame(frame);}catch(e){el('loading').textContent='屏幕资源加载失败，请重新打开完整 HTML 文件。';console.error(e);}}
start();
