/* 院长实验班 AI 教学系统 · 前端（零构建，原生 JS）
 *
 * 产品层面的硬约束（CLAUDE.md §11）在这里同样生效：
 *   - 不展示"课程进度百分比"，只展示知识点掌握度与项目进度；
 *   - 不做全班排行榜，只做个人成长曲线；
 *   - 证据不足时必须显示"数据不足，仅供参考"。
 */
'use strict';

const S = {
  token: localStorage.getItem('aiedu.token') || 'teacher:T001',
  tab: null, cache: {}, me: null, health: null,
};

// ---------------------------------------------------------------- 工具
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
};
const $ = (sel) => document.querySelector(sel);
const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const f3 = (x) => (x === null || x === undefined ? '-' : Number(x).toFixed(3));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json', 'X-Auth-Token': S.token },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({ error: '响应不是 JSON' }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const isTeacher = () => S.token.startsWith('teacher:');
const myId = () => (S.me && S.me.student_id) || null;

function heatColor(v) {
  // 掌握度色阶：红 → 黄 → 绿。刻意不用"分数"语义的配色。
  const t = Math.max(0, Math.min(1, v));
  const stops = [[207, 34, 46], [191, 135, 0], [26, 127, 55]];
  const i = t < 0.5 ? 0 : 1, k = t < 0.5 ? t * 2 : (t - 0.5) * 2;
  const a = stops[i], b = stops[i + 1];
  return `rgb(${a.map((c, j) => Math.round(c + (b[j] - c) * k)).join(',')})`;
}

function caveatBox(o) {
  if (!o || !o.caveat) return null;
  return h('div', { class: 'notice' }, `⚠ ${o.caveat}（证据 ${o.evidence_count ?? 0} 条，置信度 ${f3(o.confidence)}）`);
}

function loading() { return h('div', { class: 'loading' }, '载入中…'); }

/* 当前视角的学生 id。学生本人只能是自己；教师用顶部选择器切换。 */
function currentStudent() {
  return myId() || Number(S.pickStudent) || (S.students && S.students[0] && S.students[0].id) || 1;
}

/* 教师视角选择器：渲染在视图容器之外，避免被各视图内部的重绘清掉。 */
function renderPicker(host, needed) {
  host.innerHTML = '';
  if (!needed || myId() || !S.students || !S.students.length) return;
  if (!S.pickStudent) S.pickStudent = S.students[0].id;
  host.append(card('以学生视角查看', h('div', { class: 'flexrow' },
    h('select', {
      onchange: (e) => { S.pickStudent = Number(e.target.value); render(); },
    }, ...S.students.map((s) => h('option', {
      value: s.id, selected: Number(s.id) === Number(S.pickStudent) ? '' : null,
    }, `${s.name}（${s.sid}·${s.klass}）`))),
    h('span', { class: 'pill' }, '教师可见本班学生；跨班访问会被 API 拒绝'))));
}

function bar(v) {
  return h('span', { class: 'bar' }, h('i', { style: `width:${pct(v)};background:${heatColor(v)}` }));
}

// ---------------------------------------------------------------- SVG 图形
function radarSvg(items, size = 260) {
  const n = items.length; if (!n) return h('div', { class: 'muted' }, '暂无能力数据');
  const c = size / 2, r = c - 34;
  const pt = (i, v) => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    return [c + Math.cos(a) * r * v, c + Math.sin(a) * r * v];
  };
  const ring = (v) => items.map((_, i) => pt(i, v).join(',')).join(' ');
  const poly = items.map((it, i) => pt(i, Math.max(0.02, it.level)).join(',')).join(' ');
  const labels = items.map((it, i) => {
    const [x, y] = pt(i, 1.22);
    return `<text x="${x}" y="${y}" font-size="10" text-anchor="middle" fill="currentColor" opacity=".7">${it.code}</text>`;
  }).join('');
  return h('div', {
    html: `<svg viewBox="0 0 ${size} ${size}" width="100%" style="max-width:${size}px">
      ${[0.25, 0.5, 0.75, 1].map((v) => `<polygon points="${ring(v)}" fill="none" stroke="currentColor" opacity=".13"/>`).join('')}
      <polygon points="${poly}" fill="rgba(31,111,235,.22)" stroke="#1f6feb" stroke-width="1.5"/>
      ${labels}</svg>`,
  });
}

