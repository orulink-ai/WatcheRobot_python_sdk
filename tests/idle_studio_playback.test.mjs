import test from "node:test";
import assert from "node:assert/strict";
import {IdlePlayback} from "../src/watcherobot/idle_studio/web/playback.mjs";
const row=(id,group,weight=1)=>({id,group,weight,expression:id});
test("alternates groups and excludes each groups previous behavior",()=>{
 const p=new IdlePlayback(), rows=[row("a","standby"),row("b","standby"),row("c","feature"),row("d","feature")];
 assert.deepEqual(Array.from({length:6},()=>p.next(rows,()=>0).id),["a","c","b","d","a","c"]);
});
test("uses weights and ignores paused rows",()=>{
 const rows=[row("off","standby",0),row("a","standby",1),row("b","standby",99)];
 assert.equal(new IdlePlayback().next(rows,()=>0).id,"a");
 assert.equal(new IdlePlayback().next(rows,()=>.01).id,"b");
});
test("empty group falls back; one behavior may repeat; empty pool stops",()=>{
 const p=new IdlePlayback(), rows=[row("only","feature")];
 assert.equal(p.next(rows).id,"only");assert.equal(p.next(rows).id,"only");
 assert.equal(p.next([]),null);assert.equal(p.next([row("off","standby",0)]),null);
});
test("subsequent choices use latest draft and respect excluded resources",()=>{
 const p=new IdlePlayback(), rows=[row("a","standby"),row("b","feature")];
 p.next(rows);rows[1].weight=0;rows.push(row("new","feature"));
 assert.equal(p.next(rows).id,"new");
 assert.equal(p.next(rows,()=>0,new Set(["a","new"])),null);
});
