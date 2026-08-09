/* 教师工作台：教学大纲 / 授课计划 / 课件生成（Phase A）。
 *
 * 独立页面而非塞进 /app.js 的横向 tab 栏——原因见 docs 里的说明：
 * 侧边栏三栏布局与现有顶部 tab 栏是两种不同的 DOM 骨架，硬塞会产生双重导航。
 * 鉴权沿用同一套 X-Auth-Token 机制，与 /app.js 共享同一个 localStorage token，
 * 因此从主界面切到这里、或反过来，身份不会丢失。
 */
'use strict';

const S = { token: localStorage.getItem('aiedu.token') || '', me: null,
  courses: [], courseId: null, syllabus: null, sessions: [], deck: null,
  view: 'syllabus' };

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

function caveatBox(o) {
  if (!o) return null;
  if (o.error) return h('div', { class: 'notice bad' }, `⚠ ${o.error}`);
  if (!o.caveat) return null;
  const evid = o.evidence_count ?? 0, conf = o.confidence ?? 0;
  return h('div', { class: 'notice' }, `⚠ ${o.caveat}（证据 ${evid} 条，置信度 ${Number(conf).toFixed(3)}）`);
}

const NAV = [
  { group: '教学设计', items: [
    { id: 'syllabus', label: '教学大纲', icon: '📘', ready: true },
    { id: 'teaching_plan', label: '授课计划', icon: '📅', ready: true },
    { id: 'deck', label: '课件生成', icon: '🖼', ready: true },
    { id: 'exam', label: '试卷与练习', icon: '📝', ready: false },
  ] },
  { group: '学情与评价', items: [
    { id: 'grading', label: '评卷', icon: '✅', ready: false },
    { id: 'analysis', label: '成绩分析', icon: '📊', ready: false },
    { id: 'achievement', label: '达成度评价', icon: '🎯', ready: false },
  ] },
];

function renderSidebar() {
  const groups = NAV.map((g) => h('div', { class: 'tw-group' },
    h('div', { class: 'tw-group-title' }, g.group),
    ...g.items.map((it) => h('button', {
      class: `tw-nav-item ${it.id === S.view ? 'active' : ''} ${it.ready ? '' : 'disabled'}`,
      onclick: () => { if (it.ready) { S.view = it.id; render(); } },
      title: it.ready ? '' : '即将上线',
    }, h('span', { class: 'ic' }, it.icon), it.label,
       it.ready ? null : h('span', { class: 'pill', style: 'margin-left:auto;font-size:10px' }, '待上线')))));
  return h('aside', { class: 'tw-side' },
    h('div', { class: 'tw-side-brand' }, h('span', { class: 'logo' }, '院'),
      h('div', {}, h('b', {}, '教师工作台'), h('small', {}, '院长实验班 AI 教学系统'))),
    ...groups,
    h('div', { class: 'tw-side-foot' },
      '状态由算法维护，大模型只负责表达', h('br', {}), h('a', { href: '/' }, '← 返回学生/教师通用视图')));
}

function courseSelect() {
  return h('select', {
    onchange: (e) => { S.courseId = Number(e.target.value); S.syllabus = null; S.sessions = []; render(); },
  }, ...S.courses.map((c) => h('option', {
    value: c.id, selected: c.id === S.courseId ? '' : null,
  }, `${c.name}（${c.code}）`)));
}

// ---------------------------------------------------------------- 教学大纲
async function viewSyllabus(body) {
  body.append(h('div', { class: 'tw-steps' },
    h('span', { class: 'tw-step now' }, '① 教学大纲'),
    h('span', { class: 'tw-step' }, '② 授课计划'),
    h('span', { class: 'tw-step' }, '③ 课件生成')));

  if (!S.syllabus) {
    try { S.syllabus = await api(`/api/courseware/syllabus/${S.courseId}`); } catch (e) { S.syllabus = null; }
  }

  const card = h('div', { class: 'card' },
    h('h3', {}, '教学大纲'),
    h('p', { class: 'hint' }, '按知识图谱的章节（unit）与拓扑序自动生成骨架；勾选后额外生成每章说明文案。'),
    h('div', { class: 'flexrow' }, courseSelect(),
      h('label', {}, h('input', { type: 'checkbox', id: 'syl-fill' }), ' 生成章节说明文案'),
      h('button', { class: 'primary', onclick: onGenSyllabus }, '生成 / 更新大纲')));
  body.append(card);

  if (!S.syllabus || !S.syllabus.content) {
    body.append(h('div', { class: 'notice info' }, '尚未生成过大纲，点击上方按钮生成。'));
    return;
  }
  const c = S.syllabus.content;
  const meta = h('div', { class: 'card' },
    h('div', { class: 'flexrow' },
      h('span', { class: 'tag accent' }, `第 ${S.syllabus.version} 版`),
      h('span', { class: 'tag' }, S.syllabus.status === 'teacher_confirmed' ? '已确认' : '草稿'),
      h('span', { class: 'muted' }, `共 ${c.chapters.length} 章 · ${c.total_kps} 个知识点`),
      S.syllabus.status !== 'teacher_confirmed'
        ? h('button', { onclick: onConfirmSyllabus }, '确认此版大纲')
        : null),
    ...c.chapters.map((ch) => h('div', { class: 'tw-chapter' },
      h('h4', {}, `第 ${ch.seq} 章 · ${ch.unit}`),
      h('div', { class: 'kps' }, ch.kp_names.join('、')),
      ch.narrative ? h('div', { class: 'narrative' }, ch.narrative) : null)));
  body.append(meta);
}

