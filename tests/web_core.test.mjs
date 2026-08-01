/* 知识宇宙纯逻辑的测试。渲染壳测不了，但布局物理、色阶、降噪策略必须测。
 * 由 tests/test_web_core.py 调起（无 node 环境时自动跳过）。
 */
import assert from 'node:assert/strict';
import {
  Layout, labelPriority, masteryColor, nodeRadius, visibleMask,
} from '../apps/web/kg-core.js';

let pass = 0;
function test(name, fn) {
  fn();
  pass++;
  console.log(`  ok  ${name}`);
}

// ---------------------------------------------------------------- 色阶
test('掌握度色阶：低分偏红、高分偏绿、单调过渡', () => {
  const lo = masteryColor(0.0), mid = masteryColor(0.5), hi = masteryColor(1.0);
  assert.ok(lo[0] > lo[1] && lo[0] > lo[2], '0 分应当偏红');
  assert.ok(hi[1] > hi[0], '满分应当偏绿');
  assert.ok(mid[0] > hi[0] && mid[1] > lo[1], '中间值应当落在两端之间');
});

test('无作答记录返回中性灰，不伪装成掌握度 0', () => {
  const none = masteryColor(null), zero = masteryColor(0);
  assert.notDeepEqual(none, zero);
  assert.ok(Math.abs(none[0] - none[1]) < 0.1 && Math.abs(none[1] - none[2]) < 0.1);
});

test('色阶对越界输入是钳制的', () => {
  assert.deepEqual(masteryColor(-3), masteryColor(0));
  assert.deepEqual(masteryColor(9), masteryColor(1));
});

// ---------------------------------------------------------------- 尺寸
test('球体半径随挡路度单调增，且有上限', () => {
  assert.ok(nodeRadius(0) < nodeRadius(4));
  assert.ok(nodeRadius(4) < nodeRadius(16));
  assert.ok(nodeRadius(1e6) < 6.0, '必须封顶，否则一个节点会糊住整屏');
  assert.ok(nodeRadius(undefined) > 0);
});

// ---------------------------------------------------------------- 数据
const nodes = [
  { id: 1, code: 'A', name: '链式法则', unit: 'U1', unit_idx: 0, depth: 0, severity: 20, mastery: 0.9, due: false, validated: true },
  { id: 2, code: 'B', name: '反向传播', unit: 'U1', unit_idx: 0, depth: 1, severity: 14, mastery: 0.2, due: false, validated: false },
  { id: 3, code: 'C', name: '梯度消失', unit: 'U2', unit_idx: 1, depth: 2, severity: 3, mastery: 0.3, due: false, validated: false },
  { id: 4, code: 'D', name: '批归一化', unit: 'U2', unit_idx: 1, depth: 2, severity: 1, mastery: 0.8, due: true, validated: false },
  { id: 5, code: 'E', name: '未作答点', unit: 'U2', unit_idx: 1, depth: 3, severity: 2, mastery: null, due: false, validated: false },
];
const edges = [[1, 2], [2, 3], [2, 4], [4, 5]];
const THR = 0.75;

// ---------------------------------------------------------------- 过滤
test('只看缺口：排除已掌握，也排除无记录的（无记录 ≠ 未掌握）', () => {
  const m = visibleMask(nodes, { onlyGap: true }, THR);
  assert.deepEqual(m, [false, true, true, false, false]);
});

test('只看待复检', () => {
  assert.deepEqual(visibleMask(nodes, { onlyDue: true }, THR),
    [false, false, false, true, false]);
});

test('章节过滤与搜索可叠加，搜索大小写不敏感', () => {
  assert.deepEqual(visibleMask(nodes, { units: new Set(['U2']) }, THR),
    [false, false, true, true, true]);
  assert.deepEqual(visibleMask(nodes, { query: '反向' }, THR),
    [false, true, false, false, false]);
  assert.deepEqual(visibleMask(nodes, { query: 'c' }, THR),
    [false, false, true, false, false]);
  assert.deepEqual(visibleMask(nodes, { units: new Set(['U2']), onlyDue: true }, THR),
    [false, false, false, true, false]);
});

// ---------------------------------------------------------------- 标签降噪
test('标签有上限：不会把所有节点都标出来', () => {
  const vis = nodes.map(() => true);
  assert.equal(labelPriority(nodes, vis, {}, 2).length, 2);
});

