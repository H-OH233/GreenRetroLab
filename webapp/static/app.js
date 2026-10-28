/* ==========================================================================
   GreenRetroLab — 前端交互
   仅做三件事：提交任务 / 轮询进度 / 把结果 JSON 渲染成报告。
   不引入任何前端框架：整站约 20KB，离线可用（与工具本身的定位一致）。
   ========================================================================== */
'use strict';

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));
const num = (v, digits = 2, suffix = '') => (
  v == null || v === '' ? '—' : Number(v).toFixed(digits) + suffix
);

/* Tox21 终点的中文含义（与后端 mol_report.TOX_FULL 保持一致） */
const TOX_NAME = {
  'NR-AR': '雄激素受体·激活', 'NR-AR-LBD': '雄激素受体·配体结合域', 'NR-AhR': '芳烃受体 AhR',
  'NR-Aromatase': '芳香化酶·抑制', 'NR-ER': '雌激素受体·激活', 'NR-ER-LBD': '雌激素受体·配体结合域',
  'NR-PPAR-gamma': 'PPARγ 核受体', 'NR-PPARG': 'PPARγ 核受体',
  'SR-ARE': '抗氧化应激反应', 'SR-ATAD5': 'DNA 损伤应答', 'SR-HSE': '热休克应激反应',
  'SR-MMP': '线粒体膜电位', 'SR-p53': 'p53 肿瘤抑制通路',
};
const levelTag = (lv) => {
  const map = { 0: ['lv0', '无危害分类'], 1: ['lv1', '低危'], 2: ['lv2', '危险'], 3: ['lv3', '高危'] };
  const [cls, text] = map[lv] || ['mute', '未知'];
  return `<span class="tag ${cls}">${text}·${lv}</span>`;
};
const gradeCls = (g) => 'g-' + (g || 'D');

/* ============================================================ 进度与日志 */

let polling = null;

