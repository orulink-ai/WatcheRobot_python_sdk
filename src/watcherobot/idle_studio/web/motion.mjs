export function motionTrack(action){
 if(!action)return null;
 if(!Number.isFinite(action.fps)||!(action.fps>0)||!Number.isFinite(action.frame_start)||!Number.isFinite(action.frame_end)||action.frame_end<action.frame_start)throw Error('动作时间轴无效');
 const track={duration:(action.frame_end-action.frame_start+1)*1000/action.fps,x:[],y:[]};
 for(const object of action.animated_objects||[]){
  const axis=['body_x','底部轴'].includes(object.object_name)?'x':['head_y','头轴'].includes(object.object_name)?'y':null;
  if(!axis)continue;
  track[axis]=(object.keyframe_data||[]).map(k=>({time:(k.frame_number-action.frame_start)*1000/action.fps,value:axis==='y'?Math.max(100,Math.min(140,Math.round(k.rotation_angle))):Math.round(k.rotation_angle)}));
 }
 // Firmware merges axis key times and holds missing axes at each merged key.
 const times=[...new Set([...track.x,...track.y].map(k=>k.time))].sort((a,b)=>a-b);
 for(const axis of ['x','y']){
  const keys=track[axis];let last=keys[0]?.value??(axis==='x'?90:120);
  track[axis]=times.map(time=>{const key=keys.find(k=>k.time===time);if(key)last=key.value;return {time,value:last};});
 }
 return track;
}
export function poseAt(track,time){
 const at=(frames,fallback)=>{
  if(!frames?.length)return fallback;
  if(time<=frames[0].time)return frames[0].value;
  for(let i=1;i<frames.length;i++)if(time<frames[i].time){const a=frames[i-1],b=frames[i];return a.value+(b.value-a.value)*(time-a.time)/(b.time-a.time);}
  return frames.at(-1).value;
 };
 return {x:at(track?.x,90),y:at(track?.y,track?120:100)};
}
