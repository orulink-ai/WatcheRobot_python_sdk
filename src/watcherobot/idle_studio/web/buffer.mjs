// Decoded resources are bounded independently of the current/next playback references.
export class PreparedCache {
 constructor(limit=96*1024*1024){this.limit=limit;this.entries=new Map();this.pending=new Map();this.bytes=0;}
 get(key,load){
  if(this.entries.has(key)){const value=this.entries.get(key);this.entries.delete(key);this.entries.set(key,value);return Promise.resolve(value);}
  if(this.pending.has(key))return this.pending.get(key);
  const task=Promise.resolve().then(load).then(value=>{
   const size=value.bytes||0;
   if(size<=this.limit){
    while(this.bytes+size>this.limit&&this.entries.size){const oldest=this.entries.keys().next().value;this.bytes-=this.entries.get(oldest).bytes||0;this.entries.delete(oldest);}
    this.entries.set(key,value);this.bytes+=size;
   }
   return value;
  }).finally(()=>this.pending.delete(key));
  this.pending.set(key,task);return task;
 }
}
export class Lookahead {
 constructor(scheduler){this.scheduler=scheduler;this.pending=null;}
 prime(rows,prepare){
  const signature=JSON.stringify([rows,this.scheduler.pools]);
  if(this.pending?.signature===signature)return;
  const next=Object.assign(Object.create(Object.getPrototypeOf(this.scheduler)),this.scheduler,{previous:{...this.scheduler.previous}});
  const row=next.next(rows);
  const task=row?Promise.resolve().then(()=>prepare({...row})).then(prepared=>({prepared}),error=>({error})):Promise.resolve({prepared:null});
  this.pending={signature,next,row:row?{...row}:null,task};
 }
 async take(rows,prepare,isCurrent=()=>true){
  this.prime(rows,prepare);const pending=this.pending;this.pending=null;
  const result=await pending.task;if(!isCurrent())return {stale:true};if(result.error)throw result.error;
  Object.assign(this.scheduler,pending.next);return {row:pending.row,prepared:result.prepared};
 }
}
