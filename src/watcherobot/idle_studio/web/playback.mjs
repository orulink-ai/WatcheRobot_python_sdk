// Mirrors joy_idle_profile_pick_group and the per-group previous selection.
export class IdlePlayback {
 constructor(pools=null){this.group="standby";this.previous={};this.pools=pools;}
 next(rows,random=Math.random,excluded=new Set()){
  const available=rows.filter(r=>Number.isFinite(r.weight)&&r.weight>0&&!excluded.has(r.expression));
  let groupChoice=this.group;
  let eligible=available;
  if(this.pools){
   eligible=available.filter(r=>this.pools[r.group]>0);
   const standby=eligible.some(r=>r.group==='standby')?this.pools.standby:0;
   const feature=eligible.some(r=>r.group==='feature')?this.pools.feature:0;
   if(!standby&&!feature)return null;
   groupChoice=random()*(standby+feature)<standby?'standby':'feature';
  }
  let pool=eligible.filter(r=>(r.group||"standby")===groupChoice);
  if(!pool.length)pool=eligible;
  if(!pool.length)return null;
  const group=pool[0].group||"standby";
  const alternatives=pool.filter(r=>r.id!==this.previous[group]);
  if(alternatives.length)pool=alternatives;
  let pick=random()*pool.reduce((sum,r)=>sum+r.weight,0);
  let chosen=pool.at(-1);
  for(const row of pool){if(pick<row.weight){chosen=row;break;}pick-=row.weight;}
  this.previous[group]=chosen.id;this.group=group==="standby"?"feature":"standby";
  return chosen;
 }
}
