/* 知识宇宙 · 3D 知识图谱
 *
 * 技术取舍（参考美团《知识图谱可视化技术在美团的实践与探索》）：
 *  - 渲染：WebGL / three.js。节点用 InstancedMesh、边用单个 LineSegments，
 *    183 个知识点只占两个 draw call；上千节点也不会掉帧。
 *  - 布局：自研 3D 力导向（斥力 + 弹簧 + 向心），叠加两种约束力——
 *    分层约束（Y = 依赖深度）与聚类约束（ClusterCenter / Strength / Radius），
 *    对应文中"层次聚类布局"与"分层布局"两类做法。
 *  - 文字：不做纹理贴图，改用 HTML 覆盖层。中文在 sprite 纹理上会糊，
 *    而 HTML 层既清晰又便于做遮挡与降噪（同一时刻最多显示 N 个标签）。
 *  - 降噪：默认只显示关键标签，聚焦时才展开——"从海量数据里呈现有效信息"，
 *    而不是把所有元素一次糊到屏幕上。
 *
 * 一条教学上的坚持：Y 轴是**依赖深度**，前置在下、后继在上。
 * 但这只是可视化的分层，不是教学顺序——按拓扑序给全员排课是本项目明确废弃的做法。
 */
import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';
import { Layout, labelPriority, masteryColor as mcolor, nodeRadius, visibleMask }
  from '/kg-core.js';

const LAYOUTS = [
  { id: 'depend', name: '依赖分层', hint: 'Y 轴为依赖深度：前置在下、后继在上' },
  { id: 'cluster', name: '章节星系', hint: '按章节聚成星系，看课程的宏观结构' },
  { id: 'force', name: '自由力导向', hint: '只看连接关系本身' },
  { id: 'sphere', name: '知识球面', hint: '按章节切分球面扇区' },
];

const C = {
  bg: 0x070b14,
  edge: 0x2b3a55,
  edgeHi: 0x4da3ff,
  dim: 0.12,
  unaware: 0x5a6473,      // 未选学生时的中性色
};

/* 掌握度色阶来自 kg-core.js（与 2D 热力图共用同一套语言） */
function masteryColor(v) {
  const c = mcolor(v);
  return new THREE.Color(c[0], c[1], c[2]);
}

function unitColor(idx, total) {
  return new THREE.Color().setHSL((idx / Math.max(1, total)) * 0.85, 0.55, 0.55);
}

