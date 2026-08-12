const config = await fetch('/config.json', {cache: 'no-store'}).then(r => r.json());
const $ = id => document.getElementById(id);
const ui = {state:$('state'),code:$('pairing-code'),mode:$('mode'),pair:$('pair'),start:$('start'),stop:$('stop'),stress:$('stress'),cycles:$('cycles'),report:$('report'),message:$('message'),events:$('events'),metrics:$('metrics'),canvas:$('video'),session:$('session')};
const ctx = ui.canvas.getContext('2d', {alpha:false});
const clientId = `desktop-${crypto.randomUUID()}`;
let sessionId = '';
let commandSequence = 0;
let socket = null;
let peer = null;
let media = null;
let mjpegChannel = null;
let stressChannel = null;
let stressTimer = null;
let feedbackTimer = null;
let clockTimer = null;
let pendingFrame = null;
let decodeBusy = false;
let lastDisplayAt = 0;
let lastSequence = null;
let clockOffsetMs = null;
let clockBestRttMs = Infinity;
let deviceClockReferenceMs = null;
let offerSent = false;
let pendingLocalCandidates = [];
let pendingRemoteCandidates = [];
let rtcState = 'idle';
const frameIntervals = [];
const frameAges = [];
const report = {startedAt:new Date().toISOString(),events:[],stats:[],browserStats:[],browser:navigator.userAgent,protocol:config.protocol,pythonSdkVersion:config.pythonSdkVersion,pythonSdkCommit:config.pythonSdkCommit};

const metricNames = [
  ['displayFps','显示 FPS'],['targetFps','目标 FPS'],['frameP95','帧间隔 P95'],['latencyP95','端到端 P95'],['sendP95','发送 P95'],
  ['sendMax','发送最大值'],['jpeg','JPEG'],['drops','丢帧'],['audioLoss','音频丢包'],['heap','空闲堆']
];
ui.metrics.innerHTML = metricNames.map(([id,label]) => `<div class="metric"><span>${label}</span><b id="m-${id}">—</b></div>`).join('');

function id(prefix){ commandSequence += 1; return `${prefix}-${Date.now()}-${commandSequence}`; }
function log(message, data){ const line=`${new Date().toLocaleTimeString()} ${message}${data?` ${JSON.stringify(data)}`:''}`; report.events.push(line); ui.events.textContent=`${line}\n${ui.events.textContent}`.slice(0,12000); }
function setState(label, kind='idle'){ ui.state.textContent=label; ui.state.className=`pill ${kind}`; }
function setMessage(message, bad=false){ ui.message.textContent=message; ui.message.className=bad?'message bad':'message'; }
function metric(id, value){ const node=$(`m-${id}`); if(node) node.textContent=value; }
function envelope(type, data={}, commandId=id('rtc')){ return {type,protocol:config.protocol,client_id:clientId,session_id:sessionId,command_id:commandId,data}; }
function send(message){ if(!socket || socket.readyState!==WebSocket.OPEN) throw new Error('Daemon control channel is not connected'); socket.send(JSON.stringify(message)); }

async function pairDevice(){
  const pairingCode=ui.code.value.trim();
  if(!/^\d{6}$/.test(pairingCode)){ setMessage('配对码必须是六位数字。',true); return; }
  ui.pair.disabled=true; setState('配对中'); setMessage('正在通过局域网发现设备…');
  try{
    const response=await fetch(`${config.controlUrl}/daemon/devices/pair`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pairing_code:pairingCode,target_mode:'desktop_link'})});
    const body=await response.json(); if(!response.ok) throw new Error(body.message||body.error||'pair failed');
    await waitForDevice(); await connectDaemon();
    ui.start.disabled=false; ui.cycles.disabled=false; setState('SDK 已连接','live'); setMessage('配对成功，可以开始 RTC。');
  }catch(error){ setState('配对失败','error'); setMessage(String(error),true); ui.pair.disabled=false; log('pair failed',{error:String(error)}); }
}

async function waitForDevice(){
  const deadline=performance.now()+12000;
  while(performance.now()<deadline){ const status=await fetch(`${config.controlUrl}/daemon/devices`,{cache:'no-store'}).then(r=>r.json()); if(status.device?.online) return; await new Promise(r=>setTimeout(r,250)); }
  throw new Error('设备在 12 秒内没有完成 SDK WebSocket 握手');
}

