import {PreparedCache, Lookahead} from './buffer.mjs';
import {IdlePlayback} from './playback.mjs';
import {tableRows, updateCell, setProbability, normalizeGroups} from './table-state.mjs';
import {officialDefaults} from './defaults.mjs';
import {percentages, resourceOptions, readSession, saveRequest, confirmSave, PreviewSelection} from './state.mjs';
const $ = id => document.getElementById(id);
let session=null, demo=false, busy=false;
const options=key=>demo?samples.map(([id,name])=>({id,name})):resourceOptions(session?.catalog,key);
let rows=[], baseline=[], selected=null, dirty=false;
let baselinePools=null, baselineSchema=1;
const draftSignature=()=>JSON.stringify([rows,session?.pool_percentages]);
const samples=[['blink','轻轻眨眼',30],['look','左右看看',25],['smile','偷偷开心',20],['stretch','伸个懒腰',15],['quiet','安静陪伴',10]];
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const current=()=>rows.find(r=>r.id===selected);
const previewSelection = new PreviewSelection();
let modelViewer=null, modelPromise=null;
let playbackRun=null;
let previewBehavior=null;
let previewStopped=false;
const companionCache=new Map();
async function companion(resource,kind){
 if(!resource)return null;
 const params=new URLSearchParams({resource,kind});
 const digest=session?.catalog?.expressions?.find(e=>e.id===resource)?.assets?.[kind]?.sha256;
 if(digest)params.set('sha256',digest);
 const key=params.toString();
 if(!companionCache.has(key)){
  const response=await fetch('/api/official/companion?'+key);
  if(!response.ok){const error=await response.json();throw Error(error.detail||'组合素材读取失败');}
  companionCache.set(key,kind==='sound'?await response.arrayBuffer():await response.json());
 }
 return companionCache.get(key);
}
function playbackControls(){
 $('play-all').disabled=busy||demo||!session||!modelViewer||!rows.some(r=>r.weight>0)||!!playbackRun;
 $('stop-playback').disabled=previewStopped||(!playbackRun&&!previewSelection.loading&&!modelViewer?.sheet);
}
function stopPlayback(message='已停止预览。点击预览此行为或播放全部行为重新开始。'){
 previewStopped=true;
 playbackRun=null;previewBehavior=null;modelViewer?.clear();
 previewSelection.token={};previewSelection.loading=false;previewSelection.url=null;
 previewSelection.error='';previewSelection.metadata=null;
 $('playback-status').textContent=message;renderPreview();playbackControls();
}
const preparedCache=new PreparedCache();
async function prepareBehavior(row){
 const catalog=session?.catalog;
 const key=JSON.stringify([catalog,row.expression,row.motion,row.sound]);
 return preparedCache.get(key,async()=>{
  const params=new URLSearchParams({resource:row.expression});
  const digest=catalog?.expressions?.find(e=>e.id===row.expression)?.assets?.animation?.sha256;
  if(digest)params.set('sha256',digest);
  const [response,action,sound]=await Promise.all([fetch('/api/official/preview?'+params),companion(row.motion,'action'),companion(row.sound,'sound')]);
  const data=await response.json();if(!response.ok)throw Error(data.detail||'官方动画加载失败');
  return modelViewer.prepare(data,action,sound);
 });
}
async function playNext(run){
 if(playbackRun!==run)return;
 try{
  const signature=draftSignature();
  run.queue.scheduler.pools=session?.pool_percentages||null;
  if(!modelViewer.sheet)$('playback-status').textContent='正在准备首个行为…';
  const {row,prepared}=await run.queue.take(rows,prepareBehavior,()=>playbackRun===run&&signature===draftSignature());
  if(playbackRun!==run)return;
  if(signature!==draftSignature()){playNext(run);return;}
  if(!row){stopPlayback('没有可播放的行为，请先设置概率。');return;}
  // Commit all three tracks together only after preparation has finished.
  previewSelection.select(session,row.expression,row.name);
  previewSelection.token={};previewSelection.loading=false;previewSelection.error='';
  previewSelection.url=prepared.data.url;previewSelection.metadata=prepared.data;
  previewBehavior=row;modelViewer.start(prepared,true);
  $('playback-status').textContent='正在播放 · '+(groupNames[row.group]||'待机')+' · '+row.name;
  renderPreview();playbackControls();
  modelViewer.onComplete=()=>{if(playbackRun===run)playNext(run);};
  run.queue.prime(rows,prepareBehavior);
 }catch(error){if(playbackRun===run)stopPlayback('连播已停止：'+error.message);}
}
$('play-all').onclick=async()=>{
 if($('play-all').disabled)return;
 const run={queue:new Lookahead(new IdlePlayback(session?.pool_percentages))};playbackRun=run;previewStopped=false;
 previewSelection.token={};previewSelection.loading=false;previewSelection.error='';
 modelViewer.serial++;renderPreview();
 playbackControls();
 try{await modelViewer.unlockAudio();if(playbackRun===run)playNext(run);}
 catch(error){if(playbackRun===run)stopPlayback('无法启用声音：'+error.message);}
};
$('stop-playback').onclick=()=>stopPlayback();
async function getModel(){
 if(!modelPromise)modelPromise=import('./model-preview.mjs').then(({ModelPreview})=>{modelViewer=new ModelPreview($('model-stage'));playbackControls();return modelViewer;}).catch(e=>{modelPromise=null;throw e;});
 return modelPromise;
}
function selectPreview(resource,name,fromPlayback=false){
 if(!fromPlayback)stopPlayback('选择行为可预览组合；播放全部行为可体验随机待机。');
 previewStopped=false;
 previewBehavior=null;
 const before=previewSelection.token;
 previewSelection.select(session,resource,name);
 if(before!==previewSelection.token){modelViewer?.clear();previewSelection.metadata=null;}
}
function renderPreview(){
 const p=previewSelection;
 $('stage-label').textContent=p.name||'三维表情预览';
 $('preview').replaceChildren();
 if(demo){modelViewer?.clear();return;}
 if(p.loading)$('preview').append(el('div','正在准备官方动画…','empty-screen'));
 if(p.error)$('preview').append(previewButton(p.resource,p.name));
 $('preview-caption').textContent=p.loading?'首次同步与解码需要稍等，之后使用本机缓存。':p.error||(p.metadata?('官方 '+p.metadata.version+' · '+(p.metadata.matched_by==='sha256'?'内容校验匹配':'按名称匹配，未校验设备内容')):'选择行为，预览当前草稿的表情。');
 if(!previewStopped&&!playbackRun&&p.resource&&!p.loading&&!p.url&&!p.error)loadOfficialPreview();
}
async function loadOfficialPreview(){
 const p=previewSelection,token=p.token,resource=p.resource,behavior=previewBehavior;
 if(!resource||demo)return;
 previewStopped=false;p.loading=true;p.error='';playbackControls();renderPreview();
 try{
  const params=new URLSearchParams({resource});
  const digest=session?.catalog?.expressions?.find(e=>e.id===resource)?.assets?.animation?.sha256;
  if(digest)params.set('sha256',digest);
  const response=await fetch('/api/official/preview?'+params);
  const data=await response.json();
  if(!response.ok)throw Error(data.detail||'官方动画加载失败');
  if(p.token!==token)return;
  const viewer=await getModel();
  if(p.token!==token)return;
  const [action,sound]=behavior?await Promise.all([companion(behavior.motion,'action'),companion(behavior.sound,'sound')]):[null,null];
  if(p.token!==token)return;
  if(sound)await viewer.unlockAudio();
  if(p.token!==token)return;
  await viewer.play(data,!!playbackRun||!!behavior,action,sound);
  if(p.token===token){p.accept(token,data.url);p.metadata=data;return data;}
 }catch(error){if(p.token===token)p.error=error.message;}
 finally{if(p.token===token){p.loading=false;playbackControls();renderPreview();}}
}
function previewButton(){
 const button=el('button','重试官方预览','preview-load');button.type='button';
 button.onclick=()=>loadOfficialPreview();return button;
}
function ask(message){
 return new Promise(resolve=>{
  const dialog=$('confirm-action');$('confirm-message').textContent=message;
  dialog.returnValue='cancel';dialog.onclose=()=>resolve(dialog.returnValue==='confirm');dialog.showModal();
 });
}
function notice(text){$('notice').hidden=!text;$('notice').textContent=text;}
let filter='all';
function defaultPools(items){
 const standby=items.some(r=>r.group==='standby'&&r.weight>0),feature=items.some(r=>r.group==='feature'&&r.weight>0);
 return {standby:standby?(feature?50:100):0,feature:feature?(standby?50:100):0};
}
function poolError(){
 if(session?.schema!==3)return '';
 for(const [group,share] of Object.entries(session.pool_percentages||{})){
  if(share>0&&!rows.some(r=>r.group===group&&r.weight>0))return (group==='standby'?'日常待机':'趣味表现')+'暂无启用行为，请添加行为或将该组设为 0%。';
 }
 return '';
}
function renderPools(){
 const enabled=session?.schema===3;
 $('pool-settings').hidden=!enabled;
 if(!enabled)return;
 const pools=session.pool_percentages;
 for(const group of ['standby','feature']){$('pool-'+group).value=pools[group];$('pool-'+group).disabled=busy;}
 $('pool-slider').value=pools.standby;$('pool-slider').disabled=busy;
 $('pool-message').textContent=poolError()||'先抽取分组，再按组内概率选择行为。0% 表示暂停该组；修改后保存到 Watcher 生效。';
 $('pool-message').classList.toggle('pool-error',!!poolError());
}
function changePool(group,value){
 if(busy||session?.schema!==3)return;
 const number=Number(value);
 if(value===''||!Number.isInteger(number)||number<0||number>100){notice('分组概率请输入 0–100 的整数。');renderPools();return;}
 session.pool_percentages={standby:group==='standby'?number:100-number,feature:group==='feature'?number:100-number};
 if(playbackRun)playbackRun.queue.scheduler.pools=session.pool_percentages;
 render();
}
for(const group of ['standby','feature'])$('pool-'+group).onchange=e=>changePool(group,e.target.value);
$('pool-slider').oninput=e=>changePool('standby',e.target.value);
const groupNames={standby:'日常待机',feature:'趣味表现'};
function showPreview(){renderPreview();}
function render(){
 const grouped=session?.schema>=2,editable=demo||session?.editable;
 $('count').textContent=rows.length;$('paused').textContent=rows.filter(r=>r.weight===0).length;
 $('flow').textContent=session?.schema===3?'60 秒无操作 → 按分组概率抽取 → 按组内概率选择行为':grouped?'60 秒无操作 → 日常待机 ⇄ 趣味表现 · 组间轮换':'待机行为 · 按概率随机抽取';
 document.querySelectorAll('[data-filter]').forEach(b=>{b.hidden=!grouped&&b.dataset.filter!=='all';b.setAttribute('aria-pressed',String(b.dataset.filter===filter));});
 $('behavior-list').replaceChildren();
 const visible=tableRows(rows,baseline,grouped?filter:'all',$('search').value,session?.catalog);
 for(const group of grouped?['standby','feature']:[null]){
  const items=visible.filter(item=>!group||item.row.group===group);if(!items.length)continue;
  if(group){const tr=el('tr',undefined,'group-row'),td=el('td',groupNames[group]+' · '+items.length+' 项');td.colSpan=8;tr.append(td);$('behavior-list').append(tr);}
  for(const {row:r,index,share,changed} of items){
   const tr=el('tr',undefined,'behavior-row'+(selected===r.id?' selected':'')+(changed?' changed':''));
   tr.dataset.id=r.id;const number=el('td',String(index+1).padStart(2,'0'));number.title=changed?'此行为有未保存修改':'';tr.append(number);
   tr.onfocusin=()=>{selected=r.id;document.querySelectorAll('.behavior-row').forEach(t=>t.classList.toggle('selected',t===tr));};
   for(const key of ['name','expression','motion','sound','weight']){
    const td=el('td');let control;
    if(key==='name'||key==='weight'){control=el('input');control.type=key==='weight'?'number':'text';control.value=r[key];if(key==='weight'){control.min=0;control.max=1000000;control.step=1;}else control.maxLength=32;}
    else{control=el('select');if(key!=='expression'){const opt=el('option','无');opt.value='';control.append(opt);}
     const choices=options(key);if(r[key]&&!choices.some(o=>o.id===r[key]))choices.unshift({id:r[key],name:r[key]+'（素材缺失）'});
     for(const item of choices){const opt=el('option',item.name);opt.value=item.id;control.append(opt);}control.value=r[key]||'';}
    control.dataset.key=key;control.setAttribute('aria-label',r.name+' '+({name:'名称',expression:'表情',motion:'动作',sound:'声音',weight:'权重'}[key]));control.disabled=busy||!editable;
    if(key==='weight'){control.value=share;control.min=1;control.max=100;control.setAttribute('aria-label',r.name+' 概率（%）');}
    control.onchange=()=>{if(busy)return;if(!(key==='weight'?setProbability(rows,r.id,control.value):updateCell(r,key,control.value))){control.value=key==='weight'?percentages(rows)[index]:r[key];notice('请输入 1–100 的整数百分比；组内只有一个行为时须为 100%。名称不能为空。');return;}
     if(key==='weight')notice('已调整组内概率，其他行为自动分配剩余比例；0% 的行为暂停播放。保存后生效。');
     if(['expression','motion','sound','name'].includes(key)&&!playbackRun){selectPreview(r.expression,r.name);previewBehavior=r;previewSelection.token={};previewSelection.url=null;previewSelection.loading=false;}
     refreshTable();renderPreview();};td.append(control);tr.append(td);
   }
   const shareCell=el('td',undefined,'share-cell');shareCell.dataset.share=r.id;const bar=el('span',undefined,'share-bar');bar.append(el('i'));shareCell.append(bar,el('strong',share+'%'));tr.append(shareCell);
   const actions=el('td',undefined,'row-actions');
   const preview=el('button','▶ 预览此行为','row-preview');preview.title='在右侧三维模型预览此行为';preview.setAttribute('aria-label','预览 '+r.name);preview.disabled=busy||demo;
   preview.onclick=async()=>{
    selected=r.id;selectPreview(r.expression,r.name);previewBehavior=r;
    const token={};previewSelection.token=token;previewSelection.url=null;previewSelection.error='';
    previewSelection.loading=true;renderPreview();playbackControls();
    try{
     const viewer=await getModel();if(previewSelection.token!==token)return;
     await viewer.unlockAudio();if(previewSelection.token!==token)return;
     previewSelection.loading=false;
     $('playback-status').textContent='预览此行为 · '+r.name;showPreview();render();
    }catch(error){
     if(previewSelection.token!==token)return;
     previewSelection.loading=false;previewSelection.error=error.message;renderPreview();playbackControls();
    }
   };actions.append(preview);
   if(grouped){const move=el('button','⇄');move.title='移至'+groupNames[r.group==='standby'?'feature':'standby'];move.setAttribute('aria-label',move.title+' '+r.name);move.disabled=busy||!editable;move.onclick=()=>{const oldGroup=r.group;r.group=r.group==='standby'?'feature':'standby';normalizeGroups(rows,[oldGroup,r.group]);notice('已调整分组，并重新分配相关组内的整数概率。保存后生效。');render();};actions.append(move);}
   const remove=el('button','×','danger');remove.setAttribute('aria-label','删除 '+r.name);remove.title='移除此行为，保留素材';remove.disabled=busy||!editable||rows.length<2;remove.onclick=async()=>{if(!await ask('移除「'+r.name+'」？素材文件会保留。'))return;rows=rows.filter(item=>item.id!==r.id);selected=rows[0]?.id;normalizeGroups(rows,[r.group]);selectPreview(current()?.expression,current()?.name);notice('已移除行为草稿，素材保留；同组概率已重新分配。保存后生效。');render();};actions.append(remove);tr.append(actions);$('behavior-list').append(tr);
  }
 }
 $('empty-list').hidden=visible.length>0;$('empty-list').textContent=rows.length?'没有匹配的行为':session?(session.editable?'暂无行为，点击添加行为开始。':'设备素材已读取；当前固件不支持行为配置。'):'连接 Watcher，读取这台机器自己的待机行为。';
 refreshTable();
 if(demo){$('stage-label').textContent=current()?.name||'演示预览';$('preview').replaceChildren(el('div','◡ ◡','demo-graphic'));$('preview-caption').textContent='演示占位 · 非设备动画';}else renderPreview();
 $('stage-kind').textContent=demo?'示例数据':'官方动画 · 三维圆屏';
}
function refreshTable(){
 playbackControls();
 const shares=percentages(rows);
 document.querySelectorAll('.behavior-row').forEach(tr=>{
  const index=rows.findIndex(r=>r.id===tr.dataset.id), row=rows[index];
  tr.querySelector('[data-key=weight]').value=shares[index];
  tr.querySelector('[data-key=name]').value=row.name;
  const labels={name:'名称',expression:'表情',motion:'动作',sound:'声音',weight:'概率（%）'};
  tr.querySelectorAll('[data-key]').forEach(control=>control.setAttribute('aria-label',row.name+' '+labels[control.dataset.key]));
  const buttons=tr.querySelectorAll('.row-actions button');
  buttons[0].setAttribute('aria-label','预览 '+row.name);
  buttons[buttons.length-1].setAttribute('aria-label','删除 '+row.name);
  if(buttons.length===3)buttons[1].setAttribute('aria-label',buttons[1].title+' '+row.name);
 });
 document.querySelectorAll('[data-share]').forEach(cell=>{const i=rows.findIndex(r=>r.id===cell.dataset.share);cell.querySelector('strong').textContent=shares[i]===0?'已暂停':shares[i]+'%';cell.querySelector('i').style.width=shares[i]+'%';cell.closest('tr').classList.toggle('changed',JSON.stringify(rows[i])!==JSON.stringify(baseline.find(r=>r.id===rows[i].id)));});
 dirty=session?.pendingPoolMigration===true||JSON.stringify(rows)!==JSON.stringify(baseline)||JSON.stringify(session?.pool_percentages||null)!==JSON.stringify(baselinePools);
 renderPools();
 $('paused').textContent=rows.filter(r=>!r.weight).length;
 $('save-state').textContent=busy?'正在与设备通信…':demo?(dirty?'演示草稿 · 未保存':'演示模式'):session?.editable?(dirty?'有未保存修改':'设备配置已确认 · 版本 '+session.revision):'尚未读取行为配置';
 $('save-dot').classList.toggle('dirty',dirty);
 $('add').disabled=busy||!(demo||session?.editable)||rows.length>=24||!options('expression').length;
 $('save').disabled=busy||demo||!session?.editable||!dirty||!rows.some(r=>r.weight>0)||!!poolError();
 $('connect').disabled=busy;$('demo').disabled=busy;$('undo').disabled=busy||!dirty;
 $('restore-defaults').disabled=busy||demo||!session?.editable||session.schema<2;
}
 $('restore-defaults').onclick=async()=>{
  if(busy||demo||!session?.editable)return;
  const target=session;
  let defaults;
  try{defaults=officialDefaults(target);}catch(error){notice(error.message);return;}
  if(!await ask('恢复官方默认待机结构和概率？将替换当前草稿，按设备现有官方素材恢复 '+defaults.length+' 个行为及对应动作、声音；导入的素材文件会保留。确认后需点击“保存到 Watcher”才生效，保存前可撤销。'))return;
  if(busy||session!==target)return;
  rows=defaults;if(session.schema===3)session.pool_percentages=defaultPools(rows);selected=rows[0].id;filter='all';$('search').value='';
  selectPreview(current().expression,current().name);
  notice('已恢复官方默认草稿：组内等概率，支持分组概率时两组各 50%（空组除外）。请检查后保存到 Watcher；素材文件保持不变。');
  render();
 };
