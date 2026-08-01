/* 知识宇宙的**纯逻辑**：布局物理、色阶、可见性、标签降噪。
 *
 * 与 graph3d.js 的分工，和后端 agents 的 plan/express 是同一个道理：
 * 能确定性计算的部分抽出来，可单测、可复现；剩下那层壳只管画。
 * 本文件不 import three.js，因此可以在 Node 里直接跑（tests/test_web_core.py）。
 */

/** 掌握度色阶 → [r,g,b]（0–1）。与 2D 热力图同一套语言，避免两个视图各说各话。 */
export function masteryColor(v) {
  if (v === null || v === undefined) return [0.35, 0.39, 0.45];   // 无记录 = 中性灰
  const t = Math.max(0, Math.min(1, v));
  const stops = [[0.81, 0.13, 0.18], [0.75, 0.53, 0.0], [0.10, 0.50, 0.22]];
  const i = t < 0.5 ? 0 : 1, k = t < 0.5 ? t * 2 : (t - 0.5) * 2;
  const a = stops[i], b = stops[i + 1];
  return [0, 1, 2].map((j) => a[j] + (b[j] - a[j]) * k);
}

/** 球体半径编码挡路度：挡住的越多，看上去越"重"。 */
export function nodeRadius(severity) {
  return 2.1 + Math.min(3.6, Math.sqrt(Math.max(0, severity || 0)) * 0.62);
}

/** 过滤器 → 每个节点是否可见。 */
export function visibleMask(nodes, f, threshold) {
  const q = (f.query || '').trim().toLowerCase();
  return nodes.map((d) => {
    if (f.units && f.units.size && !f.units.has(d.unit)) return false;
    if (f.onlyGap && !(d.mastery !== null && d.mastery !== undefined
      && d.mastery < threshold)) return false;
    if (f.onlyDue && !d.due) return false;
    if (q && !(d.name.toLowerCase().includes(q) || d.code.toLowerCase().includes(q)))
      return false;
    return true;
  });
}

/** 标签降噪：同一时刻只给"值得看"的节点打标签，其余按挡路度取前 N。 */
export function labelPriority(nodes, vis, st, cap) {
  const out = [];
  for (let i = 0; i < nodes.length; i++) {
    if (!vis[i]) continue;
    const d = nodes[i];
    let pri = d.severity || 0;
    if (st.pathSet && st.pathSet.has(d.id)) pri += 1000;
    if (st.selected === d.id) pri += 5000;
    if (st.hovered === d.id) pri += 4000;
    if (st.focusSet && !st.focusSet.has(d.id)) pri -= 500;
    out.push([pri, i]);
  }
  out.sort((a, b) => b[0] - a[0]);
  return out.slice(0, cap).map((x) => x[1]);
}

/* ---------------------------------------------------------------- 3D 力导向布局
 * 斥力（O(n²)，远处剪枝）+ 弹簧 + 模式约束力。
 * 模式对应美团文中的两类做法：分层布局（depend）与层次聚类布局（cluster，
 * 含 ClusterCenter / Strength / Radius 三个参数）。
 */
export class Layout {
  constructor(nodes, edges, maxDepth, nUnits, seed = 20260801) {
    this.n = nodes.length;
    this.nodes = nodes;
    this.maxDepth = Math.max(1, maxDepth);
    this.nUnits = Math.max(1, nUnits);
    this.pos = new Float32Array(this.n * 3);
    this.vel = new Float32Array(this.n * 3);
    this.index = new Map(nodes.map((d, i) => [d.id, i]));
    this.links = edges
      .map(([a, b]) => [this.index.get(a), this.index.get(b)])
      .filter(([a, b]) => a !== undefined && b !== undefined);
    this.mode = 'depend';
    this.alpha = 1;
    this.seedValue = seed;
    this.seed();
  }

