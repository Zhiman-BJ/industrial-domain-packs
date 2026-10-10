import * as THREE from 'three';
import {OrbitControls} from './vendor/controls/OrbitControls.js';
import {GLTFLoader} from './vendor/loaders/GLTFLoader.js';

const status=document.getElementById('status');
let renderer,controls,scene,camera,radius=1;
function fit(){
  const vertical=THREE.MathUtils.degToRad(camera.fov/2);
  const horizontal=Math.atan(Math.tan(vertical)*camera.aspect);
  const distance=radius/Math.sin(Math.min(vertical,horizontal))*1.12;
  camera.position.copy(new THREE.Vector3(.45,1,.75).normalize().multiplyScalar(distance));
  // Board and copper faces are only micrometres apart. A near plane
  // 10,000 times smaller than the assembly causes visible depth artefacts.
  camera.near=distance/100;camera.far=distance*20;camera.updateProjectionMatrix();
  controls.target.set(0,0,0);controls.update();
}
try{
  const source=new URLSearchParams(location.search).get('model');
  if(!source)throw Error('本步尚无三维模型');
  const url=new URL(source,location.href);
  if(url.origin!==location.origin||!url.pathname.endsWith('.glb'))throw Error('仅加载本站导出的 GLB');
  renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));
  renderer.setClearColor(0x070c14);document.body.append(renderer.domElement);
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(40,innerWidth/innerHeight,.001,1000);
  const studio=new THREE.Scene();studio.background=new THREE.Color(0xcbd5df);
  const pmrem=new THREE.PMREMGenerator(renderer);scene.environment=pmrem.fromScene(studio).texture;pmrem.dispose();
  scene.add(new THREE.HemisphereLight(0xffffff,0x73849c,2.8));
  for(const pos of [[1,2,3],[-2,-1,-1]]){const light=new THREE.DirectionalLight(0xffffff,2.5);light.position.set(...pos);scene.add(light);}
  controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.autoRotate=true;controls.autoRotateSpeed=1.1;renderer.domElement.addEventListener('pointerdown',()=>controls.autoRotate=false,{once:true});
  const loader=new GLTFLoader();
  // Native KiCad GLB embeds its geometry. Reject unexpected external resources.
  loader.manager.setURLModifier(value=>{
    if(value.startsWith('blob:')||value.startsWith('data:'))return value;
    const resolved=new URL(value,location.href);if(resolved.href!==url.href)throw Error('GLB 含未绑定的外部资源');return resolved.href;
  });
  const result=await loader.loadAsync(url.href);
  const box=new THREE.Box3().setFromObject(result.scene);if(box.isEmpty())throw Error('三维文件中没有可显示的几何体');
  const center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3());radius=size.length()/2;
  result.scene.position.sub(center);scene.add(result.scene);fit();
  const resize=()=>{renderer.setSize(innerWidth,innerHeight);camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();};
  resize();addEventListener('resize',resize);document.getElementById('fit').onclick=fit;
  renderer.setAnimationLoop(()=>{controls.update();renderer.render(scene,camera);});
  status.textContent='三维模型已加载';document.body.dataset.loaded='true';
}catch(error){status.textContent='三维视图不可用：'+error.message;document.body.dataset.error=error.message;renderer?.dispose();}
