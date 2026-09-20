/* 求职智能体 · 岗位需求三层图谱
 *
 * 与知识宇宙（graph3d.js）同一套技术选型（three.js + InstancedMesh + HTML 标签层），
 * 但布局不一样：这里是**确定性的分层旭日图**（sunburst），不是力导向。
 * 理由——岗位需求树本身就是一棵严格的树（job -> requirement* -> kp），
 * 力导向擅长表达"关系网"，而这里要表达的是"一层层拆解下去"，
 * 分层旭日图能保证同一份数据每次打开角度完全一致，演示时不会"越跑越乱"。
 *
 * 第一层（岗位）在圆心，第二层（需求分解树，可多级）向外一圈圈铺开，
 * 最外一圈是第三层（知识点），颜色是掌握度（与知识宇宙同一套色阶），
 * 半径外扩由内向外体现"越来越具体"。
 */
import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';
import { masteryColor as mcolor } from '/kg-core.js';

const C = {
  bg: 0x070b14,
  edge: 0x2b3a55,
  job: 0xffd54a,
  neutral: 0x5a6473,
};

function fitColor(v) {
  if (v === null || v === undefined) return new THREE.Color(C.neutral);
  const c = mcolor(v);
  return new THREE.Color(c[0], c[1], c[2]);
}

const RING_STEP = 46;   // 每一层向外扩多少
const Y_STEP = -34;     // 每一层向下沉多少（视觉上像一层层剖面图）

/** 把 job -> requirement 树 -> kp 铺成分层旭日图坐标。 */
function layoutSunburst(nodes, edges) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const children = new Map();
  for (const [a, b] of edges) {
    if (!children.has(a)) children.set(a, []);
    children.get(a).push(b);
  }
  // 叶子权重：知识点节点权重 1，其余节点权重 = 子节点权重之和（至少 1）
  const weight = new Map();
  function weightOf(id) {
    if (weight.has(id)) return weight.get(id);
    const kids = children.get(id) || [];
    const w = kids.length ? kids.reduce((s, k) => s + weightOf(k), 0) : 1;
    weight.set(id, w);
    return w;
  }
  weightOf('job');

  const pos = new Map();
  pos.set('job', { x: 0, y: 0, z: 0, angle: 0 });

  function place(id, a0, a1) {
    const kids = children.get(id) || [];
    if (!kids.length) return;
    let cursor = a0;
    for (const kid of kids) {
      const span = (a1 - a0) * (weightOf(kid) / weightOf(id));
      const mid = cursor + span / 2;
      const node = byId.get(kid);
      const r = (node.layer || 1) * RING_STEP;
      pos.set(kid, {
        x: Math.cos(mid) * r,
        y: (node.layer || 1) * Y_STEP,
        z: Math.sin(mid) * r,
        angle: mid,
      });
      place(kid, cursor, cursor + span);
      cursor += span;
    }
  }
  place('job', 0, Math.PI * 2);
  return pos;
}

export function mountCareer(host, opts) {
  const { data, onSelect } = opts;
  const nodes = data.nodes, edges = data.edges;
  const hasStudent = data.student_id !== null && data.student_id !== undefined;

  host.innerHTML = '';
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  const w = () => host.clientWidth || 600, hgt = () => host.clientHeight || 480;
  renderer.setSize(w(), hgt());
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(C.bg);
  const camera = new THREE.PerspectiveCamera(55, w() / hgt(), 0.1, 3000);
  camera.position.set(0, 140, 260);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  const pos = layoutSunburst(nodes, edges);
  const byId = new Map(nodes.map((n) => [n.id, n]));

  // 边
  const edgeGeom = new THREE.BufferGeometry();
  const edgePts = [];
  for (const [a, b] of edges) {
    const pa = pos.get(a), pb = pos.get(b);
    if (!pa || !pb) continue;
    edgePts.push(pa.x, pa.y, pa.z, pb.x, pb.y, pb.z);
  }
  edgeGeom.setAttribute('position', new THREE.Float32BufferAttribute(edgePts, 3));
  scene.add(new THREE.LineSegments(
    edgeGeom, new THREE.LineBasicMaterial({ color: C.edge, transparent: true, opacity: 0.55 })));

  // 节点
  const meshes = new Map();
  const labelHost = document.createElement('div');
  labelHost.className = 'career3d-labels';
  labelHost.style.cssText = 'position:absolute;inset:0;pointer-events:none;overflow:hidden';
  host.style.position = 'relative';
  host.appendChild(labelHost);
  const labelEls = new Map();

  for (const n of nodes) {
    const p = pos.get(n.id);
    if (!p) continue;
    const isJob = n.layer === 0;
    const isKp = n.id.startsWith('kp:');
    const radius = isJob ? 7 : isKp ? 2.6 : 3.6 + Math.min(3, (n.weight || 0.5) * 4);
    const color = isJob ? new THREE.Color(C.job)
      : isKp ? (hasStudent ? fitColor(n.retained) : new THREE.Color(C.neutral))
      : (hasStudent ? fitColor(n.fit ?? null) : new THREE.Color(C.neutral));
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(radius, 18, 14),
      new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.25 }));
    mesh.position.set(p.x, p.y, p.z);
    mesh.userData = n;
    scene.add(mesh);
    meshes.set(n.id, mesh);

    const label = document.createElement('div');
    label.className = 'career3d-label' + (isJob ? ' job' : isKp ? ' kp' : ' req');
    label.textContent = n.name;
    label.style.cssText = 'position:absolute;transform:translate(-50%,-140%);'
      + 'font-size:11px;color:#dbe6ff;white-space:nowrap;text-shadow:0 1px 2px #000;';
    if (isJob) label.style.fontSize = '14px';
    labelHost.appendChild(label);
    labelEls.set(n.id, label);
  }

  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const dl = new THREE.DirectionalLight(0xffffff, 0.6);
  dl.position.set(80, 160, 120);
  scene.add(dl);

  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();
  function onClick(ev) {
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    const hit = raycaster.intersectObjects([...meshes.values()])[0];
    if (hit && onSelect) onSelect(hit.object.userData);
  }
  renderer.domElement.addEventListener('click', onClick);

  const proj = new THREE.Vector3();
  let alive = true;
  function frame() {
    if (!alive) return;
    controls.update();
    for (const [id, mesh] of meshes) {
      proj.copy(mesh.position).project(camera);
      const el = labelEls.get(id);
      if (!el) continue;
      const behind = proj.z > 1;
      el.style.display = behind ? 'none' : 'block';
      el.style.left = `${(proj.x * 0.5 + 0.5) * w()}px`;
      el.style.top = `${(-proj.y * 0.5 + 0.5) * hgt()}px`;
    }
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  frame();

  function onResize() {
    renderer.setSize(w(), hgt());
    camera.aspect = w() / hgt();
    camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', onResize);

  return {
    destroy() {
      alive = false;
      window.removeEventListener('resize', onResize);
      renderer.domElement.removeEventListener('click', onClick);
      renderer.dispose();
      host.innerHTML = '';
    },
  };
}
