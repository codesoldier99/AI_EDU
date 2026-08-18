/* 考场前端。独立于教学系统的 app.js —— 考试页不该有任何通往别处的入口。
 *
 * 三条实现纪律，都是为了"别丢答案"：
 *   1. 倒计时以**服务端**返回的 remaining_sec 为准，本机时钟只用于两次心跳之间的插值。
 *      改本机时间是最低成本的作弊方式，也是最容易让学生误判剩余时间的方式。
 *   2. 每次作答立刻保存一次，另有 15 秒周期保存兜底；保存失败要**显式报红**，
 *      不能让学生以为存上了。
 *   3. 切题、失焦、关页面前都触发保存。
 */
'use strict';

(() => {
  const S = { token: null, view: null, cur: 0, dirty: new Map(), timer: null, left: 0 };
  const $ = (s) => document.querySelector(s);
  const root = () => $('#root');

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

  async function api(path, body) {
    const res = await fetch(path, {
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json', 'X-Auth-Token': S.token || '' },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({ error: '响应异常' }));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  // ---------------------------------------------------------------- 登录
  function viewLogin(msg) {
    root().innerHTML = '';
    root().append(h('div', { class: 'exam-login' },
      h('h1', {}, '院长实验班选拔考试'),
      h('p', { class: 'sub' }, '请使用监考老师发放的准考条上的学号与口令登录。口令一场考试只用一次。'),
      msg ? h('div', { class: 'notice bad' }, msg) : null,
      h('label', {}, '考试代码'),
      h('input', { id: 'code', value: new URLSearchParams(location.search).get('exam') || 'ML-SELECT-2026' }),
      h('label', {}, '学号'),
      h('input', { id: 'sid', placeholder: '如 2026001', autocomplete: 'off' }),
      h('label', {}, '准考口令（6 位）'),
      h('input', { id: 'ticket', placeholder: '区分不了 0 和 O 时请举手', maxlength: '8', autocomplete: 'off' }),
      h('button', { class: 'primary', onclick: doLogin }, '进入考场')));
    $('#ticket').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
  }

  async function doLogin() {
    const code = $('#code').value.trim();
    const sid = $('#sid').value.trim();
    const ticket = $('#ticket').value.trim().toUpperCase();
    if (!sid || !ticket) return viewLogin('学号与口令都要填。');
    try {
      const r = await api('/api/exam/login', { exam_code: code, sid, ticket });
      S.token = r.token;
      sessionStorage.setItem('exam.token', r.token);
      await loadPaper();
    } catch (e) { viewLogin(e.message); }
  }

  // ---------------------------------------------------------------- 卷面
  async function loadPaper() {
    S.view = await api('/api/exam/paper');
    if (S.view.status !== 'open') return viewDone(S.view.status);
    S.left = S.view.remaining_sec;
    renderPaper();
    startClock();
  }

  function renderPaper() {
    const v = S.view;
    root().innerHTML = '';
    root().append(
      h('div', { class: 'exam-top' },
        h('div', {},
          h('b', {}, v.title),
          h('div', { class: 'who' }, `${v.student_name}（${v.sid}） · 满分 ${v.total_score} 分 · 共 ${v.items.length} 题`)),
        h('div', { class: 'exam-clock', id: 'clock' }, '--:--'),
        h('div', { class: 'save-state', id: 'saved' }, '')),
      h('div', { class: 'exam-wrap' },
        h('div', { id: 'qs' }, ...v.items.map((it, i) => renderQ(it, i))),
        h('div', { class: 'exam-nav' },
          h('h4', {}, '答题卡'),
          h('div', { class: 'navgrid', id: 'navgrid' },
            ...v.items.map((it, i) => h('button', {
              id: `nav-${i}`,
              class: (it.response || '').trim() ? 'done' : '',
              onclick: () => document.getElementById(`q-${i}`).scrollIntoView({ behavior: 'smooth' }),
            }, i + 1))),
          h('p', { class: 'hint', style: 'margin-top:10px' },
            '蓝色 = 已作答。倒计时归零会自动交卷，已保存的答案照常计分。'),
          h('div', { class: 'exam-submit' },
            h('button', { class: 'primary', onclick: confirmSubmit }, '交卷')))));
  }

  function renderQ(it, i) {
    const body = [];
    if (it.itype === 'single' || it.itype === 'judge') {
      for (const opt of it.options) {
        const letter = String(opt).trim().slice(0, 1).toUpperCase();
        body.push(h('label', {
          class: 'opt' + (it.response === letter ? ' sel' : ''),
          onclick: () => setTimeout(() => refreshSel(i), 0),
        },
          h('input', {
            type: 'radio', name: `q${it.question_id}`, value: letter,
            checked: it.response === letter ? '' : null,
            onchange: (e) => onAnswer(i, e.target.value),
          }), opt));
      }
    } else if (it.itype === 'fill') {
      body.push(h('input', {
        class: 'fill', value: it.response || '', placeholder: '填写答案',
        oninput: (e) => onAnswer(i, e.target.value),
        onblur: flush,
      }));
    } else {
      body.push(h('textarea', {
        value: it.response || '',
        placeholder: it.itype === 'program' ? '在此写出完整实现…' : '写出运行结果…',
        oninput: (e) => onAnswer(i, e.target.value),
        onblur: flush,
      }));
    }
    // 题干里的代码块用 <pre> 呈现：缩进对程序阅读题是语义的一部分
    const [head, ...rest] = String(it.stem).split('\n\n');
    return h('div', { class: 'q', id: `q-${i}` },
      h('div', { class: 'qhead' },
        h('span', { class: 'qno' }, `${i + 1}.`),
        h('span', { class: 'tag accent' }, it.itype_label),
        h('span', { class: 'tag' }, `${it.points} 分`)),
      h('div', { class: 'stem' }, head),
      ...rest.map((seg) => (/^\s*(import|def|X |Y |a =|print|from)/m.test(seg)
        ? h('pre', {}, seg) : h('div', { class: 'stem' }, seg))),
      ...body);
  }

  function refreshSel(i) {
    const box = document.getElementById(`q-${i}`);
    if (!box) return;
    box.querySelectorAll('.opt').forEach((el) => {
      const r = el.querySelector('input');
      el.classList.toggle('sel', r && r.checked);
    });
  }

  // ---------------------------------------------------------------- 保存
  function onAnswer(i, value) {
    const it = S.view.items[i];
    it.response = value;
    S.dirty.set(it.question_id, value);
    const nav = document.getElementById(`nav-${i}`);
    if (nav) nav.classList.toggle('done', String(value).trim() !== '');
    mark('待保存…');
    flushSoon();
  }

  let soon = null;
  function flushSoon() { clearTimeout(soon); soon = setTimeout(flush, 800); }

  async function flush() {
    if (!S.dirty.size) return;
    const batch = [...S.dirty.entries()];
    S.dirty.clear();
    try {
      for (const [qid, val] of batch) {
        await api('/api/exam/save', { question_id: qid, response: val });
      }
      mark('已保存');
    } catch (e) {
      // 保存失败必须让学生看见，并把内容放回待保存队列重试
      for (const [qid, val] of batch) if (!S.dirty.has(qid)) S.dirty.set(qid, val);
      mark('保存失败，正在重试', true);
    }
  }

  function mark(text, bad) {
    const el = $('#saved');
    if (!el) return;
    el.textContent = text;
    el.className = 'save-state' + (bad ? ' bad' : '');
  }

  // ---------------------------------------------------------------- 计时
  function startClock() {
    tick();
    clearInterval(S.timer);
    S.timer = setInterval(tick, 1000);
    setInterval(heartbeat, 15000);
    setInterval(flush, 15000);
  }

  function tick() {
    S.left = Math.max(0, S.left - 1);
    const el = $('#clock');
    if (el) {
      const m = String(Math.floor(S.left / 60)).padStart(2, '0');
      const s = String(S.left % 60).padStart(2, '0');
      el.textContent = `${m}:${s}`;
      el.className = 'exam-clock' + (S.left <= 60 ? ' danger' : S.left <= 300 ? ' warn' : '');
    }
    if (S.left <= 0) { clearInterval(S.timer); autoSubmit(); }
  }

  async function heartbeat() {
    try {
      const r = await api('/api/exam/heartbeat');
      S.left = r.remaining_sec;          // 服务端说了算
      if (r.status !== 'open') { clearInterval(S.timer); viewDone(r.status); }
    } catch (e) { /* 断网时保持本地倒计时，等下次心跳纠正 */ }
  }

  // ---------------------------------------------------------------- 交卷
  function confirmSubmit() {
    const blank = S.view.items.filter((it) => !(it.response || '').trim()).length;
    const msg = blank
      ? `还有 ${blank} 题没有作答。交卷后不能再修改，确定交卷吗？`
      : '所有题目都已作答。交卷后不能再修改，确定交卷吗？';
    if (window.confirm(msg)) doSubmit('student');
  }

  async function autoSubmit() { await doSubmit('expired'); }

  async function doSubmit(reason) {
    try { await flush(); } catch (e) { /* 尽力保存 */ }
    try { await api('/api/exam/submit', {}); } catch (e) { /* 服务端超时会自动收卷 */ }
    viewDone(reason === 'expired' ? 'expired' : 'submitted');
  }

  function viewDone(status) {
    clearInterval(S.timer);
    sessionStorage.removeItem('exam.token');
    root().innerHTML = '';
    root().append(h('div', { class: 'exam-done' },
      h('h2', {}, status === 'expired' ? '考试时间已到，已自动交卷' : '交卷成功'),
      h('p', { class: 'muted' }, '你的答案已经保存。成绩与录取结果由学院统一公布。'),
      h('p', { class: 'hint' }, '现在可以关闭页面了。')));
  }

  // ---------------------------------------------------------------- 启动
  window.addEventListener('beforeunload', (e) => {
    if (S.token && S.view && S.view.status === 'open') {
      flush();
      e.preventDefault();
      e.returnValue = '';
    }
  });

  const saved = sessionStorage.getItem('exam.token');
  if (saved) {
    S.token = saved;
    loadPaper().catch(() => { S.token = null; viewLogin(); });
  } else {
    viewLogin();
  }
})();