async function connectDaemon(){
  if(socket?.readyState===WebSocket.OPEN) return;
  socket=new WebSocket(config.externalUrl); socket.binaryType='arraybuffer';
  await new Promise((resolve,reject)=>{ const timeout=setTimeout(()=>reject(new Error('Daemon WebSocket timeout')),5000); socket.onopen=()=>{socket.send(JSON.stringify({type:'sys.client.hello',code:0,data:{role:'desktop',client_id:clientId}}));}; socket.onmessage=event=>{ if(typeof event.data==='string'){ const msg=JSON.parse(event.data); if(msg.type==='sys.ack'&&msg.data?.type==='sys.client.hello'){clearTimeout(timeout);socket.onmessage=handleMessage;resolve();} } }; socket.onerror=()=>reject(new Error('Daemon WebSocket failed')); });
  socket.onmessage=handleMessage; socket.onclose=()=>{setState('控制通道断开','error');stopRtc(false);};
}

async function handleMessage(event){
  if(typeof event.data!=='string') return;
  let msg; try{msg=JSON.parse(event.data);}catch{return;}
  if(typeof msg.type==='string'&&msg.type.startsWith('evt.rtc.')&&msg.protocol!==config.protocol){log('dropped protocol mismatch');return;}
  if(msg.client_id && msg.client_id!==clientId) return;
  if(msg.session_id && sessionId && msg.session_id!==sessionId) return;
  if(msg.type==='evt.rtc.signal'){try{await handleSignal(msg.data||{});}catch(error){setMessage(String(error),true);log('invalid rtc signal',{error:String(error)});await stopRtc();}}
  else if(msg.type==='evt.rtc.state'){ const state=msg.data?.state||'unknown';rtcState=state;setState(`RTC ${state}`,state==='connected'?'live':state==='failed'?'error':'idle'); if(state==='connected'){ui.stress.disabled=false;} log('rtc state',msg.data); }
  else if(msg.type==='evt.rtc.stats'){ updateDeviceStats(msg.data||{}); report.stats.push({at:Date.now(),...msg.data}); }
  else if(msg.type==='evt.rtc.clock.pong') applyClockSample(msg.data||{});
  else if(msg.type==='evt.rtc.capabilities'){report.capabilities=msg.data;log('capabilities',msg.data);}
  else if(msg.type==='sys.nack'){setMessage(`${msg.data?.type||'command'}: ${msg.data?.error||'rejected'}`,true);log('nack',msg.data);}
}

async function startRtc(){
  if(peer) return;
  sessionId=`session-${crypto.randomUUID()}`;rtcState='starting'; ui.session.textContent=sessionId.slice(0,24); setState('RTC 启动中');
  try{
    const mode=ui.mode.value;
    peer=new RTCPeerConnection({iceServers:[],bundlePolicy:'max-bundle'});
    peer.onconnectionstatechange=()=>{log('peer state',{state:peer?.connectionState});if(peer?.connectionState==='failed')setState('P2P 失败','error');};
    offerSent=false;pendingLocalCandidates=[];pendingRemoteCandidates=[];
    peer.onicecandidate=event=>{if(!event.candidate)return;const candidate={kind:'candidate',candidate:event.candidate.candidate,sdp_mid:event.candidate.sdpMid||'0',sdp_mline_index:event.candidate.sdpMLineIndex||0};if(offerSent)send(envelope('ctrl.rtc.signal',candidate));else pendingLocalCandidates.push(candidate);};
    if(mode!=='video'){
      media=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
      for(const track of media.getAudioTracks()) peer.addTrack(track,media);
    }
    mjpegChannel=peer.createDataChannel('mjpeg-data',{ordered:false,maxPacketLifeTime:200}); mjpegChannel.binaryType='arraybuffer'; mjpegChannel.onmessage=queueFrame; mjpegChannel.onopen=()=>log('mjpeg-data open'); mjpegChannel.onclose=()=>log('mjpeg-data closed');
    stressChannel=peer.createDataChannel('rtc-stress',{ordered:false,maxPacketLifeTime:100}); stressChannel.onopen=()=>{ui.stress.disabled=false;};
    send(envelope('ctrl.rtc.session.start',{mode}));
    const offer=await peer.createOffer(); await peer.setLocalDescription(offer);
    send(envelope('ctrl.rtc.signal',{kind:'offer',sdp:offer.sdp||''}));
    offerSent=true;for(const candidate of pendingLocalCandidates)send(envelope('ctrl.rtc.signal',candidate));pendingLocalCandidates=[];
    ui.start.disabled=true;ui.stop.disabled=false;feedbackTimer=setInterval(sendFeedback,1000);clockTimer=setInterval(sendClockPing,3000);sendClockPing();
  }catch(error){ setState('RTC 启动失败','error');setMessage(String(error),true);log('start failed',{error:String(error)});await stopRtc(); }
}