// ---------------------------------------------------------------- 主视图
export function mountUniverse(host, opts) {
  const { data, onSelect, onAsk } = opts;
  const nodes = data.nodes, edges = data.edges;
  const hasStudent = data.student_id !== null && data.student_id !== undefined;

  host.innerHTML = '';
  host.style.position = 'relative';
  const canvasBox = document.createElement('div');
  canvasBox.className = 'kg-canvas';
  host.append(canvasBox);

  // ---- three.js 基础 ----
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(C.bg);
  scene.fog = new THREE.FogExp2(C.bg, 0.0022);   // 密度每帧按相机距离自适应

  const camera = new THREE.PerspectiveCamera(52, 1, 0.5, 3000);
  camera.position.set(0, 40, 300);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  canvasBox.append(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.rotateSpeed = 0.6;
  controls.minDistance = 30;
  controls.maxDistance = 900;
  controls.autoRotateSpeed = 0.45;

  // 打光偏"均匀"而非"戏剧"：颜色编码的是掌握度，不能被阴影改写
  scene.add(new THREE.AmbientLight(0xffffff, 1.5));
  scene.add(new THREE.HemisphereLight(0xdfeaff, 0x223047, 1.0));
  const key = new THREE.DirectionalLight(0xdcebff, 1.1);
  key.position.set(80, 140, 120);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x9ec2ff, 0.6);
  fill.position.set(-120, -60, -90);
  scene.add(fill);

  // 星空：给"知识宇宙"一点氛围，同时提供旋转时的空间参照
  {
    const g = new THREE.BufferGeometry();
    const N = 1400, arr = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const r = 700 + Math.random() * 900;
      const th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
      arr[i * 3] = r * Math.sin(ph) * Math.cos(th);
      arr[i * 3 + 1] = r * Math.cos(ph);
      arr[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
    }
    g.setAttribute('position', new THREE.BufferAttribute(arr, 3));
    scene.add(new THREE.Points(g, new THREE.PointsMaterial({
      color: 0x8fa6c8, size: 1.4, sizeAttenuation: true, transparent: true, opacity: 0.5,
    })));
  }

  // ---- 节点：InstancedMesh ----
  const layout = new Layout(nodes, edges, data.max_depth, data.units.length);
  const N = nodes.length;
  const sphereGeo = new THREE.IcosahedronGeometry(1, 2);
  const nodeMat = new THREE.MeshStandardMaterial({ roughness: 0.55, metalness: 0.0 });
  const mesh = new THREE.InstancedMesh(sphereGeo, nodeMat, N);
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  mesh.count = N;
  scene.add(mesh);

  const baseColor = [], radius = [];
  for (let i = 0; i < N; i++) {
    const d = nodes[i];
    baseColor.push(hasStudent ? masteryColor(d.mastery)
      : unitColor(d.unit_idx, data.units.length));
    // 尺寸编码挡路度：挡住的越多，看上去越"重"
    radius.push(nodeRadius(d.severity));
  }

  // 光环：待复检（琥珀色脉冲） / 已验证掌握（青绿描边）
  const ringGeo = new THREE.TorusGeometry(1, 0.09, 8, 32);
  const dueMat = new THREE.MeshBasicMaterial({ color: 0xe3a008, transparent: true });
  const okMat = new THREE.MeshBasicMaterial({ color: 0x2ecc71, transparent: true,
    opacity: 0.85 });
  const dueIdx = [], okIdx = [];
  nodes.forEach((d, i) => { if (d.due) dueIdx.push(i); else if (d.validated) okIdx.push(i); });
  const dueMesh = new THREE.InstancedMesh(ringGeo, dueMat, Math.max(1, dueIdx.length));
  const okMesh = new THREE.InstancedMesh(ringGeo, okMat, Math.max(1, okIdx.length));
  dueMesh.count = dueIdx.length; okMesh.count = okIdx.length;
  scene.add(dueMesh, okMesh);

  // ---- 边：单个 LineSegments ----
  const linkPairs = layout.links;
  const edgeGeo = new THREE.BufferGeometry();
  const edgePos = new Float32Array(linkPairs.length * 6);
  const edgeCol = new Float32Array(linkPairs.length * 6);
  edgeGeo.setAttribute('position', new THREE.BufferAttribute(edgePos, 3));
  edgeGeo.setAttribute('color', new THREE.BufferAttribute(edgeCol, 3));
  const edgeMesh = new THREE.LineSegments(edgeGeo, new THREE.LineBasicMaterial({
    vertexColors: true, transparent: true, opacity: 0.8,
  }));
  scene.add(edgeMesh);

  // ---- 状态 ----
  const S = {
    selected: null, hovered: null, focusSet: null, pathSet: null,
    filterUnits: new Set(), onlyGap: false, onlyDue: false, query: '',
    labelCap: 22, autoRotate: false, mode: 'depend',
  };
  const neighbors = new Map(nodes.map((d) => [d.id, new Set()]));
  for (const [a, b] of edges) {
    neighbors.get(a)?.add(b);
    neighbors.get(b)?.add(a);
  }

  const visible = () => visibleMask(nodes, {
    units: S.filterUnits, onlyGap: hasStudent && S.onlyGap,
    onlyDue: S.onlyDue, query: S.query,
  }, data.threshold);

  // ---- 每帧更新实例矩阵与颜色 ----
  const dummy = new THREE.Object3D();
  const tmpColor = new THREE.Color();
  let vis = visible();

  function updateInstances(t) {
    const p = layout.pos;
    for (let i = 0; i < N; i++) {
      const d = nodes[i];
      const on = vis[i];
      let r = radius[i];
      let dim = 1;
      if (S.focusSet && !S.focusSet.has(d.id)) dim = C.dim;
      if (!on) dim = 0.05;
      if (S.pathSet && S.pathSet.has(d.id)) { r *= 1.5; dim = 1; }
      if (S.selected === d.id) r *= 1.75;
      if (S.hovered === d.id) r *= 1.35;
      dummy.position.set(p[i * 3], p[i * 3 + 1], p[i * 3 + 2]);
      dummy.scale.setScalar(on || S.pathSet?.has(d.id) ? r : r * 0.35);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);

      tmpColor.copy(baseColor[i]);
      if (S.pathSet && S.pathSet.has(d.id)) tmpColor.lerp(new THREE.Color(0x4da3ff), 0.55);
      if (dim < 1) tmpColor.multiplyScalar(0.25 + dim);
      if (S.selected === d.id || S.hovered === d.id) tmpColor.offsetHSL(0, 0.12, 0.14);
      mesh.setColorAt(i, tmpColor);
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;

    // 光环跟随
    const pulse = 1 + Math.sin(t * 0.004) * 0.16;
    dueIdx.forEach((i, k) => {
      dummy.position.set(p[i * 3], p[i * 3 + 1], p[i * 3 + 2]);
      dummy.scale.setScalar(radius[i] * 1.85 * pulse);
      dummy.quaternion.copy(camera.quaternion);
      dummy.updateMatrix();
      dueMesh.setMatrixAt(k, dummy.matrix);
    });
    okIdx.forEach((i, k) => {
      dummy.position.set(p[i * 3], p[i * 3 + 1], p[i * 3 + 2]);
      dummy.scale.setScalar(radius[i] * 1.5);
      dummy.quaternion.copy(camera.quaternion);
      dummy.updateMatrix();
      okMesh.setMatrixAt(k, dummy.matrix);
    });
    dueMesh.instanceMatrix.needsUpdate = true;
    okMesh.instanceMatrix.needsUpdate = true;
    dueMat.opacity = 0.35 + Math.sin(t * 0.004) * 0.2;

    // 边
    const cA = new THREE.Color(C.edge), cB = new THREE.Color(C.edgeHi);
    linkPairs.forEach(([a, b], k) => {
      edgePos[k * 6] = p[a * 3]; edgePos[k * 6 + 1] = p[a * 3 + 1];
      edgePos[k * 6 + 2] = p[a * 3 + 2];
      edgePos[k * 6 + 3] = p[b * 3]; edgePos[k * 6 + 4] = p[b * 3 + 1];
      edgePos[k * 6 + 5] = p[b * 3 + 2];
      const inPath = S.pathSet && S.pathSet.has(nodes[a].id) && S.pathSet.has(nodes[b].id);
      const inFocus = !S.focusSet
        || (S.focusSet.has(nodes[a].id) && S.focusSet.has(nodes[b].id));
      const c = inPath ? cB : cA;
      const f = inPath ? 1 : inFocus ? 0.75 : 0.12;
      for (const off of [0, 3]) {
        edgeCol[k * 6 + off] = c.r * f;
        edgeCol[k * 6 + off + 1] = c.g * f;
        edgeCol[k * 6 + off + 2] = c.b * f;
      }
    });
    edgeGeo.attributes.position.needsUpdate = true;
    edgeGeo.attributes.color.needsUpdate = true;
  }

  // ---- HTML 标签层（中文清晰 + 可控降噪）----
  const labelLayer = document.createElement('div');
  labelLayer.className = 'kg-labels';
  canvasBox.append(labelLayer);
  const labelPool = [];
  const v3 = new THREE.Vector3();

  function updateLabels() {
    // 降噪见 kg-core.labelPriority：全打上去等于什么都没说
    const take = labelPriority(nodes, vis, S, S.labelCap);
    while (labelPool.length < take.length) {
      const el = document.createElement('div');
      el.className = 'kg-label';
      labelLayer.append(el);
      labelPool.push(el);
    }
    const w = canvasBox.clientWidth, h = canvasBox.clientHeight;
    // 屏幕空间避让：按优先级贪心放置，与已放置标签相交的直接不显示。
    // 文字互相压住时，"显示了很多标签"等于"一个都没读到"。
    const placed = [];
    labelPool.forEach((el, k) => {
      const i = take[k];
      if (i === undefined) { el.style.display = 'none'; return; }
      const d = nodes[i];
      v3.set(layout.pos[i * 3], layout.pos[i * 3 + 1], layout.pos[i * 3 + 2]);
      v3.project(camera);
      if (v3.z > 1) { el.style.display = 'none'; return; }
      const x = (v3.x * 0.5 + 0.5) * w, y = (-v3.y * 0.5 + 0.5) * h;
      const halfW = d.name.length * 6.5 + 6, halfH = 10;
      const must = S.selected === d.id || S.hovered === d.id || S.pathSet?.has(d.id);
      const hit = placed.some((r) =>
        Math.abs(r.x - x) < r.halfW + halfW && Math.abs(r.y - y) < r.halfH + halfH);
      if (hit && !must) { el.style.display = 'none'; return; }
      placed.push({ x, y, halfW, halfH });
      el.style.display = 'block';
      el.textContent = d.name;
      el.className = 'kg-label' + (S.selected === d.id ? ' sel' : '')
        + (S.pathSet?.has(d.id) ? ' path' : '');
      el.style.transform = `translate(-50%,-50%) translate(${x}px,${y}px)`;
    });
  }

  // ---- 拾取 ----
  const ray = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let pointerInside = false;

  function pick(ev) {
    const r = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - r.left) / r.width) * 2 - 1;
    pointer.y = -((ev.clientY - r.top) / r.height) * 2 + 1;
    ray.setFromCamera(pointer, camera);
    const hit = ray.intersectObject(mesh, false)[0];
    if (!hit || hit.instanceId === undefined) return null;
    return vis[hit.instanceId] ? nodes[hit.instanceId] : null;
  }

  renderer.domElement.addEventListener('pointermove', (ev) => {
    pointerInside = true;
    const d = pick(ev);
    S.hovered = d ? d.id : null;
    renderer.domElement.style.cursor = d ? 'pointer' : 'grab';
  });
  renderer.domElement.addEventListener('pointerleave', () => {
    pointerInside = false; S.hovered = null;
  });
  renderer.domElement.addEventListener('click', (ev) => {
    const d = pick(ev);
    select(d ? d.id : null);
  });
  renderer.domElement.addEventListener('dblclick', (ev) => {
    const d = pick(ev);
    if (d) flyTo(d.id);
  });

  function select(id) {
    S.selected = id;
    S.pathSet = null;
    S.focusSet = id === null ? null
      : new Set([id, ...(neighbors.get(id) || [])]);
    onSelect && onSelect(id === null ? null : nodes.find((d) => d.id === id));
    renderPanel();
  }

  // ---- 相机自适应 ----
  /* 力导向的展开尺度随节点数与模式变化，写死的相机位置必然不是撑爆就是太远。
     每次布局收敛（或切换模式）后按包围盒重新取景。 */
  let fly = null;
  function fitCamera(padding = 1.08, animate = true, subset = null) {
    const p = layout.pos;
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    let any = false;
    for (let i = 0; i < N; i++) {
      if (subset ? !subset.has(nodes[i].id) : !vis[i]) continue;
      any = true;
      minX = Math.min(minX, p[i * 3]); maxX = Math.max(maxX, p[i * 3]);
      minY = Math.min(minY, p[i * 3 + 1]); maxY = Math.max(maxY, p[i * 3 + 1]);
      minZ = Math.min(minZ, p[i * 3 + 2]); maxZ = Math.max(maxZ, p[i * 3 + 2]);
    }
    if (!any) return;
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, cz = (minZ + maxZ) / 2;
    // 依赖分层下图谱是一块"扁平的板"：用包围球半径会把相机推得太远，画面里只剩小点。
    // 正确做法是横竖分别算需要多远才装得下，取更受限的那个，再补上纵深的一半。
    const hx = Math.max((maxX - minX) / 2, 10);
    const hy = Math.max((maxY - minY) / 2, 10);
    const hz = Math.max((maxZ - minZ) / 2, 10);
    const fovV = (camera.fov * Math.PI) / 180;
    const fovH = 2 * Math.atan(Math.tan(fovV / 2) * camera.aspect);
    const horiz = Math.max(hx, hz);
    const dist = Math.max(horiz / Math.tan(fovH / 2), hy / Math.tan(fovV / 2))
      * padding + Math.min(hx, hz) * 0.5;
    const target = new THREE.Vector3(cx, cy, cz);
    const dir = new THREE.Vector3().subVectors(camera.position, controls.target);
    if (dir.lengthSq() < 1e-6) dir.set(0.35, 0.25, 1);
    dir.normalize().multiplyScalar(dist);
    if (!animate) {
      camera.position.copy(target).add(dir);
      controls.target.copy(target);
      return;
    }
    fly = {
      t: 0, fromCam: camera.position.clone(), toCam: target.clone().add(dir),
      fromTgt: controls.target.clone(), toTgt: target,
    };
  }

  // ---- 相机飞行 ----
  function flyTo(id) {
    const i = layout.index.get(id);
    if (i === undefined) return;
    const target = new THREE.Vector3(layout.pos[i * 3], layout.pos[i * 3 + 1],
      layout.pos[i * 3 + 2]);
    const dir = new THREE.Vector3().subVectors(camera.position, controls.target)
      .normalize().multiplyScalar(70);
    fly = {
      t: 0,
      fromCam: camera.position.clone(), toCam: target.clone().add(dir),
      fromTgt: controls.target.clone(), toTgt: target.clone(),
    };
  }

  /* 根因回溯：把那条链点亮并飞过去。这是 2D 表格给不了的体验——
     "他不是反向传播不会，是链式法则没通"这句话，在这里是看得见的一条光路。*/
  async function highlightRootCause(id) {
    if (!hasStudent) return;
    const r = await opts.fetchRootCause(id);
    if (!r || !r.path || r.path.length < 2) {
      S.pathSet = new Set([id]);
      flyTo(id);
    } else {
      S.pathSet = new Set(r.path);
      S.focusSet = new Set(r.path);
      // 框住**整条链**，而不是飞到根因那一个点上——
      // 要让人看见的是"从症状到病根这一路"，不是终点。
      fitCamera(1.6, true, S.pathSet);
    }
    renderPanel(r);
  }

  // ---- UI ----
  const ui = document.createElement('div');
  ui.className = 'kg-ui';
  host.append(ui);
  const panel = document.createElement('div');
  panel.className = 'kg-panel';
  host.append(panel);

  function renderUI() {
    ui.innerHTML = '';
    const bar = document.createElement('div');
    bar.className = 'kg-bar';
    for (const l of LAYOUTS) {
      const b = document.createElement('button');
      b.textContent = l.name;
      b.title = l.hint;
      b.className = S.mode === l.id ? 'on' : '';
      b.onclick = () => {
        S.mode = l.id; layout.setMode(l.id);
        fitted = midFitted = false;
        renderUI();
      };
      bar.append(b);
    }
    const fitBtn = document.createElement('button');
    fitBtn.textContent = '适应视图';
    fitBtn.title = '把当前可见的知识点全部装进画面';
    fitBtn.onclick = () => fitCamera();
    bar.append(fitBtn);
    const rot = document.createElement('button');
    rot.textContent = S.autoRotate ? '停止自转' : '自动旋转';
    rot.className = S.autoRotate ? 'on' : '';
    rot.onclick = () => { S.autoRotate = !S.autoRotate; renderUI(); };
    bar.append(rot);
    ui.append(bar);

    const row2 = document.createElement('div');
    row2.className = 'kg-bar';
    const q = document.createElement('input');
    q.placeholder = '搜知识点…';
    q.value = S.query;
    q.oninput = () => { S.query = q.value; vis = visible(); };
    q.onkeydown = (e) => {
      if (e.key === 'Enter') {
        const hit = nodes.find((d, i) => vis[i]);
        if (hit) { select(hit.id); flyTo(hit.id); }
      }
    };
    row2.append(q);

    if (hasStudent) {
      for (const [key, label, tip] of [
        ['onlyGap', '只看我的缺口', '掌握度低于阈值的知识点'],
        ['onlyDue', '只看待复检', '曾经达标，但按遗忘曲线已经掉下来了'],
      ]) {
        const b = document.createElement('button');
        b.textContent = label; b.title = tip;
        b.className = S[key] ? 'on' : '';
        b.onclick = () => { S[key] = !S[key]; vis = visible(); renderUI(); };
        row2.append(b);
      }
    }
    const sel = document.createElement('select');
    sel.innerHTML = '<option value="">全部章节</option>'
      + data.units.map((u) => `<option${S.filterUnits.has(u) ? ' selected' : ''}>${u}</option>`)
        .join('');
    sel.onchange = () => {
      S.filterUnits = sel.value ? new Set([sel.value]) : new Set();
      vis = visible();
    };
    row2.append(sel);

    const reset = document.createElement('button');
    reset.textContent = '重置视角';
    reset.onclick = () => {
      S.query = ''; S.filterUnits = new Set(); S.onlyGap = S.onlyDue = false;
      select(null); vis = visible(); renderUI();
      vis = visible(); fitCamera();
    };
    row2.append(reset);
    ui.append(row2);

    const legend = document.createElement('div');
    legend.className = 'kg-legend';
    legend.innerHTML = hasStudent
      ? `<span><i style="background:linear-gradient(90deg,#cf222e,#bf8700,#1a7f37)"></i>掌握度</span>
         <span><i class="ring due"></i>待复检</span>
         <span><i class="ring ok"></i>已验证掌握</span>
         <span class="muted">球体大小 = 挡路度</span>`
      : `<span class="muted">按章节着色 · 球体大小 = 挡路度 · 选一位学生可叠加掌握度</span>`;
    ui.append(legend);
  }

  function renderPanel(rc) {
    const d = nodes.find((x) => x.id === S.selected);
    if (!d) { panel.className = 'kg-panel'; panel.innerHTML = ''; return; }
    panel.className = 'kg-panel open';
    const m = d.mastery;
    panel.innerHTML = `
      <div class="kg-p-head">
        <div>
          <div class="kg-p-title">${d.name}</div>
          <div class="kg-p-sub">${d.code} · ${d.unit}</div>
        </div>
        <button class="kg-close">×</button>
      </div>
      <div class="kg-tags">
        <span class="tag">${d.type}</span>
        <span class="tag">依赖深度 ${d.depth}</span>
        <span class="tag">挡路度 ${d.severity}</span>
        <span class="tag">前置 ${d.n_pre} · 后继 ${d.n_post}</span>
        ${d.tasks ? `<span class="tag accent">被 ${d.tasks} 个项目任务需要</span>` : ''}
        ${(d.modules || []).map((x) => `<span class="tag accent">${x}</span>`).join('')}
      </div>
      ${hasStudent ? `
      <div class="kg-metrics">
        <div><b>${m === null ? '—' : m.toFixed(3)}</b><span>掌握度</span></div>
        <div><b>${d.retained === null || d.retained === undefined ? '—'
          : d.retained.toFixed(3)}</b><span>计入遗忘</span></div>
        <div><b>${d.validated ? '是' : '否'}</b><span>已验证</span></div>
      </div>
      ${d.due ? '<div class="kg-note warn">该复习了：曾经达标，但按遗忘曲线已经掉到复检线以下。</div>' : ''}
      ${m !== null && m < data.threshold
        ? '<div class="kg-note">掌握度低于阈值，属于当前缺口。</div>' : ''}
      ` : '<div class="kg-note">选择一位学生后，可叠加他的掌握度与复检状态。</div>'}
      ${rc ? (rc.is_self
        ? '<div class="kg-note">回溯结果：本身即根因，建议直接重讲并当堂检测。</div>'
        : `<div class="kg-note hi">根因回溯（深度 ${rc.depth}）：真正卡住的是「${rc.root_name}」，
           已在图上点亮整条链。</div>`) : ''}
      <div class="kg-actions">
        ${hasStudent ? '<button class="kg-rc">根因回溯</button>' : ''}
        <button class="kg-focus">聚焦邻域</button>
        <button class="kg-fly">飞到这里</button>
        ${onAsk ? '<button class="primary kg-ask">就它开始追问</button>' : ''}
      </div>`;
    panel.querySelector('.kg-close').onclick = () => select(null);
    panel.querySelector('.kg-fly').onclick = () => flyTo(d.id);
    panel.querySelector('.kg-focus').onclick = () => {
      S.focusSet = new Set([d.id, ...(neighbors.get(d.id) || [])]);
    };
    const rcBtn = panel.querySelector('.kg-rc');
    if (rcBtn) rcBtn.onclick = () => highlightRootCause(d.id);
    const askBtn = panel.querySelector('.kg-ask');
    if (askBtn) askBtn.onclick = () => onAsk(d);
  }

  renderUI();

  // ---- 主循环 ----
  let raf = 0, stopped = false;
  function resize() {
    const w = canvasBox.clientWidth || host.clientWidth;
    const h = canvasBox.clientHeight || 520;
    renderer.setSize(w, h, false);
    camera.aspect = w / Math.max(1, h);
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvasBox);
  resize();

  let fitted = false, midFitted = false;
  function tick(t) {
    if (stopped) return;
    raf = requestAnimationFrame(tick);
    if (document.hidden) return;                  // 切走时不烧 CPU
    const moving = layout.step();
    // 取两次景：布局大致成形时先框一次（软件渲染下收敛很慢，不能干等），
    // 完全静止后再框一次定稿。
    if (moving && !midFitted && layout.alpha < 0.35) { midFitted = true; fitCamera(); }
    if (!moving && !fitted) { fitted = true; fitCamera(); }
    if (moving && layout.alpha > 0.9) { fitted = false; midFitted = false; }
    controls.autoRotate = S.autoRotate;
    if (fly) {
      fly.t = Math.min(1, fly.t + 0.02);
      const e = 1 - Math.pow(1 - fly.t, 3);       // easeOutCubic
      camera.position.lerpVectors(fly.fromCam, fly.toCam, e);
      controls.target.lerpVectors(fly.fromTgt, fly.toTgt, e);
      if (fly.t >= 1) fly = null;
    }
    controls.update();
    // 雾只用来给深度线索，不该在远景里把整张图压暗
    scene.fog.density = 0.62 / Math.max(120, camera.position.distanceTo(controls.target));
    updateInstances(t);
    updateLabels();
    renderer.render(scene, camera);
  }
  raf = requestAnimationFrame(tick);

  return {
    destroy() {
      stopped = true;
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      sphereGeo.dispose();
      ringGeo.dispose();
      edgeGeo.dispose();
      host.innerHTML = '';
    },
    select, flyTo, highlightRootCause,
  };
}
