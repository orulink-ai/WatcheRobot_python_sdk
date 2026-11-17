import * as THREE from 'three';
import {GLTFLoader} from './vendor/GLTFLoader.js';
import {OrbitControls} from './vendor/OrbitControls.js';
import {motionTrack,poseAt} from './motion.mjs';

// Model/material contract reused from desktop studioScene.ts (f51052ba).
export class ModelPreview {
 constructor(host) {
  this.host=host;this.canvas=document.createElement('canvas');
  this.canvas.width=this.canvas.height=240;this.context=this.canvas.getContext('2d');
  this.texture=new THREE.CanvasTexture(this.canvas);this.texture.flipY=false;
  this.texture.colorSpace=THREE.SRGBColorSpace;this.texture.generateMipmaps=false;
  this.texture.minFilter=THREE.LinearFilter;this.texture.magFilter=THREE.LinearFilter;
  this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
  this.renderer.setPixelRatio(Math.min(devicePixelRatio,2));host.replaceChildren(this.renderer.domElement);
  this.scene=new THREE.Scene();this.camera=new THREE.PerspectiveCamera(27,1,.01,100);
  this.camera.position.set(0,1.05,-5.3);
  this.controls=new OrbitControls(this.camera,this.renderer.domElement);
  this.controls.target.set(0,.8,0);this.controls.enablePan=false;
  this.controls.minDistance=3;this.controls.maxDistance=6;this.controls.update();
  this.scene.add(new THREE.HemisphereLight(0xffffff,0x66756a,3));
  const light=new THREE.DirectionalLight(0xffffff,3);light.position.set(-3,5,-4);this.scene.add(light);
  this.clear();
  this.ready=new GLTFLoader().loadAsync('/vendor/watcherobot-studio.glb').then(gltf=>{
   const root=gltf.scene;
   this.base=root.getObjectByName('底部轴');this.head=root.getObjectByName('头轴');
   this.baseRest=this.base?.quaternion.clone();this.headRest=this.head?.quaternion.clone();
   root.traverse(object=>{
    if(!object.isMesh)return;
    const materials=Array.isArray(object.material)?object.material:[object.material];
    if(materials.some(m=>m.name.includes('玻璃_2'))||['实体1_9','实体3'].includes(object.name)){
     object.material=new THREE.MeshBasicMaterial({map:this.texture,toneMapped:false});
    }else materials.forEach(m=>{if(m.isMeshStandardMaterial){m.roughness=.58;m.metalness=.02;}});
   });
   const box=new THREE.Box3().setFromObject(root),size=box.getSize(new THREE.Vector3()),center=box.getCenter(new THREE.Vector3());
   root.position.set(-center.x,-box.min.y,-center.z);
   const group=new THREE.Group();group.scale.setScalar(1.7/Math.max(size.x,size.y,size.z));group.add(root);this.scene.add(group);
  });
  this.observer=new ResizeObserver(()=>{const w=host.clientWidth,h=host.clientHeight;if(!w||!h)return;this.renderer.setSize(w,h);this.camera.aspect=w/h;this.camera.updateProjectionMatrix();});this.observer.observe(host);
  this.renderer.setAnimationLoop(time=>{
   if(!host.getClientRects().length)return;
   if(this.sheet&&this.once&&time-this.started>=this.cycle){this.once=false;this.finished=true;const done=this.onComplete;this.onComplete=null;done?.();}
   if(this.sheet){const clock=this.finished?this.cycle:time-this.started;this.pose(poseAt(this.motion,clock));let elapsed=this.once||this.finished?Math.min(clock,this.total-1):clock%this.total,index=0;while(index<this.data.durations.length-1&&elapsed>=this.data.durations[index])elapsed-=this.data.durations[index++];
    if(index!==this.frame){const d=this.data;this.context.drawImage(this.sheet,index%d.columns*d.width,Math.floor(index/d.columns)*d.height,d.width,d.height,0,0,this.canvas.width,this.canvas.height);this.texture.needsUpdate=true;this.frame=index;}}
   this.renderer.render(this.scene,this.camera);
  });
 }
 pose({x,y}){
  if(this.base)this.base.quaternion.copy(this.baseRest).multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),THREE.MathUtils.degToRad(x-90)));
  if(this.head)this.head.quaternion.copy(this.headRest).multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),THREE.MathUtils.degToRad(98-Math.min(140,Math.max(100,y)))));
 }
 unlockAudio(){this.audioContext??=new AudioContext();return this.audioContext.resume();}
 clear(){this.audioSource?.stop();this.audioSource=null;this.onComplete=null;this.once=false;this.finished=false;this.serial=(this.serial||0)+1;this.sheet=null;this.context.fillStyle='#090e0c';this.context.fillRect(0,0,240,240);this.texture.needsUpdate=true;}
 async prepare(data,action=null,sound=null){
  const image=new Image();image.src=data.url;
  const [audio]=await Promise.all([sound?this.audioContext.decodeAudioData(sound.slice(0)):null,image.decode(),this.ready]);
  const motion=motionTrack(action),total=data.durations.reduce((a,b)=>a+b,0);
  return {data,image,audio,motion,total,cycle:Math.max(total,motion?.duration||0,(audio?.duration||0)*1000),bytes:image.naturalWidth*image.naturalHeight*4+(audio?audio.length*audio.numberOfChannels*4:0)};
 }
 start(prepared,once=false){
  this.audioSource?.stop();this.audioSource=null;this.onComplete=null;this.serial++;
  const {data,image,audio,motion,total,cycle}=prepared;
  this.finished=false;this.motion=motion;this.once=once;this.data=data;this.sheet=image;this.total=total;this.cycle=cycle;
  this.started=performance.now();this.frame=-1;
  // Draw the first frame immediately; never expose a cleared texture between clips.
  this.context.drawImage(image,0,0,data.width,data.height,0,0,this.canvas.width,this.canvas.height);
  this.texture.needsUpdate=true;this.pose(poseAt(motion,0));
  if(audio){this.audioSource=this.audioContext.createBufferSource();this.audioSource.buffer=audio;this.audioSource.connect(this.audioContext.destination);this.audioSource.start();}
 }
 async play(data,once=false,action=null,sound=null){
  const serial=++this.serial,prepared=await this.prepare(data,action,sound);
  if(serial===this.serial)this.start(prepared,once);
 }
}