async function handleSignal(data){
  if(!peer) return;
  if(data.kind==='answer'){if(typeof data.sdp!=='string'||new TextEncoder().encode(data.sdp).byteLength>16384)throw new Error('invalid RTC answer');await peer.setRemoteDescription({type:'answer',sdp:data.sdp});for(const candidate of pendingRemoteCandidates)await peer.addIceCandidate(candidate);pendingRemoteCandidates=[];}
  else if(data.kind==='candidate'){if(typeof data.candidate!=='string'||new TextEncoder().encode(data.candidate).byteLength>2048)throw new Error('invalid ICE candidate');const candidate={candidate:data.candidate,sdpMid:data.sdp_mid,sdpMLineIndex:data.sdp_mline_index};if(peer.remoteDescription)await peer.addIceCandidate(candidate);else pendingRemoteCandidates.push(candidate);}
  else if(data.kind==='bye') await stopRtc(false);
}

async function stopRtc(notify=true){
  stopStress(); clearInterval(feedbackTimer);clearInterval(clockTimer);feedbackTimer=null;clockTimer=null;
  if(notify&&socket?.readyState===WebSocket.OPEN&&sessionId){try{send(envelope('ctrl.rtc.session.stop',{}));}catch{}}
  if(peer){peer.close();peer=null;} if(media){media.getTracks().forEach(t=>t.stop());media=null;} mjpegChannel=null;stressChannel=null;pendingFrame=null;decodeBusy=false;
  offerSent=false;pendingLocalCandidates=[];pendingRemoteCandidates=[];ui.start.disabled=!(socket?.readyState===WebSocket.OPEN);ui.stop.disabled=true;ui.stress.disabled=true;setState('SDK 已连接',socket?.readyState===WebSocket.OPEN?'live':'error');
}

function queueFrame(event){
  const bytes=new Uint8Array(event.data); if(bytes.byteLength<20) return;
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength); const magic=String.fromCharCode(...bytes.slice(0,4)); const size=view.getUint32(16,true);
  if(magic!=='WJPG'||bytes[4]!==1||bytes[5]!==0||view.getUint16(6,true)!==20||size===0||size>61440||size!==bytes.byteLength-20||bytes[20]!==0xff||bytes[21]!==0xd8||bytes.at(-2)!==0xff||bytes.at(-1)!==0xd9) return;
  pendingFrame={sequence:view.getUint32(8,true),timestamp:view.getUint32(12,true),jpeg:bytes.slice(20)}; if(!decodeBusy)decodeLatest();
}

async function decodeLatest(){
  decodeBusy=true;
  while(pendingFrame){
    const frame=pendingFrame;pendingFrame=null;
    try{
      const bitmap=await createImageBitmap(new Blob([frame.jpeg],{type:'image/jpeg'}));ctx.drawImage(bitmap,0,0,640,480);bitmap.close();
      const now=performance.now();if(lastDisplayAt){pushBounded(frameIntervals,now-lastDisplayAt,600);}lastDisplayAt=now;
      if(clockOffsetMs!==null&&deviceClockReferenceMs!==null){const timestamp=unwrapDeviceTimestamp(frame.timestamp);pushBounded(frameAges,now-(timestamp+clockOffsetMs),600);}
      if(lastSequence!==null&&frame.sequence>lastSequence+1)metric('drops',String(frame.sequence-lastSequence-1));lastSequence=frame.sequence;
      metric('jpeg',`${(frame.jpeg.byteLength/1024).toFixed(1)} KiB`);updateBrowserMetrics();
    }catch(error){log('decode failed',{error:String(error)});}
  }
  decodeBusy=false;
}

function pushBounded(items,value,max){items.push(value);if(items.length>max)items.splice(0,items.length-max);}
function pct(items,rank){if(!items.length)return 0;const sorted=[...items].sort((a,b)=>a-b);return sorted[Math.max(0,Math.ceil(sorted.length*rank/100)-1)];}
function updateBrowserMetrics(){const p95=pct(frameIntervals,95);metric('displayFps',p95?Math.min(99,1000/p95).toFixed(1):'—');metric('frameP95',p95?`${p95.toFixed(0)} ms`:'—');const age=pct(frameAges,95);metric('latencyP95',age?`${age.toFixed(0)} ms`:'—');}
function updateDeviceStats(data){metric('targetFps',data.target_fps??'—');metric('sendP95',data.video_send_p95_us?`${(data.video_send_p95_us/1000).toFixed(0)} ms`:'—');metric('sendMax',data.video_send_max_us?`${(data.video_send_max_us/1000).toFixed(0)} ms`:'—');metric('drops',data.dropped_frames??'—');metric('audioLoss',data.audio_loss_percent!=null?`${Number(data.audio_loss_percent).toFixed(2)}%`:'—');metric('heap',data.free_heap_bytes?`${(data.free_heap_bytes/1024).toFixed(0)} KiB`:'—');}

