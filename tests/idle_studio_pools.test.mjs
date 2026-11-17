import test from 'node:test';
import assert from 'node:assert/strict';
import {IdlePlayback} from '../src/watcherobot/idle_studio/web/playback.mjs';
import {readSession, saveRequest} from '../src/watcherobot/idle_studio/web/state.mjs';
import {Lookahead} from '../src/watcherobot/idle_studio/web/buffer.mjs';
const rows=[{id:'a',expression:'a',group:'standby',weight:100},{id:'b',expression:'b',group:'feature',weight:100}];

test('supported legacy profiles expose pool probabilities by default without mutating the device document',()=>{
 const data={editable:true,profile:{schema:2,behaviors:rows},device:{capabilities:['joy_idle_pools_v1']}};
 const session=readSession(data);
 assert.equal(session.schema,3);
 assert.equal(session.pendingPoolMigration,true);
 assert.deepEqual(session.pool_percentages,{standby:50,feature:50});
 assert.equal(data.profile.schema,2);
 assert.equal(data.profile.pool_percentages,undefined);
});
test('default pool selection assigns zero to an empty pool',()=>{
 const session=readSession({editable:true,profile:{schema:2,behaviors:[rows[0]]},device:{capabilities:['joy_idle_pools_v1']}});
 assert.deepEqual(session.pool_percentages,{standby:100,feature:0});
});
test('unsupported or unreadable profiles are never silently upgraded',()=>{
 for(const data of [
  {editable:true,profile:{schema:2,behaviors:rows},device:{capabilities:[]}},
  {editable:false,profile:{schema:2,behaviors:rows},device:{capabilities:['joy_idle_pools_v1']}},
  {editable:true,device:{capabilities:['joy_idle_pools_v1']}},
 ]) assert.notEqual(readSession(data).schema,3);
});

test('pool edits discard a prefetched scene from a now disabled pool',async()=>{
 const scheduler=new IdlePlayback({standby:100,feature:0});
 const queue=new Lookahead(scheduler);
 queue.prime(rows,async row=>row.id);
 scheduler.pools={standby:0,feature:100};
 const next=await queue.take(rows,async row=>row.id);
 assert.equal(next.row.id,'b');
 assert.deepEqual(scheduler.pools,{standby:0,feature:100});
});

test('pool and behavior use independent random samples',()=>{
 const scheduler=new IdlePlayback({standby:80,feature:20});
 const samples=[.1,.99];
 const choices=[...rows,{id:'c',expression:'c',group:'standby',weight:100}];
 assert.equal(scheduler.next(choices,()=>samples.shift()).id,'c');
 assert.equal(samples.length,0);
});
test('pool draw uses 80/20 boundary independently on every scene',()=>{
 const scheduler=new IdlePlayback({standby:80,feature:20});
 assert.equal(scheduler.next(rows,()=>.7999).id,'a');
 assert.equal(scheduler.next(rows,()=>.7999).id,'a');
 assert.equal(scheduler.next(rows,()=>.8).id,'b');
});
test('disabled pool never becomes a fallback when enabled assets fail',()=>{
 const scheduler=new IdlePlayback({standby:100,feature:0});
 assert.equal(scheduler.next(rows,()=>.99).id,'a');
 assert.equal(scheduler.next(rows,()=>0,new Set(['a'])),null);
});
test('pool settings survive device read and save independently of row weights',()=>{
 const profile={schema:3,pool_percentages:{standby:80,feature:20},behaviors:rows};
 const session=readSession({port:'test',editable:true,device_id:'device',revision:4,profile});
 assert.deepEqual(saveRequest(session,rows).profile,profile);
});
