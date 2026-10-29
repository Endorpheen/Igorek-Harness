'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {ChatGPTWorkspace}=require('../chatgpt-workspace.cjs');
async function layout(rect){
 const state={visible:null,bounds:null};
 const workspace={layoutSequence:0,blocked:false,attached:true,overlayActive:()=>false,
  parent:{isDestroyed:()=>false,getContentSize:()=>[1100,627],webContents:{executeJavaScript:async()=>rect}},
  view:{webContents:{isDestroyed:()=>false},setVisible:value=>{state.visible=value;},setBounds:value=>{state.bounds=value;}}};
 await ChatGPTWorkspace.prototype.layout.call(workspace);return state;
}
test('the embedded view stays inside the scrolled surface and above the console',async()=>{
 const result=await layout({x:0,y:-113.5625,width:1085,height:539.9375});
 assert.deepEqual(result,{visible:true,bounds:{x:0,y:36,width:1085,height:390}});
 const left=await layout({x:-30,y:80,width:100,height:200});
 assert.deepEqual(left,{visible:true,bounds:{x:0,y:80,width:70,height:200}});
});
test('surfaces outside the visible parent region hide the native view',async()=>{
 for(const rect of [{x:0,y:-400,width:300,height:200},{x:-400,y:50,width:200,height:200},{x:1200,y:50,width:200,height:200}]){
  assert.deepEqual(await layout(rect),{visible:false,bounds:null});
 }
});