async function sendFeedback(){
  if(!peer||!sessionId)return;let audioLoss=0,audioJitter=0,audioQueue=0,concealed=0;
  for(const stat of (await peer.getStats()).values()){if(stat.type==='inbound-rtp'&&stat.kind==='audio'){const total=(stat.packetsReceived||0)+(stat.packetsLost||0);audioLoss=total?100*(stat.packetsLost||0)/total:0;audioJitter=1000*(stat.jitter||0);audioQueue=stat.jitterBufferEmittedCount?1000*(stat.jitterBufferDelay||0)/stat.jitterBufferEmittedCount:0;concealed=stat.concealedSamples||0;}}
  const p95=pct(frameIntervals,95),age=pct(frameAges,95),rtt=Number.isFinite(clockBestRttMs)?clockBestRttMs:0;
  const congestion=(age>250||p95>180||rtt>180)?2:(age>180||p95>140||rtt>120)?1:0;
  const sample={at:Date.now(),display_fps:p95?1000/p95:0,frame_interval_p95_ms:p95,frame_age_p95_ms:age,rtt_ms:rtt,audio_queue_ms:audioQueue,audio_packet_loss_percent:audioLoss,audio_jitter_ms:audioJitter,audio_concealed_samples:concealed,congestion_level:congestion};
  report.browserStats.push(sample);metric('audioLoss',`${audioLoss.toFixed(2)}%`);
  send(envelope('ctrl.rtc.feedback',{display_fps_x100:Math.round(sample.display_fps*100),frame_age_p95_us:Math.round(age*1000),rtt_us:Math.round(rtt*1000),audio_queue_ms:Math.round(audioQueue),audio_packet_loss_x100:Math.round(audioLoss*100),audio_jitter_us:Math.round(audioJitter*1000),audio_concealed_frames:Math.round(concealed),congestion_level:congestion}));
}
function sendClockPing(){if(!peer)return;send(envelope('ctrl.rtc.clock.ping',{browser_send_us:Math.round(performance.now()*1000)}));}
function applyClockSample(data){const t3=performance.now(),t0=Number(data.browser_send_us)/1000,t1=Number(data.device_receive_us)/1000,t2=Number(data.device_send_us)/1000;const rtt=(t3-t0)-(t2-t1);if(Number.isFinite(rtt)&&rtt>=0&&rtt<clockBestRttMs){clockBestRttMs=rtt;clockOffsetMs=((t0-t1)+(t3-t2))/2;deviceClockReferenceMs=t2;}}
function unwrapDeviceTimestamp(timestamp){const wrap=4294967296;return timestamp+Math.round((deviceClockReferenceMs-timestamp)/wrap)*wrap;}

function startStress(){
  if(stressTimer){stopStress();return;}if(!stressChannel||stressChannel.readyState!=='open')return;
  const payload=crypto.getRandomValues(new Uint8Array(16000));stressTimer=setInterval(()=>{if(stressChannel?.readyState==='open'&&stressChannel.bufferedAmount<65536)stressChannel.send(payload);},64);ui.stress.textContent='停止压力';log('stress started',{target_bps:2000000});
}
function stopStress(){if(stressTimer){clearInterval(stressTimer);stressTimer=null;log('stress stopped');}ui.stress.textContent='2 Mbps 压力';}
async function waitForRtcState(expected,timeoutMs){const deadline=performance.now()+timeoutMs;while(performance.now()<deadline){if(rtcState===expected)return;await new Promise(r=>setTimeout(r,50));}throw new Error(`RTC did not reach ${expected} within ${timeoutMs} ms`);}
async function runCycles(){ui.cycles.disabled=true;try{for(let i=1;i<=20;i+=1){setMessage(`生命周期循环 ${i}/20`);await startRtc();await waitForRtcState('connected',10000);await new Promise(r=>setTimeout(r,500));await stopRtc();await waitForRtcState('stopped',5000);}setMessage('20 次生命周期循环完成。');}catch(error){setMessage(String(error),true);log('lifecycle cycle failed',{error:String(error)});await stopRtc();}finally{ui.cycles.disabled=false;}}
function exportReport(){const blob=new Blob([JSON.stringify({...report,finishedAt:new Date().toISOString(),clientId,sessionId,frameIntervalP95Ms:pct(frameIntervals,95),frameAgeP95Ms:pct(frameAges,95)},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`watcher-rtc-${new Date().toISOString().replaceAll(':','-')}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}

ui.pair.addEventListener('click',pairDevice);ui.start.addEventListener('click',startRtc);ui.stop.addEventListener('click',()=>stopRtc());ui.stress.addEventListener('click',startStress);ui.cycles.addEventListener('click',runCycles);ui.report.addEventListener('click',exportReport);window.addEventListener('beforeunload',()=>{try{if(sessionId)send(envelope('ctrl.rtc.session.stop',{}));}catch{}});
