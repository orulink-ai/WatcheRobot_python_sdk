// Default contract of joyinside-integration-on-dev-20261103:
// joyinside_idle_core.c + joy_idle_defaults.c. Preserve equal weights exactly;
// rounding a twelve-item pool to integer percentages would change its defaults.
const canonical = ['standby1','standby2','standby3','standby4','standby5','standby6',
 'standby_little1','standby_little2','standby_little3','standby_little4',
 'funnyface','snow','custom1','happy','bigeyes','calmness','inquisitive','sunglasses','hesitate','music','swimming','coding'];
const mouth = ['standby_mouth1','standby_mouth2','standby_mouth3','standby_mouth4','standby_mouth5','standby_mouth6',
 'standby_little_mouth1','standby_little_mouth2','standby_little_mouth3','standby_little_mouth4',
 'funnyface','snow','love_mouth','happy_mouth','biyeyes_mouth','clmness_mouth','inquisitive_mouth','sunglasses_mouth','hesitate_mouth','music_mouth','swimming','coding'];

export function officialDefaults(session) {
 if (!session?.editable || session.schema < 2) throw Error('请先连接支持分组待机行为的 Watcher');
 const assets = new Map((session.catalog?.expressions || []).map(item=>[item.id,item.assets]));
 const rows = canonical.flatMap((preferred,index)=>{
  const id = assets.get(preferred)?.animation ? preferred : mouth[index];
  const tracks = assets.get(id);
  if (!tracks?.animation) return [];
  return [{id,name:id,expression:id,motion:tracks.action?id:null,sound:tracks.sound?id:null,
   group:index<10?'standby':'feature',weight:1}];
 });
 if (!rows.length) throw Error('设备中没有可用的官方待机素材，请先补齐素材');
 return rows;
}