for(const button of document.querySelectorAll('[data-filter]'))button.onclick=()=>{filter=button.dataset.filter;render();};

$('demo').onclick=async()=>{if(dirty&&!await ask('放弃当前草稿并进入演示？'))return;demo=true;session=null;baselinePools=null;filter='all';$('search').value='';$('catalog-host').replaceChildren();$('asset-count').textContent='0';selectPreview(null,null);$('device-catalog')?.remove();rows=samples.map(([id,name,weight],i)=>({id,name,weight,expression:id,motion:i%2?id:null,sound:i===2?id:null}));baseline=structuredClone(rows);selected=rows[0].id;$('device-name').textContent='交互演示';$('device-meta').textContent='示例数据 · 未连接机器';$('add').disabled=false;notice('演示模式：可体验组合与概率编辑，示例不会写入 Watcher。');render();};
$('search').oninput=render;
$('add').onclick=()=>{if(busy||!(demo||session?.editable)||!options('expression').length)return;if(rows.length>=24){notice('最多支持 24 个行为');return;}const id='b_'+crypto.randomUUID().replaceAll('-','').slice(0,16);rows.push({...((session?.schema>=2)?{group:filter!=='all'?filter:(current()?.group||'standby')}:{}),id,name:'新的小习惯',expression:options('expression')[0].id,motion:null,sound:null,weight:10});selected=id;setProbability(rows,id,rows.filter(r=>r.group===current().group).length===1?100:10);notice('已添加行为；同组其他行为自动分配剩余比例。保存后生效。');selectPreview(current().expression,current().name);$('search').value='';filter='all';render();const input=document.querySelector('[data-id="'+id+'"] input');input?.focus();input?.select();};
$('undo').onclick=async()=>{if(!await ask('撤销所有未保存修改？'))return;rows=structuredClone(baseline);if(session){session.pool_percentages=structuredClone(baselinePools);session.schema=baselineSchema;}selected=rows[0]?.id;selectPreview(current()?.expression,current()?.name);notice('已撤销未保存修改。');render();};
$('connect').onclick=async()=>{if(dirty&&!await ask('连接设备将放弃当前草稿，继续？'))return;$('connection').showModal();$('connect-status').textContent='正在查找 USB 设备…';$('read').disabled=true;try{const response=await fetch('/api/ports');if(!response.ok)throw Error('无法读取端口');const ports=await response.json();$('ports').replaceChildren();ports.forEach(p=>{const option=el('option',p.label+' · '+p.port);option.value=p.port;$('ports').append(option);});$('read').disabled=!ports.length;$('connect-status').textContent=ports.length?'请选择 Watcher 对应的 USB 端口。':'未发现 USB 设备，请检查数据线。';}catch(e){$('connect-status').textContent=e.message;}};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
render();

