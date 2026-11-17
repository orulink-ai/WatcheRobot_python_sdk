import {test} from 'node:test';
import assert from 'node:assert/strict';
import {PreparedCache, Lookahead} from '../src/watcherobot/idle_studio/web/buffer.mjs';
import {IdlePlayback} from '../src/watcherobot/idle_studio/web/playback.mjs';
test('cache deduplicates preparation, evicts by bytes and retries failures',async()=>{
 const cache=new PreparedCache(10);let calls=0;
 const load=async()=>{calls++;return {bytes:6};};
 const [a,b]=await Promise.all([cache.get('a',load),cache.get('a',load)]);
 assert.equal(a,b);assert.equal(calls,1);
 await cache.get('b',load);await cache.get('a',load);assert.equal(calls,3);
 await assert.rejects(cache.get('bad',async()=>{throw Error('offline');}));
 assert.equal(await cache.get('bad',async()=>({bytes:1})).then(x=>x.bytes),1);
});
test('lookahead prepares without advancing scheduler and invalidates edited drafts',async()=>{
 const scheduler=new IdlePlayback(),q=new Lookahead(scheduler);
 const rows=[{id:'a',expression:'a',weight:1,group:'standby'},{id:'b',expression:'b',weight:1,group:'feature'}];
 let calls=0;const prepare=async row=>{calls++;return row.expression;};
 q.prime(rows,prepare);assert.equal(scheduler.group,'standby');
 const first=await q.take(rows,prepare);assert.equal(first.prepared,'a');assert.equal(calls,1);
 assert.equal(scheduler.group,'feature');
 q.prime(rows,prepare);rows[1].expression='edited';
 const second=await q.take(rows,prepare);assert.equal(second.prepared,'edited');assert.equal(calls,3);
});
test('prefetch failure is captured and reported only when consumed',async()=>{
 const q=new Lookahead(new IdlePlayback()),rows=[{id:'a',weight:1}];
 q.prime(rows,async()=>{throw Error('missing');});
 await assert.rejects(q.take(rows,async()=>null),/missing/);
});

test('a draft change during preparation does not commit stale scheduler state',async()=>{
 const scheduler=new IdlePlayback(),q=new Lookahead(scheduler);
 let resolve;const gate=new Promise(r=>resolve=r);
 const pending=q.take([{id:'old',weight:1}],()=>gate,()=>false);
 resolve({bytes:1});assert.deepEqual(await pending,{stale:true});
 assert.equal(scheduler.group,'standby');assert.deepEqual(scheduler.previous,{});
});

test('model prepares off-screen and starts prepared tracks without decoding or clearing',async()=>{
 const {readFile}=await import('node:fs/promises');const {runInNewContext}=await import('node:vm');
 const source=await readFile(new URL('../src/watcherobot/idle_studio/web/model-preview.mjs',import.meta.url),'utf8');
 let decodes=0,draws=0,clears=0,audioStarts=0;
 const Model=runInNewContext(source.replace(/^import .*;$/gm,'').replace('export class ModelPreview','class ModelPreview')+';ModelPreview',{
  Image:class {constructor(){this.naturalWidth=20;this.naturalHeight=10;}async decode(){decodes++;}},
  motionTrack:()=>null,poseAt:()=>({x:90,y:120}),performance:{now:()=>100}
 });
 const viewer=Object.create(Model.prototype);
 Object.assign(viewer,{serial:0,ready:Promise.resolve(),canvas:{width:240,height:240},texture:{},sheet:'old',context:{drawImage(){draws++;},fillRect(){clears++;}},pose(){},audioContext:{async decodeAudioData(){return {duration:2,length:20,numberOfChannels:1};},createBufferSource(){return {connect(){},start(){audioStarts++;}}}}});
 const data={url:'local',width:10,height:10,durations:[100,100]};
 const prepared=await viewer.prepare(data,null,new ArrayBuffer(2));
 assert.equal(viewer.sheet,'old');assert.equal(draws,0);assert.equal(clears,0);
 viewer.start(prepared,true);assert.equal(decodes,1);assert.equal(draws,1);assert.equal(clears,0);assert.equal(audioStarts,1);assert.equal(viewer.cycle,2000);
});
