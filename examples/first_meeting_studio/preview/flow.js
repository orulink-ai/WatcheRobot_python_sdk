/* Pure simulation clock. No device, microphone, network or persistent storage. */
(function(root){
 'use strict';
 const poses=[
 [600,0,-32,-32,32,0,1,100,0],
 [2000,350,-49,-43,55,0,.95,103,-1],
 [1000,350,-32,-32,32,0,1,100,0],
 [2000,0,-65,-59,72,.7,.25,110,2],
 [1700,0,-80,-80,80,1,0,113,0]];
 const keys=['left_upper','right_upper','lower','mouth_opacity','gaze_y','tilt','pan'];
 const smooth=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x);};
 function wakeAt(ms){
  let previous=poses[0].slice(2);
  for(let i=0;i<poses.length;i++){const p=poses[i],target=p.slice(2);
   if(ms<=p[0]+p[1]){const t=i===0?1:smooth(ms/p[0]);return Object.fromEntries(keys.map((k,j)=>[k,previous[j]+(target[j]-previous[j])*t]));}
   ms-=p[0]+p[1];previous=target;
  }return Object.fromEntries(keys.map((k,j)=>[k,previous[j]]));
 }
 const stages={
 scan:['等待扫码','机器人直接显示配网入口。手机扫码后，继续输入 Wi-Fi 密码。'],
 network:['手机输入密码','机器人显示等待页；用下方按钮模拟手机提交的结果。'],
 failed:['连接没有成功','保留重试入口，返回输入密码，不重走扫码。'],
 fade:['网络已连接','密码页保留成功反馈，随后用 1.2 秒渐隐。'],
 wake:['慢慢醒来','先试着睁眼，又轻轻闭上；第二次抬眼醒来，嘴巴渐显。'],
 peek:['偷偷探头','轻轻探头，看看这个新世界。'],
 left:['看看左边','好奇地转向左侧。'],
 right:['看看右边','再看看右边，慢慢回正。'],
 hello:['发现你了','温暖的笑意，轻轻点头。'],
 ask:['交换名字','把声音换成文字，在右侧回复。'],
 name:['等你告诉我名字','输入名字，继续这次初见。'],
 invite:['留个小纪念','先征得同意，再进入拍照。'],
 consent:['等待拍照许可','可以拍照，也可以跳过。'],
 clarify:['确认你的意思','只有明确同意才会拍照。'],
 photo:['第一张合照','拍照表情播放一次；闪光时生成示意纪念卡。'],
 photoDone:['记住这一刻','照片为示意，不访问摄像头。'],
 skip:['不用拍照也没关系','尊重选择，继续认识彼此。'],
 chat:['初见完成','可以继续文字对话，也可以查看日常开机。'],
 thinking:['认真听你说','离线示例回复，不调用在线模型。'],
 reply:['聊一聊','嘴巴只在文字播放时张合。'],
 daily:['日常开机','再次开机不重复初见，不自动收音。']};
 const lines={peek:'嗯？外面好像比梦里还热闹。',left:'这是我的新家吗？',hello:'啊，找到你了！我是 Watcher。以后这张桌子，分我一小块就好。',ask:'我该怎么称呼你呀？可以告诉我，我叫……',clarify:'我还没确认你的意思。如果愿意拍照，请说“可以”；不愿意也没关系。',photoDone:'照片保存好啦。今天是我们认识的第一天，值得偷偷开心一下！你平时喜欢做什么呀？',skip:'好的，我们不拍照。你平时喜欢做什么呀？'};
 const chain={fade:'wake',wake:'peek',peek:'left',left:'right',right:'hello',hello:'ask',ask:'name',invite:'consent',clarify:'consent',photo:'photoDone',photoDone:'chat',skip:'chat',thinking:'reply',reply:'chat'};
 class Flow{
  constructor(){this.reset();}
  reset(){this.state='scan';this.elapsed=0;this.clock=0;this.paused=false;this.messages=[];this.captures=0;this.name='朋友';this.photoMarked=false;this.reply='';}
  enter(state){this.state=state;this.elapsed=0;const text=this.line;if(text)this.messages.push({role:'watcher',text,state});}
  get line(){return this.state==='invite'?`你好，${this.name}。想给今天留个小纪念，可以拍一张你的照片吗？照片只保存在这台电脑，不拍也完全没关系。`:this.state==='reply'?this.reply:lines[this.state]||'';}
  get duration(){return ({fade:1200,wake:8120,right:2400,photo:3500,thinking:2000})[this.state]||(this.line?Math.max(2400,this.line.length*130+1100):Infinity);}
  get speaking(){return !!this.line&&this.elapsed>350&&this.elapsed<this.duration-600;}
  get title(){return stages[this.state][0];}
  get detail(){return stages[this.state][1];}
  next(){if(this.paused)return;if(this.state==='scan'||this.state==='failed')this.enter('network');else if(this.state==='network')this.enter('fade');}
  fail(){if(this.state==='network'&&!this.paused)this.enter('failed');}
  tick(dt){if(this.paused||!Number.isFinite(dt)||dt<0)return;this.clock+=dt;
   while(dt>0){const step=Math.min(dt,this.duration-this.elapsed);this.elapsed+=step;dt-=step;
    if(this.state==='photo'&&!this.photoMarked&&this.elapsed>=2500){this.photoMarked=true;this.captures++;}
    if(this.elapsed>=this.duration&&chain[this.state])this.enter(chain[this.state]);else break;
   }
  }
  input(raw){if(this.paused)return;const text=String(raw).trim().slice(0,160);if(!text||!['name','consent','chat'].includes(this.state))return;this.messages.push({role:'user',text});
   if(this.state==='name'){this.name=text.replace(/^(我的名字是|我叫|叫我)/,'').trim().slice(0,16)||'朋友';this.enter('invite');}
   else if(this.state==='consent'){if(/不|别|跳过|拒绝|算了/.test(text))this.enter('skip');else if(/^(可以|好的|好|同意|拍吧|可以拍照|愿意)[！!。.]?$/.test(text))this.enter('photo');else this.enter('clarify');}
   else{this.reply=`记住啦，${this.name}。和你一起慢慢发现新鲜事，应该会很有意思。桌边那一小块位置，我先预订啦。`;this.enter('thinking');}
  }
 }
 const api={Flow,wakeAt,smooth};if(typeof module!=='undefined')module.exports=api;else root.WatcherPreview=api;
})(typeof globalThis!=='undefined'?globalThis:this);