  /** 确定性初始化：同一份数据每次打开形状一致，演示时不会"每次都不一样"。 */
  seed() {
    let s = this.seedValue;
    const rnd = () => (s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff - 0.5;
    for (let i = 0; i < this.n; i++) {
      const d = this.nodes[i];
      const ang = (d.unit_idx / this.nUnits) * Math.PI * 2;
      this.pos[i * 3] = Math.cos(ang) * 60 + rnd() * 40;
      this.pos[i * 3 + 1] = (d.depth / this.maxDepth - 0.5) * 120 + rnd() * 10;
      this.pos[i * 3 + 2] = Math.sin(ang) * 60 + rnd() * 40;
    }
    this.vel.fill(0);
  }

  setMode(mode) {
    this.mode = mode;
    this.alpha = 1;             // 重新加热，让布局平滑迁移过去
  }

  clusterCenter(unitIdx) {
    const a = (unitIdx / this.nUnits) * Math.PI * 2;
    return [Math.cos(a) * 200, ((unitIdx % 3) - 1) * 55, Math.sin(a) * 200];
  }

  step() {
    if (this.alpha < 0.005) return false;
    const { pos, vel, n } = this;
    const REP = this.mode === 'cluster' ? 620 : 1500;
    const SPRING = 0.012, REST = 26;

    for (let i = 0; i < n; i++) {
      let fx = 0, fy = 0, fz = 0;
      const ix = pos[i * 3], iy = pos[i * 3 + 1], iz = pos[i * 3 + 2];
      for (let j = 0; j < n; j++) {
        if (i === j) continue;
        const dx = ix - pos[j * 3], dy = iy - pos[j * 3 + 1], dz = iz - pos[j * 3 + 2];
        let d2 = dx * dx + dy * dy + dz * dz;
        if (d2 < 1) d2 = 1;
        if (d2 > 90000) continue;                 // 远处忽略，省一半计算
        const f = REP / d2 / Math.sqrt(d2);
        fx += dx * f; fy += dy * f; fz += dz * f;
      }
      vel[i * 3] += fx; vel[i * 3 + 1] += fy; vel[i * 3 + 2] += fz;
    }

    for (const [a, b] of this.links) {
      const dx = pos[b * 3] - pos[a * 3];
      const dy = pos[b * 3 + 1] - pos[a * 3 + 1];
      const dz = pos[b * 3 + 2] - pos[a * 3 + 2];
      const d = Math.max(1, Math.hypot(dx, dy, dz));
      const f = (d - REST) * SPRING;
      const ux = dx / d * f, uy = dy / d * f, uz = dz / d * f;
      vel[a * 3] += ux; vel[a * 3 + 1] += uy; vel[a * 3 + 2] += uz;
      vel[b * 3] -= ux; vel[b * 3 + 1] -= uy; vel[b * 3 + 2] -= uz;
    }

    for (let i = 0; i < n; i++) {
      const d = this.nodes[i];
      let cx = 0, cy = 0, cz = 0, k = 0.006;
      if (this.mode === 'depend') {
        const targetY = (d.depth / this.maxDepth - 0.5) * 190;
        vel[i * 3 + 1] += (targetY - pos[i * 3 + 1]) * 0.06;
        k = 0.011;      // 向心力偏强：让这块"板"收紧，否则在屏幕上就只剩小点
      } else if (this.mode === 'cluster') {
        const c = this.clusterCenter(d.unit_idx);
        cx = c[0]; cy = c[1]; cz = c[2];
        k = 0.032;                                  // Strength：簇心吸引要压过斥力
      } else if (this.mode === 'sphere') {
        const a = (d.unit_idx / this.nUnits) * Math.PI * 2;
        const phi = (d.depth / this.maxDepth) * Math.PI;
        const R = 110;                              // Radius
        cx = Math.sin(phi) * Math.cos(a) * R;
        cy = Math.cos(phi) * R;
        cz = Math.sin(phi) * Math.sin(a) * R;
        k = 0.035;
      }
      vel[i * 3] += (cx - pos[i * 3]) * k;
      vel[i * 3 + 1] += (cy - pos[i * 3 + 1]) * (this.mode === 'depend' ? 0 : k);
      vel[i * 3 + 2] += (cz - pos[i * 3 + 2]) * k;
    }

    const damp = 0.82;
    for (let i = 0; i < n * 3; i++) {
      vel[i] *= damp;
      pos[i] += vel[i] * this.alpha;
    }
    this.alpha *= 0.985;
    return true;
  }

  /** 收敛到静止（供测试与"一键定型"用）。 */
  settle(maxIter = 800) {
    let it = 0;
    while (it < maxIter && this.step()) it++;
    return it;
  }

  /** 分层布局下，依赖深度是否真的体现为高度差（供测试）。 */
  layerCorrelation() {
    let n = 0, sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0;
    for (let i = 0; i < this.n; i++) {
      const x = this.nodes[i].depth, y = this.pos[i * 3 + 1];
      n++; sx += x; sy += y; sxx += x * x; syy += y * y; sxy += x * y;
    }
    const cov = n * sxy - sx * sy;
    const den = Math.sqrt(n * sxx - sx * sx) * Math.sqrt(n * syy - sy * sy);
    return den === 0 ? 0 : cov / den;
  }
}