// Present the catalog independently of configuration support. Old firmware can
// still expose its real resources without pretending to expose idle behaviors.
$('read').onclick = async () => {
 if(busy)return;
 stopPlayback();busy=true;render();
 $('ports').disabled=true;
 $('connection').querySelector('.dialog-close').disabled=true;
 $('read').disabled=true;
 $('connect-status').textContent='正在读取设备，请保持连接…';
 try {
  const response = await fetch('/api/device?port='+encodeURIComponent($('ports').value));
  const data = await response.json();
  if (!response.ok) throw Error(data.detail || '素材读取失败');
  session = readSession(data);filter='all';$('search').value='';
  demo = false;
  rows=session.rows;baseline=structuredClone(rows);baselinePools=structuredClone(session.pool_percentages);baselineSchema=session.schema;selected=rows[0]?.id;selectPreview(current()?.expression,current()?.name);
  $('add').disabled=true;
  $('device-name').textContent='Watcher · '+data.port;
  $('device-meta').textContent='固件 '+(data.device.firmware_version||'未知');
  const migrated = data.profile?.schema === 2 && session.schema === 3;
  notice(migrated ? '分组概率已默认开启；保存到 Watcher 后生效。' : data.message);$('connection').close();render();
  if(!rows.length){$('stage-kind').textContent=session.editable?'尚未创建行为':'配置接口待升级';
  $('preview').replaceChildren(el('div','设备素材目录已读取','empty-screen'));
  $('preview-caption').textContent='选择下方素材后，点击读取动画预览。';
  $('save-state').textContent=session.editable?'设备尚无已保存配置':'素材已读取 · 配置接口待升级';
  $('stage-label').textContent = '设备表情素材';}
  renderCatalog();
 } catch (error) { $('connect-status').textContent=error.message; } finally { busy=false;render();$('ports').disabled=false;$('connection').querySelector('.dialog-close').disabled=false;$('read').disabled=false; }
};

