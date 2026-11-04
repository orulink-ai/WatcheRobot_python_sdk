const {test}=require('node:test');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
test('离线浏览器：完整流程、失败、暂停、重播、输入安全与响应布局',async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(['error','warning'].includes(m.type()))errors.push(m.text());});
  page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await page.goto(pathToFileURL(path.join(__dirname,'Watcher-初见体验.html')).href);
  await page.locator('#loading').waitFor({state:'hidden'});
  const visual=await page.evaluate(()=>{
   flow.enter('hello');flow.paused=true;smile=.72;flow.clock=1000;flow.elapsed=0;draw(0);
   const pixel=Array.from(ctx.getImageData(131,206,1,1).data);
   const radius=getComputedStyle(el('screen')).borderRadius;
   reset();return {pixel,radius};
  });
  assert.ok(visual.pixel[1]>200,'微笑眼睛不能被旧的黑色瞳孔横向切断');
  assert.equal(visual.radius,'50%','机器人屏幕必须为圆形');
  await page.clock.install();
  await page.getByRole('button',{name:'模拟手机扫码',exact:true}).click();
  await page.clock.runFor(20);
  await page.getByRole('button',{name:'模拟输入失败',exact:true}).click();
  await page.clock.runFor(20);
  assert.equal(await page.locator('#title').textContent(),'连接没有成功');
  await page.getByRole('button',{name:'返回输入密码',exact:true}).click();
  await page.clock.runFor(20);
  await page.getByRole('button',{name:'输入成功，连接网络',exact:true}).click();
  await page.clock.runFor(2000);
  await page.locator('#pause').click();await page.clock.runFor(20);
  const paused=await page.evaluate(()=>flow.elapsed);
  await page.clock.runFor(1000);assert.equal(await page.evaluate(()=>flow.elapsed),paused);
  await page.locator('#pause').click();await page.clock.runFor(60000);
  assert.equal(await page.evaluate(()=>flow.state),'name');
  await page.locator('#input').fill('我叫<img src=x onerror=alert(1)>');
  await page.locator('#send').click();await page.clock.runFor(20000);
  assert.equal(await page.locator('#transcript img').count(),0);
  await page.getByRole('button',{name:'可以',exact:true}).click();
  await page.clock.runFor(2000);assert.equal(await page.evaluate(()=>flow.captures),0);
  await page.clock.runFor(600);assert.equal(await page.evaluate(()=>flow.captures),1);
  await page.clock.runFor(20000);assert.equal(await page.evaluate(()=>flow.state),'chat');
  assert.equal(await page.evaluate(()=>flow.captures),1);
  await page.getByRole('button',{name:'查看日常开机'}).click();await page.clock.runFor(20);
  assert.equal(await page.evaluate(()=>flow.state),'daily');
  for(const width of [390,768,1440]){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
  await page.locator('#reset').click();await page.clock.runFor(20);
  assert.deepEqual(await page.evaluate(()=>[flow.state,flow.captures,flow.messages.length]),['scan',0,0]);
  await page.getByRole('button',{name:'自动演示全程'}).click();await page.clock.runFor(120000);
  assert.deepEqual(await page.evaluate(()=>[flow.state,flow.captures]),['chat',1]);
  assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
 }finally{await browser.close();}
});