function appendLog(entry) {
  const box = $('#log');
  const line = el('div');
  line.innerHTML = `<span class="t">${esc(entry.t)}</span> <span class="${esc(entry.kind || 'info')}">${esc(entry.text)}</span>`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function setBusy(busy) {
  $('#run').disabled = busy;
  $('#run').textContent = busy ? '评估中…' : '开始评估';
  $('#progress').hidden = !busy;
}

function pollJob(jobId) {
  clearInterval(polling);
  let seen = 0;
  polling = setInterval(async () => {
    let job;
    try {
      job = await (await fetch('/api/job/' + jobId)).json();
    } catch (err) {
      return;                                     // 网络抖动，下一次轮询再试
    }
    (job.log || []).slice(seen).forEach(appendLog);
    seen = (job.log || []).length;
    if (job.state === 'done') {
      clearInterval(polling);
      setBusy(false);
      renderResult(job.result);
      refreshHistory();
      // 把 run_id 写进地址栏：刷新或收藏后仍能回到这份报告（纯本地链接）
      try { history.replaceState(null, '', '/?run=' + job.result.run_id); } catch (e) { /* 忽略 */ }
    } else if (job.state === 'error') {
      clearInterval(polling);
      setBusy(false);
      appendLog({ t: '--:--:--', kind: 'err', text: job.error || '未知错误' });
      $('#report').innerHTML = `<div class="note bad"><b>评估失败。</b> ${esc(job.error || '')}
        <br>可点击左侧「运行环境自检」查看环境问题，或改用 SMILES 直接输入。</div>`;
    }
  }, 700);
}

async function startRun() {
  const raw = $('#input').value.trim();
  if (!raw) { $('#input').focus(); return; }
  const payload = {
    input: raw,
    routes: +$('#opt-routes').value || 3,
    iterations: +$('#opt-iterations').value || 200,
    offline: $('#opt-offline').checked,
    skip_retro: $('#opt-skipretro').checked,
  };
  setBusy(true);
  $('#log').innerHTML = '';
  $('#report').innerHTML = `<div class="note">已提交 ${raw.split('\n').filter((x) => x.trim()).length} 个分子，正在排队…</div>`;
  try {
    const res = await (await fetch('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })).json();
    if (res.error) throw new Error(res.error);
    pollJob(res.job_id);
  } catch (err) {
    setBusy(false);
    appendLog({ t: '--:--:--', kind: 'err', text: String(err.message || err) });
  }
}

/* ============================================================ 渲染：小部件 */

const secHead = (num, title, note = '') => `
  <div class="sec-head">
    <span class="sec-num">§ ${num}</span>
    <h2 class="sec-title">${esc(title)}</h2>
    ${note ? `<span class="sec-note">${note}</span>` : ''}
  </div>`;

const scoreBar = (score, grade) => `
  <span class="score-bar"><i class="${gradeCls(grade)}" style="width:${Math.max(0, Math.min(100, score))}%;
    background:currentColor"></i></span>`;

const scaleFor = (logS) => {
  const span = (Math.max(-6, Math.min(2, logS)) + 6) / 8 * 100;
  const tick = (v) => ((v + 6) / 8 * 100).toFixed(1);
  const label = (v) => `<span style="left:${tick(v)}%">${v}</span>`;
  return `
  <div class="scale">
    <div class="scale-track">
      <div class="tick" style="left:${tick(-4)}%"></div>
      <div class="tick" style="left:${tick(-1)}%"></div>
      <div class="mark" style="left:${span.toFixed(1)}%" title="logS = ${num(logS, 2)}"></div>
    </div>
    <div class="scale-labels">${label(-6)}${label(-4)}${label(-1)}${label(0)}${label(2)}</div>
  </div>`;
};

const miniBar = (p) => {
  const cls = p >= 0.5 ? 'bad' : (p >= 0.3 ? 'warn' : '');
  return `<span class="mini ${cls}"><i style="width:${(p * 100).toFixed(0)}%"></i></span>`;
};

/* ============================================================ 渲染：各章节 */

function renderSample(item) {
  const d = item.descriptors || {};
  const rows = [
    ['输入', `<span class="mono">${esc(item.input)}</span>`],
    ['结构式', '<span class="mid">见左侧结构图（键线式，末端甲基写作 CH₃）</span>'],
    ['分子式', `<span class="num">${esc(d.formula || '—')}</span>`],
    ['分子量', `<span class="num">${num(d.mw, 2)} g·mol⁻¹</span>`],
    ['logP / TPSA', `<span class="num">${num(d.logp, 2)} / ${num(d.tpsa, 1)} Å²</span>`],
    ['氢键给体/受体', `<span class="num">${d.hbd ?? '—'} / ${d.hba ?? '—'}</span>`],
    ['可旋转键 / 环数', `<span class="num">${d.rot_bonds ?? '—'} / ${d.rings ?? '—'}</span>`],
    ['规范 SMILES', `<span class="mono" style="font-size:11.5px">${esc(item.canonical_smiles || '—')}</span>`
      + '<div class="hint" style="margin:2px 0 0">机器可读形式，仅供检索/复制；人读请看上面的结构式与流程图。</div>'],
  ];
  if (item.properties?.resolve_note) rows.push(['名称解析', esc(item.properties.resolve_note)]);
  if (item.properties?.scope?.note) rows.push(['模型适用性', esc(item.properties.scope.note)]);

  // 中文名/别名/商品名/俗名解析结果（离线词库命中时展示，含盐型与剂型提示）
  const info = item.alias_info || {};
  if (info.matched) {
    const en = Array.isArray(info.en) ? (info.en[0] || '') : (info.en || '');
    rows.splice(1, 0, ['名称识别', `<b>${esc(info.zh)}</b>` +
      `<span class="mid">（${esc(en)}${info.cid ? '，PubChem CID ' + esc(info.cid) : ''}）</span>`]);
    rows.splice(2, 0, ['写法判定', `<span class="mid">输入「${esc(info.matched)}」被识别为它的${esc(info.match_type || '名称')}</span>`]);
    (info.notes || []).forEach((note) => rows.push(['', `<span class="mid">${esc(note)}</span>`]));
  }
  return `
  <section class="sec" id="s01">
    ${secHead('01', '样本', esc(item.label))}
    <div class="sample">
      <div class="structure">${item.structure_svg || '<span class="mono" style="color:#8b9199">结构式不可用</span>'}</div>
      <table class="id-table"><tbody>
        ${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join('')}
      </tbody></table>
    </div>
  </section>`;
}

function molThumb(smiles, w = 150, h = 96, alt = '结构式') {
  if (!smiles) return '<span class="muted">—</span>';
  return `<img class="molimg" loading="lazy" alt="${esc(alt)}"
    src="/molimg?smiles=${encodeURIComponent(smiles)}&w=${w}&h=${h}">`;
}

function schemeFigure(route, base, caption) {
  if (!route || !route.scheme) return '';
  return `<figure class="scheme">
    <img loading="lazy" alt="路线 ${esc(route.index)} 结构式合成流程图" src="${base}${esc(route.scheme)}">
    <figcaption>${caption || '正向合成流程图（起始原料 → 目标分子）：分子以结构式表示，'
      + '箭头上为反应模板条件（编号/在 USPTO 中出现次数/置信度），下为建议条件。'
      + '这是模板推理结果，不代表每步反应一定可行，请人工核对化学合理性。'}</figcaption>
  </figure>`;
}

function renderRoute(route, highlight, base) {
  const pl = route.plausibility || null;
  const steps = route.step_detail.map((s) => {
    const ex = s.precedent;
    const precCell = s.precedent_sim != null
      ? `<td class="num ${s.precedent_sim < 0.35 ? 'hot' : ''}">${Number(s.precedent_sim).toFixed(2)}</td>`
      : '<td class="num">—</td>';
    const exRow = ex ? `<tr class="prec-ex"><td></td><td colspan="6" class="mid">
        最近先例（相似度 ${Number(ex.sim).toFixed(2)}${ex.source ? '，来源 ' + esc(ex.source) : ''}）：
        <span class="mono">${esc(String(ex.reactants).slice(0, 120))}</span>
        ${ex.reagents ? '<span class="mono">（' + esc(String(ex.reagents).slice(0, 60)) + '）</span>' : ''}
        → <span class="mono">${esc(String(ex.product).slice(0, 80))}</span></td></tr>` : '';
    return `
    <tr>
      <td class="num">第 ${s.step} 步</td>
      <td class="mono">${esc(s.template ?? '—')}</td>
      <td class="num">${esc(s.occurrence ?? '?')}</td>
      <td class="num">${s.probability != null ? Number(s.probability).toFixed(3) : '—'}</td>
      <td class="num">${num(s.ae, 1, '%')}</td>
      <td class="num ${s.feasibility != null && s.feasibility < 0.5 ? 'hot' : ''}">${s.feasibility != null ? Number(s.feasibility).toFixed(2) : '—'}</td>
      ${precCell}
    </tr>${exRow}`;
  }).join('');
  const mats = route.materials.map((m) => `
    <tr>
      <td>${levelTag(m.level)} ${m.whitelist ? '<span class="tag lv0">常见绿色试剂</span>' : ''}</td>
      <td class="molcell">${molThumb(m.smiles)}</td>
      <td>${esc(m.caption || m.name || '—')}</td>
      <td class="mid">${esc((m.h_codes || []).join(', ') || '—')}</td>
    </tr>`).join('');
  const plNote = pl && pl.reasons?.length ? `
    <div class="note ${pl.verdict === '不推荐' ? 'warn' : ''}">
      <b>可行性复核：${esc(pl.verdict)}</b>（科学分 ${pl.science_score != null ? Number(pl.science_score).toFixed(1) : '—'}
      ${pl.min_sim != null ? ` · 最弱一步先例相似度 ${Number(pl.min_sim).toFixed(2)}` : ''}
      ${pl.min_filter != null ? ` · 内置过滤器最低可行性 ${Number(pl.min_filter).toFixed(2)}` : ''}）
      —— ${esc(pl.reasons.join('；'))}
      <ul class="pl-rules">${(pl.blocked || []).concat(pl.cautions || []).map((h) => `
        <li><b>【${esc(h.zh)}】</b>第 ${esc(h.step ?? '—')} 步 · ${esc(h.level === 'high' ? '强判据' : (h.level === 'medium' ? '中判据' : '提示'))}
          <div class="mid">为什么存疑：${esc(h.why || '')}</div>
          <div class="mid">怎么处理：${esc(h.todo || '')}</div></li>`).join('')}</ul>
    </div>` : '';
  return `
  <article class="route ${highlight ? '' : (route.suspicious ? 'dim' : '')}">
    <header>
      <span class="r-id">路线 ${route.index}</span>
      <span class="r-meta">${route.steps} 步 · 原子经济性 ${num(route.atom_economy, 1, '%')} ·
        模板支持度 ${route.min_occurrence != null ? '≥' + route.min_occurrence + ' 次' : '—'} ·
        危险原料 ${route.hazardous} / 高危 ${route.highly_hazardous}</span>
      <span class="r-flags">
        ${pl ? `<span class="tag ${pl.verdict === '不推荐' ? 'mid' : (pl.verdict === '需确认' ? 'mid' : 'lv0')}">可行性：${esc(pl.verdict)}</span>` : ''}
        ${route.suspicious ? '<span class="tag mid">疑似同系物拆解</span>' : ''}
        ${route.solved ? '' : '<span class="tag mute">原料未全部在库</span>'}
        ${scoreBar(route.score, route.grade)}
        <span class="grade ${gradeCls(route.grade)}">${esc(route.grade)}</span>
        <span class="r-meta"><b>${num(route.score, 1)}</b></span>
      </span>
    </header>
    <div class="body">
      ${schemeFigure(route, base)}
      <table class="data steps-table">
        <thead><tr><th class="num">步骤</th><th>反应模板</th><th class="num">USPTO 出现</th>
          <th class="num">策略置信度</th><th class="num">该步原子经济性</th><th class="num">可行性</th>
          <th class="num">先例相似度</th></tr></thead>
        <tbody>${steps}</tbody>
      </table>
      <table class="data">
        <thead><tr><th>安全性</th><th>结构式</th><th>名称</th><th>GHS 危害代码</th></tr></thead>
        <tbody>${mats}</tbody>
      </table>
      ${plNote}
      ${route.suspicious ? `<div class="note warn">${esc(route.precursor_note || '')}</div>` : ''}
    </div>
  </article>`;
}

function renderRetro(item, base) {
  const retro = item.retro;
  if (!retro) {
    return `<section class="sec">${secHead('04', '逆合成路线')}
      <div class="note">未执行逆合成规划。</div></section>`;
  }
  const st = retro.stats || {};
  const cfg = st.config || {};
  return `
  <section class="sec" id="s04">
    ${secHead('04', '逆合成路线', `USPTO 模板策略 + MCTS · 最多 ${cfg.max_transforms} 步`)}
    <dl class="stats">
      <div><dt>搜索节点</dt><dd>${st.number_of_nodes ?? '—'}</dd></div>
      <div><dt>抽出路线</dt><dd>${st.number_of_routes ?? '—'}</dd></div>
      <div><dt>已打通</dt><dd>${st.number_of_solved_routes ?? '—'}</dd></div>
      <div><dt>搜索用时</dt><dd>${num(st.wall_time, 1)} s</dd></div>
      <div><dt>迭代 / 时限</dt><dd>${cfg.iteration_limit ?? '—'} / ${cfg.time_limit ?? '—'}s</dd></div>
      <div><dt>库存</dt><dd>${esc((cfg.stock || []).join(', ') || '—')}</dd></div>
    </dl>
    ${retro.routes.slice(0, 4).map((r, i) => renderRoute(r, i === 0, base)).join('')}
    ${retro.routes.length > 4 ? `<p class="hint">另有 ${retro.routes.length - 4} 条路线未在此展开（完整数据与全部流程图见下方原始报告与输出目录）。</p>` : ''}
  </section>`;
}

function renderRanking(item, base) {
  const green = item.green;
  const pl = item.plausibility;
  if (!green || !pl) {
    return `<section class="sec">${secHead('03', '路线排序与推荐')}
      <div class="note">无路线可排序。</div></section>`;
  }
  const byRoute = {};
  (pl.routes || []).forEach((x) => { byRoute[x.route] = x; });
  // 排序：先可行性分层（通过 > 需确认 > 不推荐），同层内按科学分（后端已算好的综合排序）
  const rows = green.ranked.map((r, i) => {
    const p = r.plausibility || byRoute[r.index] || {};
    const tag = p.verdict === '不推荐' ? 'mid' : (p.verdict === '需确认' ? 'mid' : 'lv0');
    return `
    <tr class="${r === green.recommended ? 'em' : (r.suspicious ? 'dim' : '')}">
      <td class="num">${i + 1}</td>
      <td class="num">${r.index}</td>
      <td class="num"><b>${p.science_score != null ? num(p.science_score, 1) : '—'}</b></td>
      <td><span class="tag ${tag}">${esc(p.verdict || '—')}</span></td>
      <td class="num ${p.min_sim != null && p.min_sim < 0.35 ? 'hot' : ''}">${p.min_sim != null ? num(p.min_sim, 2) : '—'}</td>
      <td class="num ${p.min_filter != null && p.min_filter < 0.5 ? 'hot' : ''}">${p.min_filter != null ? num(p.min_filter, 2) : '—'}</td>
      <td class="num">${r.steps}</td>
      <td class="num">${r.min_occurrence != null ? '≥' + r.min_occurrence : '—'}</td>
      <td class="num">${num(r.atom_economy, 1, '%')}</td>
      <td>${r.suspicious ? '<span class="tag mid">疑似伪路线</span>' : ''}</td>
    </tr>`;
  }).join('');
  const rec = green.recommended;
  const recPl = (rec && (rec.plausibility || byRoute[rec.index])) || {};
  return `
  <section class="sec" id="s03">
    ${secHead('03', '路线排序与推荐', '科学性 60% + 绿色性 25% + 时代性 15%（绿色性在后台计算，不在页面展示）')}
    <table class="data">
      <thead><tr><th class="num">排名</th><th class="num">路线</th><th class="num">科学分</th><th>可行性</th>
        <th class="num">先例相似度</th><th class="num">过滤器</th><th class="num">步数</th>
        <th class="num">模板支持</th><th class="num">原子经济性</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="note">
      <b>科学分</b>＝ 0.45×先例相似度（真实反应库 kNN）＋ 0.25×内置过滤器可行性 ＋ 0.30×模板先例，
      取「最弱一步」与均值的加权，再扣结构级规则与步数风险惩罚。
      排序规则：<b>先过可行性闸门</b>（不推荐一律不进推荐），同层内按科学分优先，
      绿色性与时代性只作为次级因素 —— 所以「绿色分高但科学上站不住」的路线不会被推荐。
    </div>
    ${rec ? `
      <div class="note ${green.recommended_is_suspicious ? 'warn' : ''}">
        <b>推荐路线：路线 ${rec.index}</b>（科学分 ${recPl.science_score != null ? num(recPl.science_score, 1) : '—'} ·
        可行性 ${esc(recPl.verdict || '—')} · 最弱一步先例相似度
        ${recPl.min_sim != null ? num(recPl.min_sim, 2) : '—'} ·
        ${rec.steps} 步 · 原子经济性 ${num(rec.atom_economy, 1, '%')} · 模板支持度
        ${rec.min_occurrence != null ? '≥' + rec.min_occurrence + ' 次' : '未知'}）
      </div>
      ${schemeFigure(rec, base, `推荐路线 ${esc(rec.index)} 的结构式合成流程图（正向：起始原料 → 目标分子）。`)}` : ''}
    ${pl.blocked_routes?.length ? `
      <div class="note warn"><b>被可行性闸门挡下的路线</b>：路线 ${pl.blocked_routes.join('、')}
        ${pl.green_best_verdict === '不推荐' ? '（其中包含原本绿色评分最高的那条）' : ''}。
        这些路线通常「模板支持」——模板在 USPTO 里出现过——但结构上缺少必需的活化／催化／缩合条件，
        或真实反应库里找不到相近先例（出了模板的适用域）。理由见各路线卡片。</div>` : ''}
    ${green.suspicious_count ? `
      <div class="note warn">另有 ${green.suspicious_count} 条路线被判定为「疑似同系物拆解」：
        单步、单一原料、且该原料并不比目标分子简单，通常是库存过滤器的假阳性，
        已从推荐中剔除（表中灰色显示）。</div>` : ''}
  </section>`;
}

function renderModern(item) {
  const m = item.modern;
  if (!m) return '';
  const scoreRows = (m.scores || []).map((s) => `
    <tr class="${s.dated_steps ? '' : 'dim'}">
      <td class="num">${s.route}</td>
      <td class="num ${s.dated_steps ? 'hot' : ''}">${s.score != null ? num(s.score, 1) : '—'}</td>
      <td class="num">${s.dated_steps ?? 0}</td>
      <td class="mid">${esc((s.reasons || []).join('；'))}</td>
    </tr>`).join('');
  const advice = (m.advice || []).map((a) => `
    <div class="note warn">
      <b>【${esc(a.zh)}】</b><span class="mid">出现于 ${esc(a.occurrence_text)}</span>
      <div class="advice-detail">
        ${(a.reasons || []).length ? `<div>现状问题：${esc(a.reasons.join('；'))}</div>` : ''}
        <div>当代做法：${esc(a.modern)}</div>
        ${a.avoid ? `<div>建议避免：${esc(a.avoid)}</div>` : ''}
        ${a.era ? `<div>时代背景：${esc(a.era)}</div>` : ''}
        ${(a.citations || []).map((c) => `<div class="cite">文献：${esc(c.first_author || '—')}.
          <a href="${esc(c.url || ('https://doi.org/' + c.doi))}" target="_blank" rel="noopener">${esc(c.title)}</a>.
          <i>${esc(c.journal || '')}</i> (${esc(c.year || '')}). doi:${esc(c.doi)}</div>`).join('')}
      </div>
    </div>`).join('');
  const stats = m.citation_stats || {};
  return `
  <section class="sec" id="s04">
    ${secHead('04', '方法学审阅', `过时做法 → 当代替代方案 · 文献库 ${stats.papers || 0} 篇`)}
    <table class="data">
      <thead><tr><th class="num">路线</th><th class="num">方法学新旧</th><th class="num">过时步数</th><th>审阅结论</th></tr></thead>
      <tbody>${scoreRows}</tbody>
    </table>
    ${advice || '<div class="note">未检测到硫酸二甲酯 / NaH-DMF / 计量铬锰氧化 / 叠氮 / 肼 / 三苯基膦 / 计量锡 / 酰氯两步法 / 保护基等常见过时做法。</div>'}
    <div class="note">检测边界：逆合成输出只给试剂、不给反应条件，因此<b>光催化、电化学、连续流、酶催化</b>这类
      “条件级”现代方法通常检测不到，只出现在建议里；本审阅的主要作用是<b>指出过时做法</b>。
      文献由 Crossref REST API 自动检索（真实 DOI，可点开核验）${stats.generated_at ? `，检索于 ${esc(stats.generated_at)}` : ''}。</div>
  </section>`;
}

function renderAdvice(item) {
  if (!item.conclusion?.length) return '';
  return `
  <section class="sec" id="s05">
    ${secHead('05', '综合结论', '仅与合成路线有关')}
    <ol class="advice">${item.conclusion.map((t) => `<li>${esc(t)}</li>`).join('')}</ol>
  </section>`;
}

function renderFigures(result, item, onlyBatch = false) {
  const figs = onlyBatch ? {} : (item.figures || {});
  const base = `/files/${result.run_id}/`;
  const blocks = [];
  if (figs.scheme) {
    blocks.push(`<figure class="fig"><img src="${base}${esc(figs.scheme)}" alt="候选路线流程图版">
      <figcaption>候选合成路线流程图版：每条路线自上而下按正向合成读（起始原料 → 目标分子），
      分子以结构式表示，箭头上下为模板条件与建议条件。</figcaption></figure>`);
  }
  if (!blocks.length) return '';
  return `<section class="sec" id="s06">${secHead('06', '路线流程图版', '由本机渲染（RDKit 结构式）')}
    <div class="figs">${blocks.join('')}</div></section>`;
}

/* ============================================================ 渲染：组装 */

function renderMolecule(result, item) {
  const base = `/files/${result.run_id}/`;
  // 前端只做「逆合成路线」的讨论：溶解度/毒性/绿色评分在后台计算，不在页面上展示
  return [
    renderSample(item),
    renderRetro(item, base),
    renderRanking(item, base),
    renderModern(item),
    renderAdvice(item),
    renderFigures(result, item),
  ].join('');
}

function renderResult(result) {
  const host = $('#report');
  const mols = result.molecules || [];
  if (!mols.length) {
    host.innerHTML = '<div class="note warn">没有返回结果。</div>';
    return;
  }

  const header = `
    <div class="sec">
      <div class="sec-head">
        <span class="sec-num">RUN</span>
        <h2 class="sec-title">${esc(result.created_at)}　${result.batch ? `批量 ${mols.length} 个分子` : '单分子评估'}</h2>
        <span class="sec-note">总耗时 ${num(result.elapsed, 1)} 秒 · 全部计算在本机完成</span>
      </div>
    </div>`;

  if (!result.batch) {
    host.innerHTML = header + renderMolecule(result, mols[0]);
    return;
  }

  const rows = mols.map((m, i) => {
    const pl = m.plausibility || {};
    return `
    <tr>
      <td class="num">${i + 1}</td>
      <td class="molcell">${molThumb(m.canonical_smiles, 132, 84, m.label)}</td>
      <td>${esc(m.label)}</td>
      <td class="num"><b>${pl.recommended_science != null ? num(pl.recommended_science, 1) : '—'}</b></td>
      <td>${pl.recommended_verdict ? `<span class="tag ${pl.recommended_verdict === '不推荐' ? 'mid' : (pl.recommended_verdict === '需确认' ? 'mid' : 'lv0')}">${esc(pl.recommended_verdict)}</span>` : '—'}</td>
      <td class="num">${pl.blocked_routes?.length ? `<span class="num hot">${pl.blocked_routes.length}</span>` : '0'}</td>
      <td class="num">${m.summary.best_steps ?? '—'}</td>
      <td class="num">${m.summary.best_ae != null ? num(m.summary.best_ae, 1, '%') : '—'}</td>
      <td class="num">${m.summary.routes_solved ?? '—'}/${m.summary.routes_total ?? '—'}</td>
    </tr>`;
  }).join('');

  const tabs = mols.map((m, i) =>
    `<button type="button" data-idx="${i}" aria-selected="${i === 0}">${esc(m.label)}</button>`).join('');

  host.innerHTML = header + `
    <section class="sec">
      ${secHead('00', '批量对照', `${mols.length} 个分子 · 逆合成与模型只加载一次`)}
      <table class="data">
        <thead><tr><th class="num">#</th><th>结构式</th><th>分子</th><th class="num">科学分</th>
          <th>可行性</th><th class="num">被挡下</th><th class="num">步数</th>
          <th class="num">原子经济性</th><th class="num">打通/总路线</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>
    <nav class="tabs" id="tabs">${tabs}</nav>
    <div id="mol-body">${renderMolecule(result, mols[0])}</div>`;

  $('#tabs').addEventListener('click', (ev) => {
    const btn = ev.target.closest('button[data-idx]');
    if (!btn) return;
    const idx = +btn.dataset.idx;
    $('#tabs').querySelectorAll('button').forEach((b) => b.setAttribute('aria-selected', String(b === btn)));
    $('#mol-body').innerHTML = renderMolecule(result, mols[idx]);
  });
}

/* ============================================================ 历史与环境 */

async function refreshHistory() {
  try {
    const { history } = await (await fetch('/api/history')).json();
    const box = $('#history');
    if (!history?.length) { box.innerHTML = '<li class="history-empty">尚无记录</li>'; return; }
    box.innerHTML = history.map((h) => {
      const first = h.items[0] || {};
      const extra = h.items.length > 1 ? ` +${h.items.length - 1}` : '';
      return `<li><button type="button" data-run="${esc(h.run_id)}">
        <span class="h-label">${esc(first.label || '')}${extra}</span>
        <span class="h-val">${first.science != null ? Number(first.science).toFixed(1) : '—'}</span>
        <span class="h-grade">${esc(first.verdict || '')}</span>
      </button></li>`;
    }).join('');
    box.querySelectorAll('button[data-run]').forEach((b) => {
      b.addEventListener('click', async () => {
        const res = await (await fetch('/api/result/' + b.dataset.run)).json();
        if (res && res.molecules) renderResult(res);
      });
    });
  } catch (err) { /* 历史失败不影响主流程 */ }
}

async function loadEnv() {
  try {
    const h = await (await fetch('/api/health')).json();
    $('#env-kv').innerHTML = `
      <div><dt>程序目录</dt><dd>${esc(h.root)}</dd></div>
      <div><dt>数据目录</dt><dd>${esc(h.data_dir)}</dd></div>
      <div><dt>molprop</dt><dd class="${h.molprop_found ? 'yes' : 'no'}">
        ${h.molprop_found ? '已就绪' : '未找到（性质预测不可用）'}</dd></div>`;
  } catch (err) {
    $('#env-kv').innerHTML = '<div><dt>状态</dt><dd class="no">无法连接后端</dd></div>';
  }
}

/* ============================================================ 绑定 */

document.addEventListener('DOMContentLoaded', () => {
  $('#run').addEventListener('click', startRun);
  $('#clear-log').addEventListener('click', () => { $('#log').innerHTML = ''; });
  $('#chips').addEventListener('click', (ev) => {
    const chip = ev.target.closest('.chip');
    if (!chip) return;
    const box = $('#input');
    box.value = box.value.trim() ? box.value.trim() + '\n' + chip.dataset.fill : chip.dataset.fill;
    box.focus();
  });
  document.addEventListener('keydown', (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') { ev.preventDefault(); startRun(); }
  });
  $('#doctor').addEventListener('click', async () => {
    const out = $('#doctor-out');
    out.hidden = false;
    out.textContent = '自检中…（会加载模型，约 10~40 秒）';
    try {
      const res = await (await fetch('/api/doctor', { method: 'POST' })).json();
      out.textContent = res.text || '(无输出)';
      loadEnv();
    } catch (err) { out.textContent = '自检请求失败：' + err; }
  });
  loadEnv();
  refreshHistory();

  // 支持 /?run=<run_id> 直接打开某次结果（刷新、收藏、以及自动化截图都用这条路径）
  const wanted = new URLSearchParams(location.search).get('run');
  if (wanted) {
    fetch('/api/result/' + encodeURIComponent(wanted))
      .then((r) => r.json())
      .then((res) => {
        if (!res || !res.molecules) return;
        (res.log || []).forEach(appendLog);           // 还原当时的运行日志
        renderResult(res);
      })
      .catch(() => { /* 找不到就保持空状态 */ });
  }
});
