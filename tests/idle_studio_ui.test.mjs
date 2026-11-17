import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {PreviewSelection} from '../src/watcherobot/idle_studio/web/state.mjs';

const source=readFileSync(new URL('../src/watcherobot/idle_studio/web/app.js',import.meta.url),'utf8');
// Run actual application handlers with minimal DOM stubs and deferred audio.
function fixture(){
 const nodes=new Map();
 const $=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',disabled:false,replaceChildren(){this.textContent='';},append(){},});return nodes.get(id);};
 const p=new PreviewSelection();p.select({},'one','One');
 const viewer={sheet:{},clear(){this.sheet=null;},serial:0};
 const context={$,previewSelection:p,modelViewer:viewer,previewStopped:false,playbackRun:null,previewBehavior:null,
  busy:false,demo:false,session:{},rows:[{weight:1}],el:()=>({}),previewButton:()=>({}),loadOfficialPreview:()=>{throw Error('unexpected reload');}};
 const functions=source.slice(source.indexOf('function playbackControls'),source.indexOf('const preparedCache'))+
  source.slice(source.indexOf('function renderPreview'),source.indexOf('async function loadOfficialPreview'));
 runInNewContext(functions,context);return {context,p,$,viewer};
}
test('stop clears pending overlay and stale preview errors immediately',()=>{
 const {context,p,$}=fixture();p.loading=true;p.error='old failure';$('preview').textContent='loading';
 context.stopPlayback();
 assert.equal($('preview').textContent,'');assert.equal(p.error,'');assert.equal(p.loading,false);
 assert.equal(context.previewStopped,true);assert.equal($('stop-playback').disabled,true);
});
test('stale play-all audio rejection cannot cancel the newer run',async()=>{
 const {context,$,viewer}=fixture();let reject;
 viewer.unlockAudio=()=>new Promise((_,r)=>reject=r);context.Lookahead=class{};context.IdlePlayback=class{};
 context.playNext=()=>{};
 runInNewContext(source.slice(source.indexOf("$('play-all').onclick="),source.indexOf("$('stop-playback').onclick=")),context);
 const pending=$('play-all').onclick();const newer={};context.playbackRun=newer;
 reject(Error('audio unavailable'));await pending;assert.equal(context.playbackRun,newer);
});
test('stopping while a row awaits audio does not resurrect its preview',async()=>{
 const {context,p,$,viewer}=fixture();let resolve;let renders=0;
 viewer.unlockAudio=()=>new Promise(r=>resolve=r);
 Object.assign(context,{r:{id:'one',expression:'one',name:'One'},preview:{},actions:{append(){}},
  selected:null,selectPreview:()=>{},getModel:async()=>viewer,showPreview:()=>{renders++;},render:()=>{renders++;},notice:()=>{}});
 const handler=source.slice(source.indexOf('preview.onclick=async()=>'),source.indexOf(';actions.append(preview)'));
 runInNewContext(handler,context);
 const pending=context.preview.onclick();await new Promise(setImmediate);
 context.stopPlayback();resolve();await pending;
 assert.equal(renders,0);assert.match($('playback-status').textContent,/已停止/);assert.equal(p.loading,false);
});
