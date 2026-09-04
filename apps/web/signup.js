/* 报名页：会场上扫码就能填，不需要登录。
 *
 * 只做一件事——把姓名、参与方式和联系方式收下来。
 * 刻意不做账号、不做验证码、不做回显：入口上每多一步，会场上就少一批人填完。
 */
'use strict';

const ROLES = [
  { id: 'mentor', name: '① 当项目导师',
    desc: '带自己在研的横向 / 纵向课题进来当教学载体。你出题目与验收标准，'
        + '系统负责把它拆成可判定的任务；学生产出可折算集中实践学分。' },
  { id: 'review', name: '② 审知识点与映射',
    desc: '审自己课程的知识点粒度与措辞，审候选映射（每人 20 条即可），'
        + '或写一个 Markdown 教学技能包。一到两个下午。' },
  { id: 'dev', name: '③ 一起做开发',
    desc: '零构建前端、纯标准库后端，克隆下来就能跑。适配器、题库、'
        + '可视化都是独立可做的口子。' },
];

const GITEE = 'https://gitee.com/ritchiezheng_admin/AI_EDU';

const S = { roles: new Set(), sending: false, error: '', total: null };
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function source() {
  return new URLSearchParams(location.search).get('from') || 'signup-page';
}

function render() {
  $('#root').innerHTML = `
    <div class="su-wrap">
      <div class="su-head">
        <h1>院长实验班 · 参与报名</h1>
        <p>AI 海啸已经到岸。这条船正在造，缺的是人。<br>
           三种参与方式，选一种就行，都不要求先懂 AI。</p>
      </div>
      <form class="su-card" id="su-form" novalidate>
        <div class="su-field">
          <label for="f-name">姓名 <span class="hint">必填</span></label>
          <input id="f-name" autocomplete="name" maxlength="40">
        </div>
        <div class="su-field">
          <label for="f-dept">教研室 / 系 <span class="hint">选填</span></label>
          <input id="f-dept" maxlength="60">
        </div>
        <div class="su-field">
          <label for="f-contact">手机或邮箱 <span class="hint">选填，方便会后联系</span></label>
          <input id="f-contact" maxlength="80">
        </div>
        <div class="su-field">
          <label>参与方式 <span class="hint">可多选，至少选一项</span></label>
          ${ROLES.map((r) => `
            <label class="su-role${S.roles.has(r.id) ? ' on' : ''}" data-role="${r.id}">
              <input type="checkbox" ${S.roles.has(r.id) ? 'checked' : ''}>
              <span><b>${esc(r.name)}</b><span>${esc(r.desc)}</span></span>
            </label>`).join('')}
        </div>
        <div class="su-field">
          <label for="f-topic">想带的项目 / 想审的课程 / 想做的模块
            <span class="hint">选填，一句话就够</span></label>
          <textarea id="f-topic" maxlength="300"></textarea>
        </div>
        <p class="su-err" id="su-err">${esc(S.error)}</p>
        <button class="su-submit" id="su-go" ${S.sending ? 'disabled' : ''}>
          ${S.sending ? '提交中…' : '提交报名'}</button>
        <p class="su-count" id="su-count">${
          S.total === null ? '' : `已有 ${S.total} 位老师报名`}</p>
      </form>
      <div class="su-card su-links">
        <a href="${GITEE}" target="_blank" rel="noreferrer noopener">
          <b>代码仓库（Gitee）→</b>
          <span>${GITEE}<br>克隆下来 <code>make setup</code> 就能跑，无需外网、无需 API Key。</span></a>
        <a href="/">
          <b>先看看系统 →</b>
          <span>知识宇宙、拉取式任务、学习工作台，讲的每个数字都能当场点开核对。</span></a>
      </div>
    </div>`;

  for (const el of document.querySelectorAll('.su-role')) {
    el.addEventListener('click', (e) => {
      // 点在原生 checkbox 上时让它自己翻，别再翻一次把结果抵消掉
      const id = el.dataset.role;
      if (e.target.tagName !== 'INPUT') e.preventDefault();
      if (S.roles.has(id)) S.roles.delete(id); else S.roles.add(id);
      render();
    });
  }
  $('#su-form').addEventListener('submit', submit);
}

async function submit(e) {
  e.preventDefault();
  if (S.sending) return;
  const body = {
    name: $('#f-name').value.trim(),
    dept: $('#f-dept').value.trim(),
    contact: $('#f-contact').value.trim(),
    topic: $('#f-topic').value.trim(),
    roles: [...S.roles],
    source: source(),
  };
  if (!body.name) { S.error = '请填写姓名'; return render(); }
  if (!body.roles.length) { S.error = '请至少选择一种参与方式'; return render(); }

  S.sending = true; S.error = ''; render();
  try {
    const res = await fetch('/api/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    done(body.name, data.total);
  } catch (err) {
    S.sending = false;
    S.error = `提交失败：${err.message}。可截图发给郑积仕老师，人工登记。`;
    render();
  }
}

function done(name, total) {
  $('#root').innerHTML = `
    <div class="su-wrap">
      <div class="su-card su-done">
        <div class="tick">✅</div>
        <h2>${esc(name)}老师，报名已收到</h2>
        <p class="su-count">${total ? `你是第 ${total} 位` : ''}
          <br>我们会在会后一周内联系你，约一次半小时的对接。</p>
      </div>
      <div class="su-card su-links">
        <a href="${GITEE}" target="_blank" rel="noreferrer noopener">
          <b>代码仓库（Gitee）→</b><span>${GITEE}</span></a>
        <a href="/"><b>先看看系统 →</b><span>讲的每个数字都能当场点开核对</span></a>
      </div>
    </div>`;
}

(async function boot() {
  render();
  try {
    const r = await fetch('/api/signup/stats');
    if (r.ok) { S.total = (await r.json()).total; render(); }
  } catch { /* 计数只是锦上添花，取不到就不显示，别挡住报名 */ }
})();
