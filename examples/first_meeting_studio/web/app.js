const $=id=>document.getElementById(id),token=document.querySelector('meta[name="meeting-token"]').content;let audioUrl, photoUrl, photoName = "";
async function api(path,body){const r=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:{'X-Meeting-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d}
async function action(fn){try{await fn()}catch(e){$('toast').hidden=false;$('toast').textContent=e.message;setTimeout(()=>$('toast').hidden=true,8000)}}
function editor(scenes){for(const scene of scenes){const card=document.createElement('fieldset');card.className='panel';for(const [key,value] of Object.entries(scene)){const label=document.createElement('label');label.textContent=({title:'片段',text:'台词',expression:'表情',pan:'转头 / 度',tilt:'抬头 / 度',duration_ms:'动作 / 毫秒',hold_seconds:'停留 / 秒',gesture:'小动作'})[key]||key;const f=document.createElement(['expression','gesture'].includes(key)?'select':'input');f.name=key;if(key==='gesture'){for(const [v,name] of Object.entries({none:'无附加动作',peek:'好奇探头',nod:'轻轻点头',settle:'停顿回正'})){const o=document.createElement('option');o.value=v;o.textContent=name;f.append(o)}}else if(key==='expression')for(const v of ['sleepy','curious','warm','wink','thinking','speaking','listening','photo','sing','interference','error','neutral','smile','happy','anger','concentration','sad','speechless']){const o=document.createElement('option');o.value=v;o.textContent=({sleepy:'待机 · 梦醒',curious:'待机 · 好奇',warm:'微笑 · 温暖',wink:'墨镜 · 小调皮',thinking:'处理 · 思考',speaking:'平静 · 实际语音嘴型',listening:'待机 · 聆听',photo:'拍照 · 新版完整动画',sing:'唱歌',interference:'干扰',error:'错误',neutral:'平静',smile:'微笑',happy:'开心',anger:'生气',concentration:'专注',sad:'难过',speechless:'无语'})[v];f.append(o)}else if(typeof value==='number'){f.type='number';f.step='any'}f.value=value;label.append(f);card.append(label)}$('scenes').append(card)}}
$('take-photo').onclick=()=>action(()=>api('photo',{}));
$('start').onclick=()=>action(()=>api('start',{boot:true}));$('rehearse').onclick=()=>action(()=>api('start',{boot:true,rehearsal:true}));$('stop').onclick=()=>action(()=>api('stop',{}));$('check').onclick=()=>action(()=>api('check',{}));
function showCredentials(c){
 for(const name of ['tts_token','stt_token','speech_api_key']){const f=$('settings').elements.namedItem(name);f.value='';f.placeholder=c[name+'_configured']?'已保存，留空保留；填写可替换':'尚未配置';}
 $('credential-status').textContent='TTS '+((c.speech_api_key_configured||c.tts_token_configured)?'已保存':'未配置')+' · STT '+((c.speech_api_key_configured||c.stt_token_configured)?'已保存':'未配置')+'；请检测服务确认授权。';
}
$('settings').onsubmit=e=>{e.preventDefault();action(async()=>{const c=await api('config',Object.fromEntries([...$('settings').elements].filter(f=>f.name).map(f=>[f.name,f.type==='checkbox'?f.checked:f.type==='number'?Number(f.value):f.value])));showCredentials(c);})};
$('clear-credentials').onclick=()=>action(async()=>{if(!confirm('清除本机语音凭据？之后需重新填写。'))return;const c=await api('config/clear-credentials',{});showCredentials(c);for(const name of ['tts_app_id','stt_app_id'])$('settings').elements.namedItem(name).value='';});
$('story').onsubmit=e=>{e.preventDefault();action(()=>api('config',{scenes:[...$('scenes').children].map(card=>Object.fromEntries([...card.querySelectorAll('input,select')].map(f=>[f.name,f.type==='number'?Number(f.value):f.value])))}))};
$('text-form').onsubmit=e=>{e.preventDefault();action(async()=>{await api('text',{text:$('message').value});$('message').value=''})};$('pair-form').onsubmit=e=>{e.preventDefault();action(()=>api('pair',{code:$('pair-code').value}))};
$('audition').onclick=()=>action(async()=>{const r=await fetch('/api/audition',{method:'POST',headers:{'X-Meeting-Token':token}});if(!r.ok)throw Error((await r.json()).error);if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=URL.createObjectURL(await r.blob());$('audio').src=audioUrl;$('audio').hidden=false;await $('audio').play()});
async function updatePhoto(name){
 if(name===photoName)return;
 if(!name){if(photoUrl)URL.revokeObjectURL(photoUrl);photoUrl=undefined;photoName="";$('photo-preview').hidden=true;$('photo-download').hidden=true;return}
 const r=await fetch('/api/photo',{headers:{'X-Meeting-Token':token}});
 if(!r.ok){$('photo-hint').textContent='照片暂未返回，正在等待';return}
 const next=URL.createObjectURL(await r.blob());
 if(photoUrl)URL.revokeObjectURL(photoUrl);photoUrl=next;photoName=name;
 $('photo-preview').src=next;$('photo-preview').hidden=false;$('photo-download').href=next;$('photo-download').hidden=false;
 $('photo-hint').textContent='这张照片由 Watcher 实际拍摄，只保存在本机。';
}
window.addEventListener('pagehide',()=>{if(photoUrl)URL.revokeObjectURL(photoUrl);if(audioUrl)URL.revokeObjectURL(audioUrl)});
let onboardingState, advancing=false;
const onboardingCopy={
 preparing:['让我先找到你。','正在准备 Watcher 屏幕，请稍等。',''],
 scan:['我们的故事，从找到彼此开始。','Watcher 正显示演示二维码。无需手机或密码，点击下方按钮模拟扫码。','模拟扫码成功 →'],
 network:['给我们的相遇，搭一座小桥。','Watcher 正显示输入密码页。模拟手机提交成功后，画面渐隐并自动苏醒；无需实际输入密码。','模拟输入完成 · 配网成功 →'],
 failed:['没关系，我们再试一次。','Watcher 正显示配网失败页。请返回手机输入密码页，再模拟输入成功或失败。','重新输入密码 →'],
 awakening:['连接好了，它正在醒来。','梦慢慢散去，眼睛一点点睁开。给它一点时间认认这个世界。',''],
 complete:['啊，终于见到你了。','连接仪式完成，接下来听它自我介绍，也告诉它你的名字。',''],
 interrupted:['先在这里歇一会儿。','流程已停止。重新排练会从发现彼此开始。',''],
 error:['这一小步还没走稳。','请查看下方错误详情，恢复设备连接后重新开始。','']
};
function renderOnboarding(s){
 onboardingState=s.onboarding;const o=onboardingState;
 $('onboarding').hidden=!o||['idle','skipped'].includes(o.stage);if(!o)return;
 const c=onboardingCopy[o.stage]||onboardingCopy.preparing;
 $('onboarding').dataset.stage=o.stage;
 $('onboarding-title').textContent=c[0];$('onboarding-description').textContent=c[1];
 $('onboarding-next').hidden=!c[2];$('onboarding-next').textContent=c[2];
 $('onboarding-next').disabled=advancing||!o.can_advance||!s.running||!s.device.online;
 $('onboarding-fail').hidden=o.stage!=='network';
 for(const id of ['onboarding-fail'])$(id).disabled=$('onboarding-next').disabled;
 $('onboarding-feedback').textContent=!s.device.online?'真实设备尚未连接':advancing?'正在确认这一步…':'';
 const preview=$('provisioning-preview'),hasPreview=['scan','network','failed'].includes(o.stage);
 preview.hidden=!hasPreview;
 if(hasPreview&&preview.dataset.stage!==o.stage){preview.src='/provisioning/'+o.stage+'.png';preview.dataset.stage=o.stage;}
 const stages=['scan','network','awakening','complete'];
 const progressStage=o.stage==='failed'?'network':o.stage;
 for(const el of document.querySelectorAll('.onboarding-steps li')){el.classList.toggle('current',el.dataset.stage===progressStage);el.classList.toggle('done',stages.indexOf(el.dataset.stage)<stages.indexOf(progressStage));}
}
async function advanceOnboarding(event){
 if(advancing||!onboardingState?.can_advance)return;
 const ticket={run_id:onboardingState.run_id,stage:onboardingState.stage,revision:onboardingState.revision,action:event};
 advancing=true;
 for(const id of ['onboarding-next','onboarding-fail'])$(id).disabled=true;
 try{await api('onboarding/next',ticket);}finally{advancing=false;}
 renderOnboarding(await api('status'));
}
$('onboarding-next').onclick=()=>action(()=>advanceOnboarding(onboardingState?.stage==='failed'?'password':'next'));
$('onboarding-fail').onclick=()=>action(()=>advanceOnboarding('fail'));
async function poll(){try{const s=await api('status');renderOnboarding(s);await updatePhoto(s.photo);$('connection').textContent=s.device.online?'● 已连接':'○ 等待设备';$('phase').textContent=s.last_error ? s.phase+' · '+s.last_error : s.phase;$('memory').textContent=s.rehearsal?'排练不记录初见完成':s.completed?'已经认识你，再次启动跳过初见':'尚未完成初见';$('health').replaceChildren();for(const [n,c] of Object.entries(s.checks)){const p=document.createElement('p');p.textContent=n+' · '+c.state+' '+(c.detail||'');$('health').append(p)}$('logs').textContent=JSON.stringify(s.events,null,2);$('conversation').replaceChildren();for(const e of s.events.filter(e=>['robot','user'].includes(e.kind))){const p=document.createElement('p');p.className='bubble '+e.kind;p.textContent=e.text;$('conversation').append(p)}const busy=s.running||Object.values(s.checks).some(c=>['waiting','checking'].includes(c.state));for(const id of ['start','rehearse','connection-probe','check','audition','take-photo'])$(id).disabled=busy;$('text-form').querySelector('button').disabled=!s.running||!['listening','waiting_text'].includes(s.phase)}catch(e){$('connection').textContent='控制台连接中断'}finally{setTimeout(poll,1000)}}
action(async()=>{const c=await api('config');for(const f of $('settings').elements)if(f.name){if(f.type==='checkbox')f.checked=c[f.name];else f.value=c[f.name]}showCredentials(c);editor(c.scenes);await poll()});

$('connection-probe').onclick=()=>action(()=>api('start',{connection_probe:true,rehearsal:true}));