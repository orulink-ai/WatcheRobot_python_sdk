import test from 'node:test';
import assert from 'node:assert/strict';
import {tableRows, updateCell} from '../src/watcherobot/idle_studio/web/table-state.mjs';

test('filtering preserves full-pool shares and tracks edits by identity', () => {
 const original=[{id:'a',name:'A',expression:'blink',group:'standby',weight:1},{id:'b',name:'B',expression:'sleep',group:'standby',weight:3},{id:'c',name:'C',group:'feature',weight:10}];
 const draft=structuredClone(original);draft[1].name='Sleep';
 const visible=tableRows(draft,original,'standby','sleep');
 assert.equal(visible.length,1);assert.equal(visible[0].share,75);assert.equal(visible[0].changed,true);
 assert.equal(tableRows(draft,original,'all','')[0].changed,false);
});
test('invalid cell input does not mutate draft; optional tracks remain null', () => {
 const row={name:'A',weight:10,motion:'turn'};
 for(const value of ['', '-1', '1.5','1000001','NaN'])assert.equal(updateCell(row,'weight',value),false);
 assert.equal(row.weight,10);assert.equal(updateCell(row,'weight','0'),true);
 assert.equal(updateCell(row,'motion',''),true);assert.equal(row.motion,null);
 assert.equal(updateCell(row,'name','   '),false);assert.equal(row.name,'A');
});

import {setProbability} from '../src/watcherobot/idle_studio/web/table-state.mjs';
test('percentage edit is exact and redistributes only its group',()=>{
 const rows=Array.from({length:10},(_,i)=>({id:String(i),group:'standby',weight:1}));rows.push({id:'other',group:'feature',weight:7});
 assert.equal(setProbability(rows,'0','1'),true);
 assert.deepEqual(rows.slice(0,10).map(r=>r.weight),[1,11,11,11,11,11,11,11,11,11]);
 assert.equal(rows[10].weight,7);
 assert.equal(setProbability(rows,'0','100'),true);assert.equal(rows[0].weight,100);assert.ok(rows.slice(1,10).every(r=>r.weight===0));
 assert.equal(setProbability(rows,'0','2'),true);assert.equal(rows.slice(0,10).reduce((n,r)=>n+r.weight,0),100);
});
test('percentage rejects invalid edits atomically and handles a sole behavior',()=>{
 const rows=[{id:'a',weight:1}];
 for(const value of ['0','101','0.5','','no','50'])assert.equal(setProbability(rows,'a',value),false);
 assert.equal(rows[0].weight,1);assert.equal(setProbability(rows,'a','100'),true);
});

import {normalizeGroups} from '../src/watcherobot/idle_studio/web/table-state.mjs';
test('structural edits normalize only affected groups to integer percentages',()=>{
 const rows=[{id:'a',group:'standby',weight:1},{id:'b',group:'standby',weight:1},{id:'c',group:'standby',weight:1},{id:'d',group:'feature',weight:7}];
 normalizeGroups(rows,['standby']);assert.deepEqual(rows.map(r=>r.weight),[34,33,33,7]);
 rows.splice(0,1);normalizeGroups(rows,['standby']);assert.deepEqual(rows.map(r=>r.weight),[50,50,7]);
});
test('normalizing a paused pool does not silently enable behaviors',()=>{
 const rows=[{id:'a',weight:0},{id:'b',weight:0}];normalizeGroups(rows,[undefined]);assert.deepEqual(rows.map(r=>r.weight),[0,0]);
});
test('search finds display names from the device catalog',()=>{
 const rows=[{id:'a',name:'standby_mouth1',expression:'mouth1',weight:1}];
 assert.equal(tableRows(rows,rows,'all','静默',{expressions:[{id:'mouth1',display_name:'静默1'}]}).length,1);
});