async function onGenSyllabus() {
  const fill = $('#syl-fill').checked;
  const out = await api('/api/courseware/syllabus', { body: { course_id: S.courseId, fill_content: fill } });
  S.syllabus = { id: out.syllabus_id, version: 1, status: 'draft', content: out.plan };
  render();
}

async function onConfirmSyllabus() {
  await api(`/api/courseware/syllabus/${S.syllabus.id}/confirm`, { body: {} });
  S.syllabus.status = 'teacher_confirmed';
  render();
}

// ---------------------------------------------------------------- 授课计划
async function viewTeachingPlan(body) {
  body.append(h('div', { class: 'tw-steps' },
    h('span', { class: 'tw-step done' }, '① 教学大纲'),
    h('span', { class: 'tw-step now' }, '② 授课计划'),
    h('span', { class: 'tw-step' }, '③ 课件生成')));

  if (!S.syllabus || !S.syllabus.id) {
    body.append(h('div', { class: 'notice bad' }, '请先在「教学大纲」页生成一版大纲。'));
    return;
  }
  const card = h('div', { class: 'card' },
    h('h3', {}, '授课计划'),
    h('p', { class: 'hint' }, `按大纲章节自动拆成一次次课（单次课知识点数超阈值会自动拆分）。当前大纲：第 ${S.syllabus.version} 版。`),
    h('div', { class: 'flexrow' },
      h('label', {}, '每次课时长（分钟）', h('input', { type: 'number', id: 'tp-min', value: 90, style: 'width:70px' })),
      h('label', {}, h('input', { type: 'checkbox', id: 'tp-fill' }), ' 生成教学环节说明'),
      h('button', { class: 'primary', onclick: onGenTeachingPlan }, '生成 / 更新授课计划')));
  body.append(card);

  try { S.sessions = (await api(`/api/courseware/teaching-plan/${S.syllabus.id}`)).items; }
  catch (e) { S.sessions = []; }

  if (!S.sessions.length) {
    body.append(h('div', { class: 'notice info' }, '尚未生成过授课计划。'));
    return;
  }
  body.append(h('div', { class: 'card' },
    ...S.sessions.map((s) => h('div', { class: 'tw-session' },
      h('div', {},
        h('b', {}, `第 ${s.seq} 次 · ${s.title}`),
        h('div', { class: 'meta' }, `${s.kp_codes.length} 个知识点 · ${s.duration_min} 分钟`),
        s.narrative ? h('div', { class: 'meta' }, s.narrative) : null),
      h('button', { onclick: () => { S.view = 'deck'; S.deckSessionId = s.id; S.deck = null; render(); } }, '生成课件 →')))));
}

async function onGenTeachingPlan() {
  const minutes = Number($('#tp-min').value) || 90;
  const fill = $('#tp-fill').checked;
  await api('/api/courseware/teaching-plan', {
    body: { syllabus_id: S.syllabus.id, session_minutes: minutes, fill_content: fill },
  });
  render();
}