test('选中与根因路径优先于挡路度', () => {
  const vis = nodes.map(() => true);
  const top = labelPriority(nodes, vis, { selected: 4 }, 1);
  assert.deepEqual(top, [3], '选中的节点必须有标签，哪怕它挡路度最低');
  const onPath = labelPriority(nodes, vis, { pathSet: new Set([3]) }, 1);
  assert.deepEqual(onPath, [2], '根因路径上的节点优先');
});

test('不可见的节点不参与标签', () => {
  const vis = [false, true, true, true, true];
  assert.ok(!labelPriority(nodes, vis, {}, 5).includes(0));
});

// ---------------------------------------------------------------- 布局
test('确定性初始化：同一份数据两次布局完全一致', () => {
  const a = new Layout(nodes, edges, 3, 2);
  const b = new Layout(nodes, edges, 3, 2);
  a.settle(200); b.settle(200);
  assert.deepEqual(Array.from(a.pos), Array.from(b.pos));
});

test('布局会收敛（alpha 退火到停止）', () => {
  const l = new Layout(nodes, edges, 3, 2);
  const it = l.settle(2000);
  assert.ok(it > 10 && it < 2000, `应在有限步内收敛，实际 ${it} 步`);
  assert.ok(Array.from(l.pos).every(Number.isFinite), '不得出现 NaN/Infinity');
});

test('依赖分层：Y 坐标与依赖深度强正相关（前置在下、后继在上）', () => {
  const l = new Layout(nodes, edges, 3, 2);
  l.setMode('depend');
  l.settle(1500);
  assert.ok(l.layerCorrelation() > 0.9,
    `分层布局必须把深度体现为高度，实际相关系数 ${l.layerCorrelation().toFixed(3)}`);
});

test('章节星系：同章节的节点聚到一起，跨章节明显更远', () => {
  const l = new Layout(nodes, edges, 3, 2);
  l.setMode('cluster');
  l.settle(2000);
  const d = (i, j) => Math.hypot(l.pos[i * 3] - l.pos[j * 3],
    l.pos[i * 3 + 1] - l.pos[j * 3 + 1], l.pos[i * 3 + 2] - l.pos[j * 3 + 2]);
  assert.ok(d(0, 1) < d(0, 2), '同章节应比跨章节更近');
  assert.ok(d(2, 3) < d(1, 4) || d(2, 3) < d(0, 2));
});

test('节点不会互相重叠到一点（斥力生效）', () => {
  const l = new Layout(nodes, edges, 3, 2);
  l.settle(1500);
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const dist = Math.hypot(l.pos[i * 3] - l.pos[j * 3],
        l.pos[i * 3 + 1] - l.pos[j * 3 + 1], l.pos[i * 3 + 2] - l.pos[j * 3 + 2]);
      assert.ok(dist > 3, `节点 ${i}/${j} 距离 ${dist.toFixed(2)} 过近`);
    }
  }
});

test('换布局模式会重新加热', () => {
  const l = new Layout(nodes, edges, 3, 2);
  l.settle(2000);
  assert.ok(l.alpha < 0.01);
  l.setMode('sphere');
  assert.equal(l.alpha, 1);
});

test('规模化：500 节点单步在预算内（力导向是 O(n²)，必须心里有数）', () => {
  const big = Array.from({ length: 500 }, (_, i) => ({
    id: i + 1, code: `K${i}`, name: `点${i}`, unit: `U${i % 12}`, unit_idx: i % 12,
    depth: i % 13, severity: i % 20, mastery: null, due: false, validated: false,
  }));
  const bigEdges = big.slice(1).map((d, i) => [big[i].id, d.id]);
  const l = new Layout(big, bigEdges, 12, 12);
  const t0 = process.hrtime.bigint();
  for (let i = 0; i < 10; i++) l.step();
  const ms = Number(process.hrtime.bigint() - t0) / 1e6 / 10;
  assert.ok(ms < 16, `单步 ${ms.toFixed(2)}ms，超过一帧预算`);
  console.log(`      500 节点单步 ${ms.toFixed(2)}ms`);
});

console.log(`\n  ${pass} 个用例通过`);