function renderCatalog() {
  const catalog = document.createElement('section');
  catalog.id = 'device-catalog';
  catalog.className = 'device-catalog';
  catalog.append(el('h2', '这台 Watcher 的表情 · '+options('expression').length));
  const upload=el('button','＋ 导入表情','import-expression');
  upload.disabled=!session?.editable;
  const file=el('input');file.type='file';file.accept='image/gif,image/png,image/jpeg,image/webp';file.hidden=true;
  upload.onclick=()=>{if(!busy)file.click();};
  file.onchange=()=>{const selectedFile=file.files[0];file.value='';if(selectedFile)importExpression(selectedFile);};
  catalog.append(upload,file);
  for (const item of options('expression')) {
   const button = el('button', item.name);
   button.title = item.id;
   button.onclick = () => {
    if(busy)return;
    selectPreview(item.id,item.name);showPreview(true);renderPreview();
   };
   catalog.append(button);
  }
  $('device-catalog')?.remove();
  $('catalog-host').replaceChildren(catalog);$('asset-count').textContent=options('expression').length;
}

async function importExpression(file) {
 if(busy||!session?.editable)return;
 if(file.size>2*1024*1024){notice('表情文件不能超过 2 MB，动画最多 120 帧。');return;}
 busy=true;render();
 const target=session, requestId=crypto.randomUUID();
 let polling=true;
 const poll=async()=>{
  try{const response=await fetch('/api/preview-progress?request_id='+requestId);
   if(response.ok&&polling){const p=await response.json();
    if(p.phase==='uploading')notice(`正在导入表情 ${Math.floor(p.received/p.total*100)}% · 请保持 USB 连接…`);
    else if(['installing','activating'].includes(p.phase))notice('上传完成，设备正在校验并加载素材…');
   }
  }catch{}
  if(polling)setTimeout(poll,750);
 };
 notice('正在准备表情并连接设备，请保持 USB 连接…');setTimeout(poll,750);
 try{
  const dataUrl=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('文件读取失败'));reader.readAsDataURL(file);});
  const response=await fetch('/api/import-expression',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
   port:target.port,device_id:target.device_id,revision:target.revision,request_id:requestId,
   file_name:file.name,name:file.name.replace(/\.[^.]+$/,''),data_url:dataUrl})});
  const result=await response.json();
  if(!response.ok)throw Error(result.detail||'导入失败');
  if(result.device_id!==target.device_id)throw Error('设备已变化，请重新连接');
  session.catalog=result.catalog;selectPreview(result.resource,file.name);renderCatalog();
  notice('表情已保存在这台 Watcher。可在表格的表情列选择，再保存行为组合。');
 }catch(error){notice('未确认导入成功：'+error.message);}
 finally{polling=false;busy=false;render();}
}

$('connection').addEventListener('cancel',event=>{if(busy)event.preventDefault();});


$('save').onclick = async () => {
 if(busy || demo || !session?.editable || !dirty)return;
 busy=true;render();notice('正在写入设备并回读校验，请保持 USB 连接…');
 try {
  const request=saveRequest(session,rows);
  const response=await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
  const result=await response.json();
  if(!response.ok)throw Error(result.detail||'保存失败');
  confirmSave(request,result);
  session.pendingPoolMigration=false;session.revision=result.revision;session.rows=structuredClone(rows);baseline=structuredClone(rows);baselinePools=structuredClone(session.pool_percentages);baselineSchema=session.schema;
  notice('已保存到这台 Watcher，并完成回读校验。');
 }catch(error){notice('未确认保存成功：'+error.message+'。当前草稿已保留。');}
 finally{busy=false;render();}
};



render();
getModel().catch(error=>{$('preview-caption').textContent='三维模型加载失败：'+error.message;});
