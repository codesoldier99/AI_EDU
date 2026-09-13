/* 学生报名页：扫码即填，不需要登录。
 *
 * 只收两件事——姓名、手机号。入口上每多一步，会场上就少一批人填完，
 * 所以刻意不做账号、不做验证码、不做回显。
 */
'use strict';

const S = { sending: false, error: '', total: null };
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function source() {
  return new URLSearchParams(location.search).get('from') || 'enroll-page';
}

const CHECK_SVG = `<svg viewBox="0 0 24 24" fill="none">
  <circle cx="12" cy="12" r="11" stroke="#0A7A6F" stroke-width="1.6"/>
  <path d="M7 12.5l3 3 7-7.5" stroke="#0A7A6F" stroke-width="2.2"
    stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function render() {
  $('#root').innerHTML = `
    <div class="en-wrap">
      <div class="en-hero">
        <span class="en-badge">院长实验班 · 报名通道</span>
        <h1>人工智能卓越工程师<br>实验班报名</h1>
        <p>填一下姓名和手机号，先把你截住——<br>
           后续的宣讲、备考时间与笔试安排会另行通知。</p>
      </div>
      <form class="en-card" id="en-form" novalidate>
        <div class="en-field">
          <label for="f-name">姓名</label>
          <input id="f-name" autocomplete="name" maxlength="40" placeholder="你的姓名">
        </div>
        <div class="en-field">
          <label for="f-phone">手机号</label>
          <input id="f-phone" type="tel" inputmode="numeric" autocomplete="tel"
            maxlength="13" placeholder="11 位手机号">
        </div>
        <p class="en-err" id="en-err">${esc(S.error)}</p>
        <button class="en-submit" id="en-go" ${S.sending ? 'disabled' : ''}>
          ${S.sending ? '提交中…' : '提交报名'}</button>
        <p class="en-count" id="en-count">${
          S.total === null ? '' : `已有 <b>${S.total}</b> 位同学报名`}</p>
      </form>
      <p class="en-note">如遇问题，请找辅导员或直接联系院长工作室</p>
    </div>`;
  $('#en-form').addEventListener('submit', submit);
  // 手机号输错最常见的原因是手滑多打/少打一位——限制成纯数字，省一轮来回
  $('#f-phone').addEventListener('input', (e) => {
    e.target.value = e.target.value.replace(/\D/g, '').slice(0, 11);
  });
}

function validate(name, phone) {
  if (!name) return '请填写姓名';
  if (!/^1\d{10}$/.test(phone)) return '请填写 11 位手机号';
  return '';
}

async function submit(e) {
  e.preventDefault();
  if (S.sending) return;
  const name = $('#f-name').value.trim();
  const phone = $('#f-phone').value.trim();
  const err = validate(name, phone);
  if (err) { S.error = err; return render(); }

  S.sending = true; S.error = ''; render();
  try {
    const res = await fetch('/api/enroll', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, phone, source: source() }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    done(name, data.total);
  } catch (err2) {
    S.sending = false;
    S.error = `提交失败：${err2.message}。可截图发给辅导员，人工登记。`;
    render();
  }
}

function done(name, total) {
  $('#root').innerHTML = `
    <div class="en-wrap">
      <div class="en-card en-done">
        <div class="en-check">${CHECK_SVG}</div>
        <h2>${esc(name)}，报名已收到</h2>
        <p>${total ? `你是第 <span class="en-rank">${total}</span> 位报名的同学<br>` : ''}
           后续安排请留意学院通知与辅导员消息。</p>
      </div>
    </div>`;
}

(async function boot() {
  render();
  try {
    const r = await fetch('/api/enroll/stats');
    if (r.ok) { S.total = (await r.json()).total; render(); }
  } catch { /* 计数只是锦上添花，取不到就不显示，别挡住报名 */ }
})();