function lineSvg(points, w = 420, hgt = 130) {
  if (!points.length) return h('div', { class: 'muted' }, '暂无成长数据');
  const max = 1, pad = 24;
  const dx = (w - pad * 2) / Math.max(1, points.length - 1);
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${pad + i * dx},${hgt - pad - (p.v / max) * (hgt - pad * 2)}`).join(' ');
  const dots = points.map((p, i) => `<circle cx="${pad + i * dx}" cy="${hgt - pad - (p.v / max) * (hgt - pad * 2)}" r="2.5" fill="#1f6feb"/>`).join('');
  return h('div', {
    html: `<svg viewBox="0 0 ${w} ${hgt}" width="100%">
      <line x1="${pad}" y1="${hgt - pad}" x2="${w - pad}" y2="${hgt - pad}" stroke="currentColor" opacity=".2"/>
      <path d="${d}" fill="none" stroke="#1f6feb" stroke-width="2"/>${dots}
      <text x="${pad}" y="14" font-size="10" fill="currentColor" opacity=".6">平均掌握度（不是课程进度）</text>
    </svg>`,
  });
}

// ---------------------------------------------------------------- 视图：班级诊断
async function viewClassDiagnosis(root) {
  root.append(loading());
  const klass = isTeacher() ? (S.klass || '') : '';
  const d = await api(`/api/diagnosis/class/ML${klass ? `?klass=${encodeURIComponent(klass)}` : ''}`);
  root.innerHTML = '';
  const p = d.plan;

  root.append(h('div', { class: 'grid g3' },
    card('全班主动使用率', h('div', {},
      h('div', { class: 'kpi' }, pct(p.engagement.active_rate),
        h('small', {}, `${p.engagement.n_active}/${p.engagement.n_students} 人近 7 天活跃`)),
      h('p', { class: 'hint' }, 'Phase 1 前移的观测指标：行为问题比算法问题更致命')),
    ),
    card('待攻克知识点', h('div', {},
      h('div', { class: 'kpi' }, p.top_weak_points.length, h('small', {}, '个（已按挡路度排序）')),
      h('p', { class: 'hint' }, '低掌握 × 高挡路度，而不是简单按错误率排序')),
    ),
    card('追问降级热点', h('div', {},
      h('div', { class: 'kpi' }, p.escalation_hotspots.reduce((a, b) => a + b.n, 0),
        h('small', {}, '次降级')),
      h('p', { class: 'hint' }, '系统被迫给出提示的地方，就是该重讲的地方')),
    ),
  ));

  root.append(caveatBox(d));

  // 叙述
  root.append(card('诊断结论', h('div', {},
    h('div', { style: 'white-space:pre-wrap' }, d.narrative || '（无）'),
    d.degraded ? h('p', { class: 'hint' }, '⚙ 当前由离线表达器生成措辞；结论本身来自确定性计算，与模型无关') : null,
  )));

  // Top3 + 根因
  const top = h('div', { class: 'grid g2' });
  for (const w of p.top_weak_points) {
    top.append(card(w.name, h('div', {},
      h('div', { class: 'flexrow' },
        h('span', { class: 'tag' }, w.unit),
        h('span', { class: 'tag bad' }, `平均掌握度 ${f3(w.avg_mastery)}`),
        h('span', { class: 'tag' }, `挡路度 ${w.blocking_severity}`)),
      w.root_cause_name
        ? h('div', { class: 'notice info' },
          `根因回溯（深度 ${w.root_cause_depth}）：真正卡住的是「${w.root_cause_name}」，建议先补这一环，再回到本题。`)
        : h('div', { class: 'notice info' }, '本身即根因，建议直接重讲并当堂检测。'),
      w.typical_errors.length
        ? h('div', { class: 'stack' }, h('h4', {}, '典型错误（来自错误模式库）'),
          ...w.typical_errors.map((e) => h('div', { class: 'muted' },
            `· ${e.description}（出现 ${e.occurrence_count} 次${e.teacher_verified ? '，教师已确认' : '，待教师确认'}）`)))
        : h('p', { class: 'hint' }, '该知识点暂无沉淀的典型错误'),
      h('div', { class: 'flexrow' },
        h('button', { class: 'primary', onclick: () => alert('已记录为下次课重点（演示）') }, '采纳为下次课重点'),
        h('button', { onclick: () => alert('已否决。否决记录用于改进系统，不用于评价教师。') }, '否决')),
    )));
  }
  root.append(top);

  // 热力图
  const heat = h('div', { class: 'heat' });
  for (const k of p.heatmap) {
    heat.append(h('i', {
      style: `background:${heatColor(k.avg_mastery)}`,
      title: `${k.code} ${k.name}\n平均掌握度 ${f3(k.avg_mastery)}｜证据 ${k.evidence_count}`,
      onclick: () => openKp(k.kp_id),
    }));
  }
  root.append(card('全班知识点掌握热力图', h('div', {}, heat,
    h('p', { class: 'hint' }, `共 ${p.heatmap.length} 个知识点，从左到右按掌握度升序；点击查看知识点详情`))));

  // 需介入学生 + 降级热点
  root.append(h('div', { class: 'grid g2' },
    card('需个别介入的学生', tableOf(
      ['学号', '姓名', '平均掌握度', '证据'],
      p.attention_students.map((s) => [s.sid, s.name, f3(s.avg_mastery),
        s.caveat ? h('span', { class: 'tag warn' }, s.caveat) : s.evidence_count]),
    ), '名单是建议，不是结论；系统不生成任何针对教师的评价性数据'),
    card('降级热点（追问引擎被迫给提示）', tableOf(
      ['知识点', '降级次数'], p.escalation_hotspots.map((e) => [e.name, e.n]),
    ), '降级是为防止挫败感摧毁动机，不是为了让学生轻松'),
  ));
}

// ---------------------------------------------------------------- 视图：错误模式库
async function viewErrors(root) {
  root.append(loading());
  const [d, st] = await Promise.all([api('/api/errors/patterns'), api('/api/errors/stats')]);
  root.innerHTML = '';
  root.append(h('div', { class: 'grid g3' },
    card('已沉淀错误模式', h('div', { class: 'kpi' }, st.patterns)),
    card('教师已确认', h('div', { class: 'kpi' }, st.verified, h('small', {}, '条'))),
    card('原始错误实例', h('div', { class: 'kpi' }, st.instances)),
  ));
  root.append(h('div', { class: 'notice info' },
    '这是系统最有价值的资产：把"学生通常错在哪"从教师的个人记忆变成学院资产。教师确认过的模式才可用于向学生解释。'));
  const rows = d.items.map((p) => [
    p.kp_name,
    h('div', {}, p.description, p.samples.length
      ? h('div', { class: 'muted mono' }, `例：${p.samples[0].raw_text.slice(0, 46)}…`) : null),
    p.root_cause_name || '-',
    p.occurrence_count,
    p.teacher_verified ? h('span', { class: 'tag ok' }, '已确认')
      : h('button', {
        onclick: async (e) => {
          await api(`/api/errors/patterns/${p.id}/verify`, { body: { note: '教师确认' } });
          e.target.replaceWith(h('span', { class: 'tag ok' }, '已确认'));
        },
      }, '确认'),
  ]);
  root.append(card('错误模式（按出现次数排序）',
    h('div', { class: 'scroll' }, tableOf(['知识点', '描述', '归因根因', '出现', '教师确认'], rows))));
}

// ---------------------------------------------------------------- 视图：审查队列
async function viewReview(root) {
  root.append(loading());
  // 规则统计是教师视角的运维数据，学生端不请求（请求了也会被 403 挡回来）
  const [d, rules] = await Promise.all([
    api('/api/review/findings'),
    isTeacher() ? api('/api/review/rules') : Promise.resolve({ items: [] }),
  ]);
  root.innerHTML = '';
  root.append(h('div', { class: 'notice info' },
    'related_kp_ids 是把工程问题翻回知识判断的唯一通道。若某类缺陷无法可靠映射到知识点，则不回写，宁缺毋滥。'));

  const rows = d.items.slice(0, 60).map((f) => [
    h('span', { class: 'mono' }, `${f.file.split('/').pop()}:${f.line}`),
    h('span', { class: `tag ${f.severity === 'error' ? 'bad' : f.severity === 'warn' ? 'warn' : ''}` }, f.rule),
    h('div', {}, f.message, h('div', { class: 'muted' }, f.suggestion)),
    f.related_kp_ids.length ? h('span', { class: 'tag accent' }, `挂 ${f.related_kp_ids.length} 个知识点`)
      : h('span', { class: 'muted' }, '无可靠映射'),
    f.wrote_back ? h('span', { class: 'tag ok' }, '已回写 L2')
      : h('span', { class: 'muted' }, f.teacher_action === 'pending' ? '待处理' : f.teacher_action),
    isTeacher() && f.teacher_action === 'pending'
      ? h('div', { class: 'flexrow' },
        h('button', {
          onclick: async (e) => {
            const r = await api(`/api/review/findings/${f.id}/action`, { body: { action: 'accepted' } });
            e.target.parentElement.replaceWith(h('span', { class: 'muted' },
              r.wrote_back_kp_ids.length ? `已采纳并回写 ${r.wrote_back_kp_ids.length} 个知识点` : `已采纳（${r.note}）`));
          },
        }, '采纳'),
        h('button', {
          onclick: async (e) => {
            await api(`/api/review/findings/${f.id}/action`, { body: { action: 'rejected' } });
            e.target.parentElement.replaceWith(h('span', { class: 'muted' }, '已否决'));
          },
        }, '否决'))
      : '',
  ]);
  root.append(card('审查发现（第一级静态分析 + 第二级模型评审）',
    h('div', { class: 'scroll' },
      tableOf(['位置', '规则', '问题与建议', '知识点映射', '回写状态', '教师处置'], rows))));

  if (rules.items.length) {
    root.append(card('规则命中与否决统计',
      tableOf(['规则', '命中', '被否决'], rules.items.map((r) => [r.rule, r.n, r.rejected])),
      '否决记录用于持续优化审查规则——最常被否决的规则就该改或该删'));
  }

  root.append(card('提交一段代码试试', h('div', {},
    h('textarea', { id: 'rv-src', rows: 10, placeholder: '粘贴 Python 代码…' }),
    h('div', { class: 'composer' },
      h('button', {
        class: 'primary',
        onclick: async () => {
          const src = $('#rv-src').value;
          if (!src.trim()) return;
          const out = await api('/api/review/source', {
            body: { student_id: currentStudent(), path: 'paste.py', source: src },
          });
          $('#rv-out').innerHTML = '';
          $('#rv-out').append(
            h('div', { style: 'white-space:pre-wrap' }, out.narrative || ''),
            tableOf(['行', '规则', '问题'], out.plan.findings.map((f) => [f.line, f.rule, f.message])));
        },
      }, '静态分析 + 模型评审')),
    h('div', { id: 'rv-out' }))));
}

// ---------------------------------------------------------------- 视图：学生状态
async function viewMe(root) {
  const sid = currentStudent();
  root.append(loading());
  const [prof, diag] = await Promise.all([
    api(`/api/profile/${sid}?course=ML`), api(`/api/diagnosis/student/${sid}`),
  ]);
  root.innerHTML = '';
  const e = prof.engagement;

  root.append(h('div', { class: 'grid g3' },
    card('连续投入', h('div', {},
      h('div', { class: 'kpi' }, e.current_days, h('small', {}, `天 · 最长 ${e.longest_days} 天`)),
      h('p', { class: 'hint' }, '只对坚持给正反馈：不奖励正确率、做题数与停留时长'))),
    card('已追踪知识点', h('div', {},
      h('div', { class: 'kpi' }, diag.plan.n_kps_tracked),
      h('p', { class: 'hint' }, '系统只展示知识点掌握度，不展示课程进度百分比'))),
    card('成就', h('div', {},
      h('div', { class: 'kpi' }, e.achievements.length),
      h('div', { class: 'flexrow' }, ...e.achievements.slice(0, 4).map((a) =>
        h('span', { class: 'tag accent', title: a.detail }, kindName(a.kind)))))),
  ));
  root.append(caveatBox(prof));

  root.append(h('div', { class: 'grid g2' },
    card('能力雷达（M1–M8）', h('div', {}, radarSvg(prof.radar),
      h('div', { class: 'stack' }, ...prof.radar.map((r) =>
        h('div', { class: 'rowline' }, h('span', {}, `${r.code} ${r.name}`),
          h('span', { class: 'flexrow' }, bar(r.level), h('span', { class: 'mono' }, f3(r.level)))))))),
    card('个人成长曲线', h('div', {},
      lineSvg(prof.growth.map((g) => ({ v: g.avg_mastery_at_end }))),
      h('div', { class: 'scroll' }, tableOf(['时间片', '事件', '降级'],
        prof.growth.map((g) => [g.week, g.events, g.escalations]))),
      h('p', { class: 'hint' }, '不做全班排行榜，只做个人成长曲线'))),
  ));

  // 掌握的质量：验证过没有、还记得吗、是自己挣来的吗
  try {
    const q = await api(`/api/verification/${sid}`);
    const ind = q.independence;
    root.append(card('掌握的质量', h('div', {},
      h('p', { class: 'hint' },
        '一次做对不算掌握。只有间隔若干天之后再次做对，才算"已验证"；'
        + '而掌握度会随时间衰减，掉下来就该复检了。'),
      h('div', { class: 'grid g3' },
        card('已验证掌握', h('div', {},
          h('div', { class: 'kpi' }, `${q.n_validated}/${q.n_mastered}`,
            h('small', {}, `验证率 ${pct(q.validated_rate)}`)),
          h('p', { class: 'hint' }, '跨时间再次做对才算数，挡住"当堂练到做对"'))),
        card('待复检', h('div', {},
          h('div', { class: 'kpi' }, q.n_due, h('small', {}, '个知识点')),
          h('p', { class: 'hint' }, '曾经达标，按遗忘曲线已掉到复检线以下'))),
        card('无提示做对率', h('div', {},
          h('div', { class: 'kpi' }, pct(ind.unaided_rate),
            h('small', {}, `${ind.n_unaided_correct}/${ind.n_correct}`)),
          h('p', { class: 'hint' }, '诊断指标，不作为激励对象或排名依据')))),
      q.due_reviews.length
        ? h('div', {}, h('h4', {}, '该复习了'),
          tableOf(['知识点', '当时掌握度', '计入遗忘', '多久没碰', '挡路度'],
            q.due_reviews.map((r) => [
              h('a', { href: '#', onclick: (e) => { e.preventDefault(); startAsk(r.kp_id); } },
                r.name),
              f3(r.p_mastery), f3(r.retained), `${r.days_since} 天`, r.blocking_severity])))
        : h('p', { class: 'hint' }, '暂无到期复检的知识点'),
      Object.keys(q.style_effectiveness || {}).length
        ? h('div', {}, h('h4', {}, '哪种追问方式对你有效'),
          tableOf(['方式', '用过', '有进展', '有效率'],
            Object.entries(q.style_effectiveness).map(([k, v]) =>
              [k, v.tried, v.progressed, pct(v.rate)])),
          h('p', { class: 'hint' }, '系统会优先用对你有效的那种问法——证据不足时不猜'))
        : null,
    )));
  } catch (e) { /* 冷启动期没有数据，静默跳过 */ }

  const weak = diag.plan.weak_points;
  root.append(card('待攻克与根因', tableOf(
    ['知识点', '掌握度', '置信度', '证据', '根因'],
    weak.map((w) => [
      h('a', { href: '#', onclick: (ev) => { ev.preventDefault(); openKp(w.kp_id); } }, w.name),
      h('span', { class: 'flexrow' }, bar(w.p_mastery), h('span', { class: 'mono' }, f3(w.p_mastery))),
      f3(w.confidence), w.evidence_count,
      w.root_cause ? h('span', { class: 'tag warn' }, `先补：${w.root_cause}` ) : '本身即根因',
    ]),
  ), '根因由图谱依赖结构确定性回溯得出，不由大模型猜测'));

  root.append(card('成就记录', h('div', { class: 'stack' },
    ...e.achievements.map((a) => h('div', { class: 'rowline' },
      h('span', {}, h('span', { class: 'tag accent' }, kindName(a.kind)), ' ', a.detail),
      h('span', { class: 'muted mono' }, (a.earned_at || '').slice(0, 10)))))));
}

function kindName(k) {
  return { breakthrough: '破关', comeback: '回归', milestone: '里程碑', first_run: '首跑通', streak: '连续' }[k] || k;
}

// ---------------------------------------------------------------- 视图：拉取式任务
async function viewPull(root) {
  const sid = currentStudent();
  root.append(loading());
  const projects = await api('/api/projects');
  const proj = S.proj || projects.items[0].code;
  const board = await api(`/api/board?project=${proj}&student_id=${sid}`);
  root.innerHTML = '';

  root.append(card('拉取式学习', h('div', {},
    h('p', { class: 'hint' }, '知识由项目需求拉取，不由课程顺序推送。选一个当前任务，系统只推此刻最挡路的 1–2 个知识点。'),
    h('div', { class: 'flexrow' },
      h('select', {
        onchange: (ev) => { S.proj = ev.target.value; render(); },
      }, ...projects.items.map((p) => h('option', { value: p.code, selected: p.code === proj ? '' : null }, p.name))),
    ))));

  const list = h('div', { class: 'stack' });
  for (const t of board.tasks) {
    list.append(h('div', { class: 'rowline' },
      h('span', {}, t.parent_id ? '　└ ' : '', t.name,
        t.milestone ? h('span', { class: 'tag accent' }, '里程碑') : null,
        h('span', { class: 'tag' }, `${t.n_kps} 个知识点`)),
      h('span', { class: 'flexrow' },
        h('span', { class: 'muted mono' }, t.status),
        h('button', { onclick: () => showGap(t.id) }, '算缺口'))));
  }
  root.append(h('div', { class: 'grid g2' },
    card('项目任务树', list, '任务所需知识点由导师标注，不由大模型生成——这是学分认定与能力追溯的依据'),
    card('知识缺口', h('div', { id: 'gap-out' }, h('p', { class: 'hint' }, '点左侧任务的「算缺口」')))));

  async function showGap(taskId) {
    const box = $('#gap-out'); box.innerHTML = ''; box.append(loading());
    const g = await api(`/api/next-target?task_id=${taskId}&student_id=${sid}`);
    const p = g.plan;
    box.innerHTML = '';
    box.append(
      h('h4', {}, p.task_name),
      h('div', { class: 'flexrow' },
        h('span', { class: 'tag' }, `所需 ${p.required_total}`),
        h('span', { class: 'tag ok' }, `已掌握 ${p.mastered}`),
        h('span', { class: 'tag bad' }, `缺口 ${p.gap.length}`)),
      caveatBox(g),
      h('div', { class: 'notice info' }, g.narrative || ''),
      h('h4', {}, '本次只推这些（拉取式）'),
      ...p.push.map((k) => h('div', { class: 'card', style: 'margin:8px 0' },
        h('div', { class: 'flexrow' },
          h('strong', {}, k.name),
          h('span', { class: 'tag' }, `掌握度 ${f3(k.p_mastery)}`),
          h('span', { class: 'tag warn' }, `挡路度 ${k.blocking_severity}`)),
        h('div', { class: 'flexrow' },
          h('button', { class: 'primary', onclick: () => startAsk(k.kp_id) }, '开始引导追问'),
          h('button', { onclick: () => openKp(k.kp_id) }, '看图谱位置')))),
      h('h4', {}, '完整缺口（缺口内部按拓扑序与挡路度排序）'),
      h('div', { class: 'scroll' }, tableOf(['知识点', '掌握度', '挡路度', '前置卡点'],
        p.gap.map((k) => [k.name, f3(k.p_mastery), k.blocking_severity,
          k.prereq_gap.length ? `${k.prereq_gap.length} 个` : '-']))),
    );
  }
}

// ---------------------------------------------------------------- 视图：追问
async function viewAsk(root) {
  root.innerHTML = '';
  root.append(card('苏格拉底式引导问答', h('div', {},
    h('p', { class: 'hint' },
      '追问策略由 L2 状态确定：从你已掌握的最近前置点切入，掌握度越低拆得越细。反复卡住时会逐级降级并留痕——降级是为了防止挫败感摧毁动机，不是为了让你轻松。'),
    h('div', { class: 'flexrow' },
      h('input', { id: 'ask-kp', placeholder: '知识点代码，如 ML-10-05', value: S.askKp || 'ML-10-05' }),
      h('button', { class: 'primary', onclick: () => startAskByCode() }, '开始')))));
  root.append(h('div', { id: 'ask-box' }));
  if (S.session) renderSession(S.session);
}

async function startAskByCode() {
  const code = $('#ask-kp').value.trim();
  const kps = await api('/api/courses/ML/kps');
  const kp = kps.items.find((k) => k.code === code || k.name === code);
  if (!kp) return alert('未找到该知识点');
  startAsk(kp.id);
}

async function startAsk(kpId) {
  S.tab = 'ask';
  await render();
  const root = $('#content'); root.innerHTML = ''; await viewAsk(root);
  const box = $('#ask-box'); box.innerHTML = ''; box.append(loading());
  const out = await api('/api/ask/start', { body: { kp_id: kpId, student_id: currentStudent() } });
  S.session = out.plan.session_id;
  renderSession(S.session, out);
}

async function renderSession(sessionId, first) {
  const box = $('#ask-box'); box.innerHTML = '';
  const v = await api(`/api/ask/session/${sessionId}`);
  const s = v.session;
  const chat = h('div', { class: 'chat' });
  for (const t of v.turns) {
    chat.append(h('div', { class: `msg ${t.role === 'system' ? 'sys' : 'me'}` },
      h('span', { class: 'meta' }, t.role === 'system' ? `助教 · ${t.style || ''}` : '我'),
      t.text));
  }
  const level = s.escalation_level;
  box.append(card('会话', h('div', {},
    h('div', { class: 'flexrow' },
      h('span', { class: 'tag' }, `尝试 ${s.attempts} 次`),
      h('span', { class: level ? 'tag warn' : 'tag ok' },
        level ? `已降级到 L${level}` : '未降级（不会直接给答案）'),
      h('span', { class: 'tag' }, `拆解层级 ${s.depth}`),
      s.style ? h('span', { class: 'tag accent' }, s.style) : null),
    first && first.plan.entry_kp_name
      ? h('div', { class: 'notice info' }, `切入点：从你已掌握的「${first.plan.entry_kp_name}」出发。${first.plan.reason || ''}`)
      : null,
    chat,
    h('div', { class: 'composer' },
      h('textarea', { id: 'ask-in', placeholder: '写下你的思路，卡住也直接说卡在哪…' }),
      h('div', { class: 'stack' },
        h('button', {
          class: 'primary',
          onclick: async () => {
            const text = $('#ask-in').value.trim(); if (!text) return;
            await api('/api/ask/reply', { body: { session_id: sessionId, text } });
            renderSession(sessionId);
          },
        }, '回复'),
        h('button', {
          onclick: async () => {
            await api('/api/ask/reply', { body: { session_id: sessionId, text: '我卡住了', stuck: true } });
            renderSession(sessionId);
          },
        }, '我卡住了'))),
    first && first.citations && first.citations.length
      ? h('p', { class: 'hint' }, `教材依据：${first.citations.map((c) => c.title).join('、')}`) : null,
  )));
}

// ---------------------------------------------------------------- 视图：副驾驶
async function viewCopilot(root) {
  root.innerHTML = '';
  root.append(card('AI 学习副驾驶', h('div', {},
    h('p', { class: 'hint' },
      '与通用对话工具的三点差异：接项目知识库做 RAG、读你的能力档案、主动同步提交记录。它不会替你写完整作业答案。'),
    h('div', { class: 'composer' },
      h('textarea', { id: 'cp-in', placeholder: '例如：AGV 上线后精度掉了一半，可能是什么原因？' }),
      h('button', {
        class: 'primary',
        onclick: async () => {
          const q = $('#cp-in').value.trim(); if (!q) return;
          const out = $('#cp-out'); out.innerHTML = ''; out.append(loading());
          const r = await api('/api/copilot', { body: { question: q, student_id: currentStudent() } });
          out.innerHTML = '';
          out.append(
            h('div', { class: 'msg sys', style: 'max-width:100%' }, r.narrative),
            caveatBox(r),
            r.citations.length
              ? h('p', { class: 'hint' }, `来源：${r.citations.map((c) => `${c.title}（${c.kb}库，相似度 ${f3(c.score)}）`).join('；')}`)
              : h('p', { class: 'hint' }, '未检索到项目材料'));
        },
      }, '提问')),
    h('div', { id: 'cp-out', style: 'margin-top:12px' }))));

  const sid = currentStudent();
  const brief = await api(`/api/brief/student/${sid}`);
  root.append(card('本周简报（学生版）', h('div', { style: 'white-space:pre-wrap' }, brief.narrative),
    '学生版重"下一步建议 + 本周破关"；教师版重"异常与需介入事项"'));
}

// ---------------------------------------------------------------- 视图：图谱
async function viewGraph(root) {
  root.append(loading());
  const d = await api('/api/courses/ML/kps');
  root.innerHTML = '';
  const units = {};
  for (const k of d.items) (units[k.unit] = units[k.unit] || []).push(k);
  root.append(h('div', { class: 'grid g3' },
    card('知识点', h('div', { class: 'kpi' }, d.items.length)),
    card('依赖边', h('div', { class: 'kpi' }, d.items.reduce((a, k) => a + k.prereqs.length, 0))),
    card('章节', h('div', { class: 'kpi' }, Object.keys(units).length)),
  ));
  root.append(h('div', { class: 'notice info' },
    '知识图谱的角色已改变：依赖边不再用于排定教学顺序，而是回答"要做这个，还缺什么"。拓扑序只用于计算缺口内部的先后。'));
  for (const [u, ks] of Object.entries(units)) {
    root.append(card(u, h('div', { class: 'scroll' }, tableOf(
      ['代码', '知识点', '类型', '难度', '前置', '能力模块'],
      ks.map((k) => [
        h('span', { class: 'mono' }, k.code),
        h('a', { href: '#', onclick: (e) => { e.preventDefault(); openKp(k.id); } }, k.name),
        k.kp_type, f3(k.difficulty), k.prereqs.length,
        k.modules.map((m) => m.code).join(' '),
      ])))));
  }
}

async function openKp(kpId) {
  const d = await api(`/api/kps/${kpId}`);
  const body = h('div', {},
    h('p', { class: 'muted' }, d.kp.description),
    h('div', { class: 'flexrow' },
      h('span', { class: 'tag' }, d.kp.code),
      h('span', { class: 'tag' }, d.kp.kp_type),
      h('span', { class: 'tag warn' }, `挡路度 ${d.blocking_severity}`),
      ...d.modules.map((m) => h('span', { class: 'tag accent' }, m.code))),
    h('h4', {}, '前置（学它之前必须先会）'),
    h('div', { class: 'flexrow' }, ...(d.prereqs.length ? d.prereqs.map((p) =>
      h('button', { onclick: () => openKp(p.id) }, p.name)) : [h('span', { class: 'muted' }, '无')])),
    h('h4', {}, '后续（它挡住了什么）'),
    h('div', { class: 'flexrow' }, ...(d.dependents.length ? d.dependents.map((p) =>
      h('button', { onclick: () => openKp(p.id) }, p.name)) : [h('span', { class: 'muted' }, '无')])),
    h('p', { class: 'hint' }, `被 ${d.tasks_requiring.length} 个项目任务需要`),
    h('div', { class: 'flexrow' },
      h('button', { class: 'primary', onclick: () => startAsk(d.kp.id) }, '就这个知识点开始追问')),
  );
  const root = $('#content') || $('#view');
  root.prepend(card(`知识点：${d.kp.name}`, body));
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ---------------------------------------------------------------- 视图：知识宇宙（3D）
async function viewUniverse(root) {
  const sid = currentStudent();
  root.append(loading());
  const url = `/api/universe/ML${isTeacher() || myId() ? `?student_id=${sid}` : ''}`;
  const data = await api(url);
  root.innerHTML = '';

  root.append(card('知识宇宙', h('div', {},
    h('p', { class: 'hint' },
      '依赖边不再是教学顺序表，而是"要做这个还缺什么"的检索索引。'
      + '球体大小是挡路度，颜色是掌握度；琥珀色光环表示"曾经达标、按遗忘曲线已经掉下来"，'
      + '需要复检。左键旋转、滚轮缩放、单击选中、双击飞过去。'),
    h('div', { class: 'flexrow' },
      h('span', { class: 'tag' }, `${data.nodes.length} 知识点`),
      h('span', { class: 'tag' }, `${data.edges.length} 依赖边`),
      h('span', { class: 'tag' }, `最大依赖深度 ${data.max_depth}`),
      h('span', { class: 'tag accent' }, data.course.name)))));

  const wrap = h('div', { class: 'kg-wrap' });
  root.append(wrap);
  root.append(h('p', { class: 'kg-hint' },
    '演示提示：选一个红色的知识点，点【根因回溯】——系统会沿依赖边把整条链点亮并飞过去。'
    + '"他不是反向传播不会，是链式法则没通"这句话，在这里是看得见的一条光路。'));

  try {
    const mod = await import('/graph3d.js');
    if (S.universe) { S.universe.destroy(); S.universe = null; }
    S.universe = mod.mountUniverse(wrap, {
      data,
      fetchRootCause: (kpId) =>
        api(`/api/universe/ML/rootcause/${kpId}?student_id=${sid}`).catch(() => null),
      onAsk: (d) => startAsk(d.id),
    });
  } catch (e) {
    wrap.innerHTML = '';
    wrap.append(h('div', { class: 'notice bad' },
      `3D 视图加载失败：${e.message}。可改用【知识图谱】的表格视图。`));
  }
}

// ---------------------------------------------------------------- 视图：可审计性
async function viewAudit(root) {
  root.append(loading());
  const health = await api('/api/health');
  const cfg = await api('/api/config');
  root.innerHTML = '';
  root.append(h('div', { class: 'grid g3' },
    card('知识点 / 依赖边', h('div', { class: 'kpi' }, `${health.graph.kps} / ${health.graph.edges}`)),
    card('任务-知识点映射', h('div', { class: 'kpi' }, health.graph.task_kp_links)),
    card('依赖图环检测', h('div', { class: 'kpi' },
      health.graph.cycles.length ? h('span', { class: 'tag bad' }, `${health.graph.cycles.length} 个环`)
        : h('span', { class: 'tag ok' }, '无环 ✓'))),
  ));

  const sid = currentStudent();
  const rp = await api(`/api/replay/${sid}`);
  root.append(card('事件流重算校验（Phase 0 验收标准）', h('div', {},
    h('div', { class: 'flexrow' },
      h('span', { class: rp.ok ? 'tag ok' : 'tag bad' }, rp.ok ? '重算一致 ✓' : '存在不一致'),
      h('span', { class: 'tag' }, `事件 ${rp.n_events}`),
      h('span', { class: 'tag' }, `知识点 ${rp.n_kps}`),
      h('span', { class: rp.engagement.match ? 'tag ok' : 'tag bad' },
        `参与度重算${rp.engagement.match ? '一致' : '不一致'}`)),
    h('p', { class: 'hint' },
      '掌握度不是模型说的，是事件算出来的。任何人都可以拿走事件流自己重算一遍——这是系统可被第三方核查的基础。'),
    rp.mismatches.length ? tableOf(['知识点', '原因'], rp.mismatches.map((m) => [m.kp_id, m.reason])) : null)));

  root.append(h('div', { class: 'grid g2' },
    card('当前教学参数（均可配置，初值待教师团队拍板）', tableOf(['参数', '取值'], [
      ['掌握度阈值', cfg.mastery_threshold],
      ['一次推送缺口上限', cfg.gap_push_limit],
      ['追问降级尝试次数', cfg.escalation_attempts],
      ['回归判定中断天数', cfg.comeback_gap_days],
      ['报告数据充分性下限', cfg.min_evidence_for_report],
      ['BKT 默认参数', JSON.stringify(cfg.bkt_default)],
    ])),
    card('可替换 / 可积累的分界', h('div', {},
      h('div', { class: 'rowline' }, h('span', {}, '大模型'),
        h('span', {}, h('span', { class: 'tag' }, '可替换'),
          h('span', { class: health.llm.degraded ? 'tag warn' : 'tag ok' },
            health.llm.degraded ? '离线降级表达器' : health.llm.model))),
      h('div', { class: 'rowline' }, h('span', {}, '知识图谱'),
        h('span', {}, h('span', { class: 'tag ok' }, '可积累'), `${health.graph.kps} 节点`)),
      h('div', { class: 'rowline' }, h('span', {}, '学生状态历史'),
        h('span', {}, h('span', { class: 'tag ok' }, '可积累'), `${health.students} 名学生`)),
      h('div', { class: 'rowline' }, h('span', {}, '检索知识库'),
        h('span', {}, h('span', { class: 'tag' }, '可替换'),
          `${health.kb.reduce((a, b) => a + b.chunks, 0)} 块 / 待重算 ${health.stale_embeddings}`)),
      h('p', { class: 'hint' }, '铁律：任何可积累的东西，都不得依赖任何可替换的东西的内部实现'))),
  ));
}

// ---------------------------------------------------------------- 视图：教师简报 & 学分映射
async function viewTeacherOps(root) {
  root.append(loading());
  const [brief, eng, opens, usage] = await Promise.all([
    api('/api/brief/teacher'), api('/api/engagement'), api('/api/teacher/opens'), api('/api/llm/usage'),
  ]);
  root.innerHTML = '';
  root.append(card('教师周期简报', h('div', {},
    h('div', { style: 'white-space:pre-wrap' }, brief.narrative),
    h('div', { class: 'notice info' }, brief.plan.note),
    h('h4', {}, '建议主动联系的学生'),
    tableOf(['学号', '姓名', '近 7 天活跃', '最后活跃'],
      brief.plan.need_contact.map((s) => [s.sid, s.name, s.active_days, s.last_active || '-'])))));

  root.append(h('div', { class: 'grid g2' },
    card('主动使用率（Phase 1 验收指标）', h('div', {},
      h('div', { class: 'kpi' }, pct(eng.active_rate), h('small', {}, `${eng.n_active}/${eng.n_students}`)),
      h('p', { class: 'hint' }, 'Khanmigo 的教训：最大难题在行为不在算法；学生周活跃率 >50% 是 Phase 3 的验收线'),
      h('h4', {}, '教师主动打开诊断报告'),
      tableOf(['工号', '报告', '近 7 天打开次数'],
        opens.items.map((o) => [o.teacher_code, o.report_kind, o.opens])),
      h('p', { class: 'hint' }, opens.note))),
    card('大模型调用与成本分账', h('div', {},
      tableOf(['用途', '模型', '调用', '输入 tok', '输出 tok', '降级次数'],
        usage.items.map((u) => [u.purpose, u.model_name, u.calls, u.tokens_in, u.tokens_out, u.degraded_calls])),
      h('p', { class: 'hint' }, '每次调用都记录 model_name / prompt_hash / token_usage，换模型不影响已积累的数据'))),
  ));

  root.append(card('项目-课程学分映射建议', h('div', {},
    h('p', { class: 'hint' }, '语义匹配叠加规则校验（单项目学分上限、必修不可完全替代、覆盖不足不认定）；系统只出建议，学术指导委员会审核。'),
    h('textarea', { id: 'cm-in', rows: 4, placeholder: '粘贴项目描述与技术栈…' },
      ),
    h('div', { class: 'composer' },
      h('button', {
        class: 'primary',
        onclick: async () => {
          const out = $('#cm-out'); out.innerHTML = ''; out.append(loading());
          const r = await api('/api/credit-map', { body: { description: $('#cm-in').value } });
          out.innerHTML = '';
          out.append(tableOf(['课程', '知识点覆盖', '建议学分', '触发的规则'],
            r.plan.suggestions.map((s) => [s.course_name, pct(s.coverage), s.suggested_credit,
              s.rules_applied.join('；') || '-'])),
            h('div', { class: 'notice' }, r.caveat));
        },
      }, '生成建议')),
    h('div', { id: 'cm-out' }))));
}

// ---------------------------------------------------------------- 骨架
function card(title, body, hint) {
  return h('div', { class: 'card' }, h('h3', {}, title),
    hint ? h('p', { class: 'hint' }, hint) : null, body);
}

function tableOf(head, rows) {
  return h('table', {},
    h('thead', {}, h('tr', {}, ...head.map((x) => h('th', {}, x)))),
    h('tbody', {}, ...rows.map((r) => h('tr', {}, ...r.map((c) => h('td', {}, c))))));
}

const TABS = [
  { id: 'class', name: '班级诊断', role: 'teacher', view: viewClassDiagnosis },
  { id: 'errors', name: '错误模式库', role: 'teacher', view: viewErrors },
  { id: 'review', name: '代码审查', role: 'any', view: viewReview, student: true },
  { id: 'ops', name: '教师简报 / 学分映射', role: 'teacher', view: viewTeacherOps },
  { id: 'me', name: '我的状态', role: 'any', view: viewMe, student: true },
  { id: 'pull', name: '拉取式任务', role: 'any', view: viewPull, student: true },
  { id: 'ask', name: '引导追问', role: 'any', view: viewAsk, student: true },
  { id: 'copilot', name: '学习副驾驶', role: 'any', view: viewCopilot, student: true },
  { id: 'universe', name: '知识宇宙 3D', role: 'any', view: viewUniverse, student: true },
  { id: 'graph', name: '知识图谱', role: 'any', view: viewGraph },
  { id: 'audit', name: '可审计性', role: 'any', view: viewAudit, student: true },
];

function visibleTabs() {
  return TABS.filter((t) => t.role === 'any' || (t.role === 'teacher' && isTeacher()));
}

function renderTabs() {
  const nav = $('#tabs'); nav.innerHTML = '';
  for (const t of visibleTabs()) {
    nav.append(h('button', {
      class: t.id === S.tab ? 'active' : '',
      onclick: () => { S.tab = t.id; render(); },
    }, t.name));
  }
}

async function render() {
  if (S.universe) { S.universe.destroy(); S.universe = null; }
  const tab = visibleTabs().find((t) => t.id === S.tab) || visibleTabs()[0];
  S.tab = tab.id;
  renderTabs();
  const main = $('#view');
  main.innerHTML = '';
  const picker = h('div', { id: 'picker' });
  const content = h('div', { id: 'content' });
  main.append(picker, content);
  renderPicker(picker, Boolean(tab.student));
  try {
    await tab.view(content);
  } catch (e) {
    content.innerHTML = '';
    content.append(h('div', { class: 'notice bad' }, `出错：${e.message}`));
  }
}

async function boot() {
  document.documentElement.dataset.theme = localStorage.getItem('aiedu.theme') || 'light';
  $('#btn-theme').onclick = () => {
    const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = t;
    localStorage.setItem('aiedu.theme', t);
  };

  S.health = await api('/api/health').catch(() => null);
  if (S.health) {
    const b = $('#llm-badge');
    b.textContent = S.health.llm.degraded ? '大模型：离线降级' : `大模型：${S.health.llm.model}`;
    b.className = 'badge ' + (S.health.llm.degraded ? 'off' : 'on');
    $('#foot-stats').textContent =
      `${S.health.graph.kps} 知识点 · ${S.health.graph.edges} 依赖边 · ${S.health.graph.task_kp_links} 任务映射 · ${S.health.students} 名学生`;
  }

  // 身份切换（演示用；接统一身份认证后替换）
  const sel = $('#identity');
  const opts = [['teacher:T001', '教师 · 张导师（实验班A）'], ['teacher:T003', '教师 · 王主任（全部班级）']];
  try {
    const tk = S.token; S.token = 'teacher:T003';
    const st = await api('/api/students');
    for (const s of st.items.slice(0, 8)) opts.push([`student:${s.sid}`, `学生 · ${s.name}（${s.sid}）`]);
    S.token = tk;
  } catch (e) { /* 尚未生成演示数据 */ }
  for (const [v, n] of opts) sel.append(h('option', { value: v, selected: v === S.token ? '' : null }, n));
  sel.onchange = async () => {
    S.token = sel.value; localStorage.setItem('aiedu.token', S.token);
    S.me = null; S.session = null; S.students = null; S.pickStudent = null;
    await loadMe();
    if (isTeacher()) {
      const d = await api('/api/students').catch(() => ({ items: [] }));
      S.students = d.items;
    }
    S.tab = null; render();
  };
  await loadMe();
  if (isTeacher()) {
    const d = await api('/api/students').catch(() => ({ items: [] }));
    S.students = d.items;
  }
  render();
}

async function loadMe() {
  const who = await api('/api/whoami').catch(() => null);
  if (!who) { S.me = null; return; }
  if (who.role === 'student') {
    S.me = who;
    S.klass = who.klass;
  } else {
    S.me = null;
    // 教师的可见班级白名单为空时代表可见全部
    S.klass = (who.klasses && who.klasses[0]) || '';
  }
}

boot();
