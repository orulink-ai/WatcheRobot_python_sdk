const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="meeting-token"]').content;
let state = {}, config = {}, photoName = '', photoUrl = '', lastConversation = '';
const phaseNames = {ready:'准备就绪',sleeping:'还在梦里…',waking:'慢慢醒来',looking_left:'看看左边',looking_right:'看看右边',synthesizing:'准备说话',speaking:'正在说话',listening:'正在聆听，请说话',recognizing:'正在识别你说的话',thinking:'想一想怎么回答',waiting_text:'等你输入一句话',capturing:'正在拍照',stopping:'正在停止…',stopped:'已停止',error:'需要检查'};
async function api(path, body){
  const response = await fetch('/api/'+path, {method:body === undefined?'GET':'POST',headers:{'X-Meeting-Token':token,'Content-Type':'application/json'},body:body === undefined?undefined:JSON.stringify(body)});
  const data = await response.json();
  if(!response.ok) throw new Error(data.error || '请求失败');
  return data;
}
let toastTimer;
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500)}
async function action(fn){try{await fn()}catch(e){toast(e.message)}}
function fillConfig(data){config=data;for(const field of $('settings').elements){if(!field.name)continue;if(field.type==='checkbox')field.checked=!!data[field.name];else{field.value=data[field.name]??'';if(field.type==='password')field.placeholder=data[field.name+'_configured']?'已配置 · 留空保留':'尚未配置'}}}
function render(s){
  state=s;$('version').textContent=s.sdk_version;$('phase').textContent=phaseNames[s.phase]||s.phase;
  const mic=s.microphone||{};
  const micNames={opening:'正在打开麦克风',waiting_speech:'正在收音，等待讲话',recording:'已检测到讲话',closing:'正在关闭麦克风',closed:'麦克风已关闭',error:'录音异常'};
  const fresh=mic.last_frame_at&&Date.now()/1000-mic.last_frame_at<3;
  $('microphone-status').textContent=(s.phase==='error'?'对话已中断 · ':s.phase==='recognizing'?'正在识别 · ':'')+(micNames[mic.state]||'麦克风未开启')+` · 收到 ${mic.frames||0} 帧`+(fresh?` · 音量 ${mic.current_rms||0} / 阈值 ${mic.threshold||0}`:'');
  const online=!!s.device.online;$('connection').textContent=online?'● 机器人已连接':'○ 控制台在线 · 机器人未连接';$('device-state').textContent=online?'已连接':'未连接';$('device-state').className=online?'ok':'';
  $('person').textContent=s.name?'你好，'+s.name:'还没认识你';
  $('caption').textContent=s.phase==='listening'?'我在听，慢慢说。':s.phase==='speaking'?'轮到我说啦。':s.phase==='capturing'?'谢谢你愿意让我认识你。':'给这个世界一个好奇的眼神。';
  const face=document.querySelector('.robot-face');face.className='robot-face'+(!['ready','sleeping','stopped'].includes(s.phase)?' awake':'')+(s.phase==='looking_left'?' left':s.phase==='looking_right'?' right':'');
  const checking=Object.values(s.checks).some(c=>['waiting','checking'].includes(c.state));
  $('start').disabled=s.running||checking||!online;$('chat').disabled=s.running||checking||!online;$('gaze').disabled=s.running||checking||!online;$('stop').disabled=!s.running;$('check').disabled=s.running||checking;
  $('settings').querySelector('button[type="submit"]').disabled=s.running||checking;
  $('send').disabled=!s.running||!['waiting_text','listening'].includes(s.phase);
  $('check-details').replaceChildren();
  for(const name of ['TTS','STT','LLM']){const c=s.checks[name];const e=$(name.toLowerCase()+'-state');e.textContent=c?({ok:'已通过',error:'需检查',waiting:'等待检测',checking:'检测中…'}[c.state]):'未检测';e.className=c?.state||'';if(c?.detail){const p=document.createElement('p');p.textContent=name+' · '+c.detail;$('check-details').append(p)}}
  const lines=s.events.filter(e=>e.kind==='user'||e.kind==='robot');const signature=JSON.stringify(lines);
  if(signature!==lastConversation){lastConversation=signature;$('conversation').replaceChildren();for(const e of lines){const bubble=document.createElement('div');bubble.className='bubble '+e.kind;const label=document.createElement('small');label.textContent=(e.kind==='user'?'YOU':'WATCHER')+' · '+e.time;bubble.append(label,document.createTextNode(e.text));$('conversation').append(bubble)}$('conversation').scrollTop=$('conversation').scrollHeight}
  const logs=s.events.map(e=>`${e.time}  [${e.kind}]  ${e.text}`).join('\n');if($('logs').textContent!==logs){$('logs').textContent=logs||'等待应用事件…';$('logs').scrollTop=$('logs').scrollHeight}
  $('animations').replaceChildren(...s.animations.map(a=>{const o=document.createElement('option');o.value=a;return o}));
  if(s.photo&&s.photo!==photoName){photoName=s.photo;fetch('/api/photo',{headers:{'X-Meeting-Token':token}}).then(r=>{if(!r.ok)throw Error('照片暂时不可用');return r.blob()}).then(blob=>{if(photoUrl)URL.revokeObjectURL(photoUrl);photoUrl=URL.createObjectURL(blob);$('photo').src=photoUrl;$('photo-wrap').hidden=false}).catch(e=>toast(e.message))}
}
$('start').onclick=()=>action(()=>api('start',{boot:true}));$('chat').onclick=()=>action(()=>api('start',{boot:false}));$('stop').onclick=()=>action(()=>api('stop',{}));$('check').onclick=()=>action(()=>api('check',{}));
$('gaze').onclick=()=>action(()=>api('start',{gaze_only:true}));
$('settings').onsubmit=e=>{e.preventDefault();action(async()=>{const body={};for(const f of $('settings').elements){if(!f.name)continue;body[f.name]=f.type==='checkbox'?f.checked:f.type==='number'?Number(f.value):f.value}fillConfig(await api('config',body));toast('配置已保存')})};
$('text-form').onsubmit=e=>{e.preventDefault();const text=$('message').value.trim();if(text)action(async()=>{await api('text',{text});$('message').value=''})};
$('pair-form').onsubmit=e=>{e.preventDefault();action(async()=>{await api('pair',{code:$('pair-code').value});$('pair-code').value='';toast('已发起配对，等待设备连接')})};
$('download').onclick=()=>{const blob=new Blob([$('logs').textContent],{type:'text/plain;charset=utf-8'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download='first-meeting.log';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
async function poll(){try{render(await api('status'))}catch(e){$('connection').textContent='控制台连接已断开';$('start').disabled=$('chat').disabled=$('send').disabled=true}finally{setTimeout(poll,1000)}}
action(async()=>{fillConfig(await api('config'));await poll()});
