import test from 'node:test';
import assert from 'node:assert/strict';
import {officialDefaults} from '../src/watcherobot/idle_studio/web/defaults.mjs';
const session = expressions => ({editable:true,schema:2,catalog:{expressions}});
test('official defaults restore groups and equal chances, using available companion tracks',()=>{
 const s=session([{id:'standby_mouth1',assets:{animation:{},action:{}}},{id:'love_mouth',assets:{animation:{},sound:{}}},{id:'user_custom',assets:{animation:{}}}]);
 const before=structuredClone(s);
 assert.deepEqual(officialDefaults(s),[
  {id:'standby_mouth1',name:'standby_mouth1',expression:'standby_mouth1',motion:'standby_mouth1',sound:null,group:'standby',weight:1},
  {id:'love_mouth',name:'love_mouth',expression:'love_mouth',motion:null,sound:'love_mouth',group:'feature',weight:1},
 ]);
 assert.deepEqual(s,before);
});
test('canonical IDs take precedence over mouth aliases; missing animations are excluded',()=>{
 const s=session([{id:'standby1',assets:{animation:{}}},{id:'standby_mouth1',assets:{animation:{}}},{id:'snow',assets:{sound:{}}}]);
 assert.deepEqual(officialDefaults(s).map(r=>r.id),['standby1']);
 officialDefaults(s)[0].weight=99;assert.equal(officialDefaults(s)[0].weight,1);
});
test('unavailable defaults and unsupported sessions fail without replacing drafts',()=>{
 for(const s of [null,{editable:false,schema:2},{editable:true,schema:1},session([])])assert.throws(()=>officialDefaults(s));
});
