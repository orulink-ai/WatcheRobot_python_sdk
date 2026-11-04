const {test}=require('node:test');
const assert=require('node:assert/strict');
const {Flow,wakeAt}=require('./flow.js');
test('配网失败返回密码页，不跳过苏醒',()=>{
 const f=new Flow();f.next();assert.equal(f.state,'network');f.fail();assert.equal(f.state,'failed');f.next();assert.equal(f.state,'network');f.next();assert.equal(f.state,'fade');f.tick(1200);assert.equal(f.state,'wake');
});
test('暂停冻结时钟，重播清除对白和拍照',()=>{
 const f=new Flow();f.next();f.next();f.paused=true;f.tick(5000);assert.equal(f.elapsed,0);f.reset();assert.equal(f.state,'scan');assert.equal(f.messages.length,0);assert.equal(f.captures,0);assert.equal(f.paused,false);
});
test('两次睁眼，闭眼嘴巴不可见，最终回中间',()=>{
 assert.equal(wakeAt(0).mouth_opacity,0);assert.equal(wakeAt(4300).left_upper,-32);assert.equal(wakeAt(4300).mouth_opacity,0);assert.ok(wakeAt(2500).left_upper < -40);assert.equal(wakeAt(8000).gaze_y,0);assert.equal(wakeAt(8000).mouth_opacity,1);
});
function reachName(){const f=new Flow();f.next();f.next();for(let i=0;i<100;i++)f.tick(1000);return f;}
test('完整流程拍照只发生一次，重播可重新开始',()=>{
 const f=reachName();assert.equal(f.state,'name');f.input('我叫小明');f.tick(30000);assert.equal(f.state,'consent');f.input('可以');assert.equal(f.state,'photo');f.input('可以');f.tick(3500);assert.equal(f.captures,1);f.tick(30000);assert.equal(f.state,'chat');f.tick(30000);assert.equal(f.captures,1);f.reset();assert.equal(f.captures,0);
});
test('纪念卡在原始帧的闪光时刻出现',()=>{const f=reachName();f.input('小明');f.tick(30000);f.input('可以');f.tick(2499);assert.equal(f.captures,0);f.tick(1);assert.equal(f.captures,1);});
test('否定和模糊许可不拍照，文字按纯文本保留',()=>{
 const f=reachName();f.input('我叫<script>');f.tick(30000);f.input('可以不拍吗');assert.equal(f.captures,0);assert.notEqual(f.state,'photo');f.tick(30000);assert.equal(f.state,'chat');
 const g=reachName();g.input('小明');g.tick(30000);g.input('随便');assert.equal(g.state,'clarify');g.tick(30000);assert.equal(g.state,'consent');
});