// ---------------------------------------------------------------- 课件生成
async function viewDeck(body) {
  body.append(h('div', { class: 'tw-steps' },
    h('span', { class: 'tw-step done' }, '① 教学大纲'),
    h('span', { class: 'tw-step done' }, '② 授课计划'),
    h('span', { class: 'tw-step now' }, '③ 课件生成')));

  if (!S.sessions.length) {
    body.append(h('div', { class: 'notice bad' }, '请先在「授课计划」页生成计划，再从某次课点「生成课件」。'));
    return;
  }
  if (!S.deckSessionId) S.deckSessionId = S.sessions[0].id;

  const card = h('div', { class: 'card' },
    h('h3', {}, '课件生成'),
    h('p', { class: 'hint' }, '每个知识点一页要点骨架；有官方渲染器（OfficeCLI）用官方引擎出 pptx，没有则用内建引擎降级出 pptx，从不因为外部工具缺失而拿不到课件。'),
    h('div', { class: 'flexrow' },
      h('select', { onchange: (e) => { S.deckSessionId = Number(e.target.value); } },
        ...S.sessions.map((s) => h('option', {
          value: s.id, selected: s.id === S.deckSessionId ? '' : null,
        }, `第 ${s.seq} 次 · ${s.title}`))),
      h('label', {}, h('input', { type: 'checkbox', id: 'deck-fill', checked: '' }), ' 生成正式文案（否则只出结构骨架）'),
      h('button', { class: 'primary', onclick: onGenDeck }, '生成课件')));
  body.append(card);

  if (!S.deck) return;
  body.append(caveatBox(S.deck) || h('div', {}));
  const meta = S.deck.plan;
  body.append(h('div', { class: 'card' },
    h('div', { class: 'flexrow' },
      h('span', { class: 'tag' }, meta.render_tool === 'officecli' ? '官方渲染引擎' : '内建降级引擎'),
      h('span', { class: 'muted' }, meta.render_tool_version || ''),
      meta.missing_kp_codes && meta.missing_kp_codes.length
        ? h('span', { class: 'tag bad' }, `${meta.missing_kp_codes.length} 个知识点未覆盖`)
        : h('span', { class: 'tag ok' }, '知识点全覆盖'),
      h('a', { href: `/api/courseware/deck/${meta.deck_id}/file?token=${encodeURIComponent(S.token)}`,
               target: '_blank' }, h('button', {}, '下载课件文件'))),
  ));
}

async function onGenDeck() {
  const fill = $('#deck-fill').checked;
  const out = await api('/api/courseware/deck', {
    body: { teaching_plan_id: S.deckSessionId, fill_content: fill },
  });
  S.deck = out;
  render();
}

// ---------------------------------------------------------------- 占位页
function viewSoon(body, label) {
  body.append(h('div', { class: 'tw-soon' },
    h('b', {}, `${label} · 即将上线`),
    '这一块正在按同一套「确定性计算 + 大模型只表达」的方式建设，敬请期待。'));
}

// ---------------------------------------------------------------- 壳
async function render() {
  document.body.innerHTML = '';
  const main = h('main', { class: 'tw-main' },
    h('header', { class: 'tw-topbar' },
      h('div', {}, h('h2', {}, NAV.flatMap((g) => g.items).find((i) => i.id === S.view)?.label || ''),
        h('div', { class: 'sub' }, '福建理工大学 人工智能与交通工程学院 · 院长工作室实验班')),
      h('div', { class: 'who' }, h('b', {}, S.me ? S.me.name : ''), S.me ? `${S.me.code}` : '')),
    h('div', { class: 'tw-body', id: 'tw-body' }));
  document.body.append(h('div', { class: 'tw-shell' }, renderSidebar(), main));
  const body = $('#tw-body');
  try {
    if (S.view === 'syllabus') await viewSyllabus(body);
    else if (S.view === 'teaching_plan') await viewTeachingPlan(body);
    else if (S.view === 'deck') await viewDeck(body);
    else viewSoon(body, NAV.flatMap((g) => g.items).find((i) => i.id === S.view)?.label || '');
  } catch (e) {
    body.append(h('div', { class: 'notice bad' }, `⚠ ${e.message}`));
  }
}

async function boot() {
  if (!S.token || !S.token.startsWith('teacher:')) {
    document.body.innerHTML = '';
    document.body.append(h('div', { class: 'tw-soon', style: 'margin:60px auto;max-width:480px' },
      h('b', {}, '该工作台仅限教师访问'),
      '请先在主界面用教师身份登录后再进入。',
      h('div', { style: 'margin-top:14px' }, h('a', { href: '/' }, h('button', { class: 'primary' }, '返回主界面')))));
    return;
  }
  try {
    S.me = await api('/api/whoami');
  } catch (e) {
    localStorage.removeItem('aiedu.token');
    location.href = '/';
    return;
  }
  S.courses = (await api('/api/courseware/courses')).items;
  if (S.courses.length) S.courseId = S.courses[0].id;
  render();
}

boot();
