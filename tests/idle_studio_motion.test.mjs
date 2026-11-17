import test from 'node:test';
import assert from 'node:assert/strict';
import {motionTrack, poseAt} from '../src/watcherobot/idle_studio/web/motion.mjs';
test('official keyframes use fps, linear interpolation and hold final pose',()=>{
 const track=motionTrack({fps:10,frame_start:0,frame_end:19,animated_objects:[{object_name:'body_x',keyframe_data:[{frame_number:0,rotation_angle:90},{frame_number:10,rotation_angle:120}]}]});
 assert.equal(track.duration,2000);assert.equal(poseAt(track,500).x,105);
 assert.equal(poseAt(track,1900).x,120);assert.equal(poseAt(track,500).y,120);
});
test('no motion is neutral and invalid fps fails clearly',()=>{
 assert.deepEqual(poseAt(null,100),{x:90,y:100});
 assert.throws(()=>motionTrack({fps:0}),/动作/);
});

test('sparse axes hold at other axis keys before moving to next target',()=>{
 const track=motionTrack({fps:10,frame_start:0,frame_end:10,animated_objects:[
 {object_name:'底部轴',keyframe_data:[{frame_number:0,rotation_angle:90},{frame_number:10,rotation_angle:130}]},
 {object_name:'头轴',keyframe_data:[{frame_number:0,rotation_angle:120},{frame_number:5,rotation_angle:140}]}]});
 assert.equal(poseAt(track,250).x,90);assert.equal(poseAt(track,750).x,110);
 assert.equal(poseAt(track,500).y,140);
});
