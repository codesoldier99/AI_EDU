/* 学习工作台 —— 切目标，不切引擎
 *
 * 吸收自 DeepTutor 的一句设计主张：学生换目标时上下文应该跟着走，
 * 而不是换一个入口重新开始。所以这里只有一个标签页，四个能力共用同一个学生上下文。
 *
 * 但产品层面的硬约束一条不松（CLAUDE.md §11）：
 *   - 组卷不显示答案，批改之后才给解析；
 *   - 不显示"本次正确率"这类当场指标，只显示知识点掌握度的变化；
 *   - 分步解题未降级前不下发任何一步的结论；
 *   - 图形全部是服务端算好的 SVG，页面不引入任何图表库。
 *
 * 依赖 app.js 里的 h / api / card / caveatBox / currentStudent 等全局工具，
 * 这些是脚本级 const，跨 <script> 可见；本文件必须先于 app.js 加载。
 */
'use strict';

const STUDY = (() => {
  const MODES = [
    { id: 'practice', name: '出题测练', hint: '练什么由复检 / 待验证 / 任务缺口算出，不是随机刷题' },
    { id: 'solve', name: '分步解题', hint: '一次只给一步，你先答，答不上来才逐级降级' },
    { id: 'research', name: '限域调研', hint: '只查院内三个知识库，不接外网；产出计入交付物，不计入掌握度' },
    { id: 'figure', name: '图示', hint: '图由确定性代码生成，模型只写下面那句解读' },
  ];
  const FIGURES = [
    ['mastery_bars', '掌握度分布'],
    ['retention_curve', '遗忘与复检'],
    ['ability_radar', '能力画像'],
    ['root_cause_chain', '根因链'],
  ];

  const st = { mode: 'practice', paper: null, solveId: null };

  function modeBar(root) {
    return h('div', { class: 'flexrow', style: 'margin-bottom:14px' },
      ...MODES.map((m) => h('button', {
        class: m.id === st.mode ? 'primary' : '',
        onclick: () => { st.mode = m.id; view(root); },
      }, m.name)),
      h('span', { class: 'pill' }, MODES.find((m) => m.id === st.mode).hint));
  }

  async function view(root) {
    root.innerHTML = '';
    root.append(modeBar(root));
    const box = h('div', { id: 'study-box' });
    root.append(box);
    box.append(loading());
    try {
      if (st.mode === 'practice') await viewPractice(box);
      else if (st.mode === 'solve') await viewSolve(box);
      else if (st.mode === 'research') await viewResearch(box);
      else await viewFigure(box);
    } catch (e) {
      box.innerHTML = '';
      box.append(h('div', { class: 'notice bad' }, `出错：${e.message}`));
    }
  }

  // ---------------------------------------------------------------- 出题测练
  async function viewPractice(box) {
    const sid = currentStudent();
    const plan = await api(`/api/practice-plan?student_id=${sid}`);
    box.innerHTML = '';

    const rows = plan.targets.map((t) => [
      t.name,
      h('span', { class: 'tag accent' }, t.kind_label),
      f3(t.p_mastery),
      f3(t.blocking_severity),
      h('span', { class: 'muted' }, t.reason),
    ]);
    box.append(card('此刻该练什么',
      h('div', {},
        plan.targets.length
          ? tableOf(['知识点', '来源', '掌握度', '挡路度', '为什么是它'], rows)
          : h('p', { class: 'muted' }, '当前没有需要练的知识点——没有到期复检、没有待验证、任务也没有缺口。'),
        caveatBox(plan),
        plan.missing_bank && plan.missing_bank.length
          ? h('div', { class: 'notice' },
            `题库缺口：${plan.missing_bank.map((m) => m.name).join('、')} 还没有已审题目，请教师先出题。`)
          : null,
        h('div', { class: 'flexrow', style: 'margin-top:12px' },
          h('button', { class: 'primary', onclick: () => assemble(box) }, '组一份练习'),
          h('span', { class: 'pill' }, plan.note))),
      '排序依据只有四类：到期复检、待跨时间验证、任务缺口、根因点。没有一类是正确率。'));

    box.append(h('div', { id: 'paper-box' }));
  }

  async function assemble(box) {
    const host = $('#paper-box'); host.innerHTML = ''; host.append(loading());
    const paper = await api('/api/quiz/assemble', { body: { student_id: currentStudent() } });
    st.paper = paper;
    host.innerHTML = '';
    if (!paper.questions.length) {
      host.append(h('div', { class: 'notice' },
        '题库里还没有可用的已审题目。模型草案需要教师在「题库审核」里确认后才会发给学生。'));
      return;
    }
    const items = paper.questions.map((q, i) => {
      const why = (paper.reasons.find((r) => r.question_id === q.id) || {});
      return h('div', { class: 'card', style: 'margin-bottom:10px' },
        h('div', { class: 'flexrow' },
          h('span', { class: 'tag accent' }, why.kind_label || ''),
          h('span', { class: 'tag' }, q.kp_name),
          why.repeat ? h('span', { class: 'tag warn' }, '重复题（题库不足）') : null),
        h('p', { style: 'margin:8px 0' }, `${i + 1}. ${q.stem}`),
        q.options && q.options.length
          ? h('div', { class: 'stack' }, ...q.options.map((o) => h('label', { class: 'flexrow' },
            h('input', { type: 'radio', name: `q${q.id}`, value: o.slice(0, 1) }), o)))
          : h('textarea', { id: `ans-${q.id}`, placeholder: '写下你的答案与推导…' }));
    });
    host.append(card(`练习（${paper.questions.length} 题）`, h('div', {},
      paper.narrative ? h('div', { class: 'msg sys', style: 'max-width:100%' }, paper.narrative) : null,
      ...items,
      h('button', { class: 'primary', onclick: () => submit(host) }, '交卷'),
      h('p', { class: 'hint' }, '交卷后才会显示解析。判不了的题会进教师人工队列，不计入掌握度。'))));
  }

  async function submit(host) {
    const answers = {};
    for (const q of st.paper.questions) {
      const radio = document.querySelector(`input[name="q${q.id}"]:checked`);
      const ta = document.getElementById(`ans-${q.id}`);
      answers[q.id] = radio ? radio.value : (ta ? ta.value : '');
    }
    host.innerHTML = ''; host.append(loading());
    const res = await api('/api/quiz/submit', { body: { paper_id: st.paper.paper_id, answers } });
    host.innerHTML = '';
    const rows = res.items.map((it) => [
      it.kp_name,
      it.is_correct === null
        ? h('span', { class: 'tag warn' }, '待教师判定')
        : h('span', { class: it.is_correct ? 'tag ok' : 'tag bad' }, it.is_correct ? '通过' : '未通过'),
      h('span', { class: 'mono' }, it.graded_by),
      it.p_after === null ? '—' : f3(it.p_after),
      h('span', { class: 'muted' }, it.detail || ''),
    ]);
    host.append(card('批改结果', h('div', {},
      tableOf(['知识点', '判定', '判分方式', '掌握度', '说明'], rows),
      h('div', { class: 'notice info' },
        `${res.n_graded} 题由确定性规则判定，${res.n_pending} 题判不了、已转人工。` +
        '这里不显示"本次正确率"——它不是本系统的优化目标。'),
      ...res.items.filter((it) => it.rationale).map((it) => h('div', { class: 'rowline' },
        h('div', {}, h('b', {}, `参考答案：${it.answer}`),
          h('div', { class: 'muted' }, it.rationale)))))));
  }

  // ---------------------------------------------------------------- 分步解题
  async function viewSolve(box) {
    box.innerHTML = '';
    box.append(card('分步解题', h('div', {},
      h('p', { class: 'hint' },
        '把推理链条掰开，但一次只交出一步：你先答，答对才往下走；连着答不上来会逐级降级，' +
        '降到第三级才给出该步结论，并标记需要教师介入。'),
      h('div', { class: 'composer' },
        h('textarea', { id: 'solve-in', placeholder: '把题目粘进来…' }),
        h('button', {
          class: 'primary',
          onclick: async () => {
            const p = $('#solve-in').value.trim(); if (!p) return;
            const host = $('#solve-box'); host.innerHTML = ''; host.append(loading());
            const v = await api('/api/solve/start', { body: { problem: p, student_id: currentStudent() } });
            st.solveId = v.session_id;
            renderSolve(v);
          },
        }, '开始')))));
    box.append(h('div', { id: 'solve-box' }));

    const list = await api(`/api/solve/sessions/${currentStudent()}`);
    if (list.items.length) {
      box.append(card('最近的解题会话', tableOf(
        ['题目', '进度', '降级', '时间'],
        list.items.map((s) => [
          h('a', {
            href: '#', onclick: async (e) => {
              e.preventDefault();
              renderSolve(await api(`/api/solve/${s.id}`));
            },
          }, s.problem.slice(0, 28)),
          `${s.cursor}/${s.n_steps}`,
          s.escalation_level ? `L${s.escalation_level}` : '未降级',
          s.created_at]))));
    }
  }

  function renderSolve(v) {
    const host = $('#solve-box'); host.innerHTML = '';
    const done = v.status === 'done';
    const steps = v.steps.map((s) => {
      const past = s.idx < v.cursor;
      return h('div', { class: 'rowline' },
        h('div', {},
          h('div', {},
            h('span', { class: past ? 'tag ok' : (s.idx === v.cursor ? 'tag accent' : 'tag') },
              `第 ${s.idx + 1} 步`),
            s.kp_name ? h('span', { class: 'tag' }, s.kp_name) : null,
            s.revealed ? h('span', { class: 'tag warn' }, '已降级下发') : null),
          h('div', { style: 'margin-top:4px' }, s.ask || h('span', { class: 'muted' }, '（未到这一步）')),
          s.student_text ? h('div', { class: 'muted' }, `你的作答：${s.student_text}`) : null,
          s.expected ? h('div', { class: 'muted' }, `该步结论：${s.expected}`) : null));
    });
    host.append(card(`解题进度 ${Math.min(v.cursor + 1, v.n_steps)}/${v.n_steps}`, h('div', {},
      h('div', { class: 'flexrow' },
        h('span', { class: v.escalation_level ? 'tag warn' : 'tag ok' },
          v.escalation_level ? `已降级到 L${v.escalation_level}（${v.escalation_label}）` : '未降级'),
        v.gives_answer ? h('span', { class: 'tag bad' }, '已标记需教师介入') : null),
      v.narrative ? h('div', { class: 'msg sys', style: 'max-width:100%;margin:10px 0' }, v.narrative) : null,
      caveatBox(v),
      ...steps,
      done ? h('div', { class: 'notice info' }, '全部步骤已完成。回到项目里把它用一次。')
        : h('div', { class: 'composer' },
          h('textarea', { id: 'solve-ans', placeholder: '写下这一步你算出/想到的结果…' }),
          h('div', { class: 'stack' },
            h('button', {
              class: 'primary',
              onclick: async () => {
                const t = $('#solve-ans').value.trim(); if (!t) return;
                renderSolve(await api('/api/solve/answer', { body: { session_id: v.session_id, text: t } }));
              },
            }, '提交这一步'),
            h('button', {
              onclick: async () => {
                renderSolve(await api('/api/solve/answer',
                  { body: { session_id: v.session_id, text: '我卡住了', stuck: true } }));
              },
            }, '我卡住了'))),
      v.citations && v.citations.length
        ? h('p', { class: 'hint' }, `教材依据：${v.citations.map((c) => c.title).join('、')}`) : null)));
  }

  // ---------------------------------------------------------------- 限域调研
  async function viewResearch(box) {
    box.innerHTML = '';
    box.append(card('限域调研', h('div', {},
      h('p', { class: 'hint' },
        '只检索院内三个知识库（教材 / 项目材料 / 培养方案），不接外网。' +
        '生成后会再量一次落地性，与材料重合率低的段落会被标成「低支撑」。'),
      h('div', { class: 'composer' },
        h('textarea', { id: 'rs-in', placeholder: '例如：AGV 定位误差的常见成因与排查顺序' }),
        h('button', {
          class: 'primary',
          onclick: async () => {
            const t = $('#rs-in').value.trim(); if (!t) return;
            const host = $('#rs-box'); host.innerHTML = ''; host.append(loading());
            renderReport(await api('/api/research', { body: { topic: t, student_id: currentStudent() } }));
          },
        }, '开始调研')))));
    box.append(h('div', { id: 'rs-box' }));

    const notes = await api(`/api/notes/${currentStudent()}`);
    if (notes.items.length) {
      box.append(card('调研记录', tableOf(['主题', '章节', '低支撑', '时间'],
        notes.items.map((n) => [
          h('a', {
            href: '#', onclick: async (e) => {
              e.preventDefault();
              const d = await api(`/api/note/${n.id}`);
              const host = $('#rs-box'); host.innerHTML = '';
              host.append(card(d.topic, h('pre', { class: 'mono', style: 'white-space:pre-wrap' }, d.body_md)));
            },
          }, n.topic),
          n.n_sections,
          n.n_unsourced ? h('span', { class: 'tag warn' }, n.n_unsourced) : '0',
          n.created_at]))));
    }
  }

  function renderReport(rep) {
    const host = $('#rs-box'); host.innerHTML = '';
    if (!rep.sections.length) {
      host.append(h('div', { class: 'notice' }, rep.caveat || '未生成报告'));
      return;
    }
    host.append(card(rep.topic, h('div', {},
      caveatBox(rep),
      ...rep.sections.map((s) => h('div', { style: 'margin-bottom:14px' },
        h('h4', {}, s.title,
          h('span', { class: s.grounded ? 'tag ok' : 'tag bad', style: 'margin-left:8px' },
            `落地性 ${pct(s.groundedness)}`)),
        !s.grounded ? h('div', { class: 'notice bad' }, '低支撑：这一段与材料的重合率偏低，引用前请核对。') : null,
        h('p', {}, s.body),
        h('p', { class: 'hint' }, `来源：${s.citations.map((c) => c.title).join('；')}`))),
      h('p', { class: 'hint' }, rep.note))));
  }

  // ---------------------------------------------------------------- 图示
  async function viewFigure(box) {
    box.innerHTML = '';
    const sel = h('select', { id: 'fig-kind' },
      ...FIGURES.map(([v, n]) => h('option', { value: v }, n)));
    box.append(card('图示', h('div', {},
      h('div', { class: 'flexrow' }, sel,
        h('button', { class: 'primary', onclick: () => drawFigure() }, '生成')),
      h('div', { id: 'fig-box', style: 'margin-top:12px' }))));
    await drawFigure();
  }

  async function drawFigure() {
    const host = $('#fig-box'); host.innerHTML = ''; host.append(loading());
    const kind = $('#fig-kind').value;
    const fig = await api(`/api/figure?kind=${kind}&student_id=${currentStudent()}`);
    host.innerHTML = '';
    host.append(
      h('h4', {}, fig.title),
      h('div', { html: fig.svg, style: 'margin:10px 0' }),
      caveatBox(fig),
      h('p', {}, fig.caption),
      fig.mermaid ? h('details', {}, h('summary', { class: 'muted' }, 'Mermaid 源码（贴进文档用）'),
        h('pre', { class: 'mono', style: 'white-space:pre-wrap' }, fig.mermaid)) : null,
      h('p', { class: 'hint' }, fig.note));
  }

  // ---------------------------------------------------------------- 教师：题库
  async function viewBank(root) {
    root.innerHTML = '';
    const q = await api('/api/quiz/review-queue');
    const s = q.stats;
    root.append(h('div', { class: 'grid g3' },
      card('可用题目', h('div', { class: 'kpi' }, s.usable, h('small', {}, `覆盖 ${s.kp_covered} 个知识点`))),
      card('待审草案', h('div', { class: 'kpi' }, s.pending, h('small', {}, '模型出的题必须过这一关'))),
      card('已退役', h('div', { class: 'kpi' }, s.retired, h('small', {}, '退役不删除，历史证据仍可解释')))));

    root.append(card('待审题目', q.items.length
      ? h('div', {}, ...q.items.map((item) => h('div', { class: 'card', style: 'margin-bottom:10px' },
        h('div', { class: 'flexrow' },
          h('span', { class: 'tag' }, item.kp_name),
          h('span', { class: 'tag' }, item.qtype),
          h('span', { class: 'tag accent' }, `来源 ${item.origin}`)),
        h('p', { style: 'margin:8px 0' }, item.stem),
        item.options.length ? h('div', { class: 'stack' }, ...item.options.map((o) => h('div', {}, o))) : null,
        h('p', { class: 'muted' }, `答案：${item.answer}｜解析：${item.rationale}`),
        h('p', { class: 'hint' }, `教材依据：${(item.citations || []).map((c) => c.title).join('、') || '（无）'}`),
        h('div', { class: 'flexrow' },
          h('button', {
            class: 'primary',
            onclick: async () => { await api(`/api/quiz/review/${item.id}`, { body: { action: 'accept' } }); viewBank(root); },
          }, '通过'),
          h('button', {
            onclick: async () => { await api(`/api/quiz/review/${item.id}`, { body: { action: 'reject' } }); viewBank(root); },
          }, '退回')))))
      : h('p', { class: 'muted' }, '没有待审题目。'), q.note));

    const pend = await api('/api/quiz/pending');
    root.append(card('待人工判分的作答', pend.items.length
      ? tableOf(['学生', '知识点', '题面', '作答', '规则说明', ''],
        pend.items.map((it) => [
          it.name, it.kp_name, (it.stem || '').slice(0, 24), it.response, it.detail,
          h('div', { class: 'flexrow' },
            h('button', {
              onclick: async () => { await api(`/api/quiz/grade/${it.event_id}`, { body: { is_correct: true } }); viewBank(root); },
            }, '判对'),
            h('button', {
              onclick: async () => { await api(`/api/quiz/grade/${it.event_id}`, { body: { is_correct: false } }); viewBank(root); },
            }, '判错'))]))
      : h('p', { class: 'muted' }, '确定性规则全部判出，没有积压。'), pend.note));

    const sk = await api('/api/skills');
    root.append(card('教学技能包', h('div', {},
      h('p', { class: 'hint' },
        `目录 ${sk.dir}／已装载 ${sk.loaded} 个。新增一个 Markdown 文件即可生效，无需改代码、无需重启。`),
      sk.items.length ? tableOf(['名称', '类型', '追问方式', '优先级', '文件', '指纹'],
        sk.items.map((p) => [p.title, p.kind, p.style || '—', p.priority, p.path, h('span', { class: 'mono' }, p.sha256)]))
        : h('p', { class: 'muted' }, '暂无技能包。'),
      sk.broken.length
        ? h('div', { class: 'notice bad' }, `${sk.broken.length} 个文件未能装载：${sk.broken.map((b) => `${b.path}（${b.error}）`).join('；')}`)
        : null)));
  }

  return { view, viewBank };
})();
