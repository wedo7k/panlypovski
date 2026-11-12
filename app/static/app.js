// YT Studio — фронтенд (без сборки, чистый JS)
const $ = (s, el = document) => el.querySelector(s);
const view = $('#view');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function api(url, opts = {}) {
  const o = {...opts};
  if (o.body && !(o.body instanceof FormData)) { o.body = JSON.stringify(o.body); o.headers = {'Content-Type': 'application/json'}; }
  const r = await fetch(url, o);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
function toast(msg, err) {
  const t = document.createElement('div'); t.className = 'toast' + (err ? ' err' : ''); t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500);
}
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };
function modal(html) {
  const bg = document.createElement('div'); bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal">${html}</div>`;
  bg.addEventListener('mousedown', e => { if (e.target === bg) bg.remove(); });
  document.body.appendChild(bg); return bg;
}

let SETTINGS = null;
const state = {project: null, tab: 'script', poll: null};
const CH_KEY = 'ytstudio.channel';
const curCh = () => { try { return localStorage.getItem(CH_KEY) || ''; } catch { return ''; } };
const setCh = v => { try { localStorage.setItem(CH_KEY, v); } catch {} };
// чтение .md/.txt файла в строку
const readFile = f => new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = no; r.readAsText(f, 'utf-8'); });

// ------------------------------------------------ роутинг
function go(hash) { location.hash = hash; }
window.addEventListener('hashchange', route);
document.querySelectorAll('.nav').forEach(n => n.onclick = () => go(n.dataset.go));
async function route() {
  clearInterval(state.poll);
  const [page, id, tab] = (location.hash.slice(1) || 'projects').split('/');
  document.querySelectorAll('.nav').forEach(n => n.classList.toggle('on', n.dataset.go === (page === 'p' ? 'projects' : page)));
  SETTINGS = SETTINGS || await api('/api/settings');
  chBar().catch(() => {});
  try {
    if (page === 'p') return await pageProject(id, tab || 'script');
    if (page === 'refs') return await pageRefs(id);
    if (page === 'channels') return await pageChannels();
    if (page === 'settings') return await pageSettings();
    if (page === 'help') return pageHelp();
    if (page === 'mixes') return await pageMixes(id);
    if (page === 'analyses') return await pageAnalyses(id);
    return await pageProjects();
  } catch (e) { view.innerHTML = `<div class="empty">Ошибка: ${esc(e.message)}</div>`; }
}

// ------------------------------------------------ верхняя панель: текущий канал + его папка
async function chBar() {
  const chs = await api('/api/channels');
  let ch = curCh(); if (ch && !chs.some(c => c.id === ch)) { ch = ''; setCh(''); }
  const c = chs.find(x => x.id === ch);
  const bar = $('#chbar');
  bar.innerHTML = `<div class="chbar">
    <div class="chmenu"><button class="chbtn" id="chopen">📺 Каналы: <b>${c ? esc(c.name) : 'все'}</b> ▾</button>
      <div class="chdrop" hidden>
        <div class="chi ${!ch ? 'on' : ''}" data-v=""><b>Все каналы</b></div>
        ${chs.map(x => `<div class="chi ${x.id === ch ? 'on' : ''}" data-v="${x.id}"><b>📺 ${esc(x.name)}</b>
          <span class="sub">${x.output_dir ? '📁 ' + esc(x.output_dir) : '⚠ папка не указана'}</span></div>`).join('')}
        <div class="chi add" data-add="1">＋ Новый канал</div>
      </div></div>
    ${c ? `<button class="chdir ${c.output_dir ? '' : 'none'}" id="chdir" title="Сюда сохраняются готовые ролики канала">📁 ${esc(c.output_dir || 'укажи папку для сохранения')}</button>` : ''}
  </div>`;
  const drop = $('.chdrop', bar);
  $('#chopen', bar).onclick = e => { e.stopPropagation(); drop.hidden = !drop.hidden; };
  document.addEventListener('click', () => { drop.hidden = true; }, {once: true});
  bar.querySelectorAll('[data-v]').forEach(b => b.onclick = () => {
    setCh(b.dataset.v);
    const nc = chs.find(x => x.id === b.dataset.v);
    if (nc && !nc.output_dir) askDir(nc); else route();
  });
  $('[data-add]', bar).onclick = () => askDir(null);
  if (c) $('#chdir', bar).onclick = () => askDir(c);
}

function askDir(c) {
  const m = modal(`<h2>${c ? esc(c.name) : 'Новый канал'}</h2>
    ${c ? '' : '<label class="f">Название канала<input type="text" id="cn" placeholder="Например: История"></label>'}
    <label class="f" style="margin-top:10px">Папка, куда сохранять всё по этому каналу<input type="text" id="cd" value="${esc(c?.output_dir || '')}" placeholder="Например: D:\\YouTube\\История"></label>
    <p class="sub" style="font-size:12px">Готовые ролики (и файл с названием/описанием/тегами) будут копироваться сюда. Папка создастся сама.</p>
    <div class="row" style="margin-top:14px"><div class="spacer"></div><button class="btn ghost" id="sk">Позже</button><button class="btn p" id="ok">Сохранить</button></div>`);
  $('#sk', m).onclick = () => { m.remove(); route(); };
  $('#ok', m).onclick = guard(async () => {
    const body = {output_dir: $('#cd', m).value.trim()};
    if (c) body.id = c.id; else { body.name = $('#cn', m).value.trim(); if (!body.name) return toast('Введи название', true); }
    const r = await api('/api/channels', {method: 'POST', body});
    setCh(r.id); m.remove(); toast('Сохранено'); route();
  });
}

// ------------------------------------------------ проекты
async function pageProjects() {
  const chs = await api('/api/channels');
  let ch = curCh(); if (ch && !chs.some(c => c.id === ch)) { ch = ''; setCh(''); }
  const [list, refs] = await Promise.all([api('/api/projects' + (ch ? '?channel=' + ch : '')), api('/api/refs')]);
  const chObj = chs.find(c => c.id === ch);
  view.innerHTML = `
  <div class="hero">
    <div><h1>${chObj ? esc(chObj.name) : 'Все проекты'}</h1><p class="sub">Сценарий → озвучка → медиа → музыка → монтаж. Одна кнопка — готовый ролик.</p></div>
    <div class="spacer"></div>
    <label class="btn big ghost">📄 Из файла задачи<input type="file" id="taskf" accept=".md,.txt,.markdown" hidden></label>
    <button class="btn big" id="new">＋ Новый проект</button>
  </div>
  <div class="grid g3">${list.map(p => `
    <div class="card click" data-id="${p.id}">
      <div class="row"><b style="font-size:15px">${esc(p.name)}</b><div class="spacer"></div><span class="pill">${p.format}</span></div>
      <p class="sub" style="margin:8px 0 0">Сцен: ${p.scenes} · ${esc(p.updated || '')}</p>
      <div style="margin-top:10px">${p.output ? '<span class="pill ok">✓ ролик собран</span>' : '<span class="pill gray">черновик</span>'}</div>
    </div>`).join('') || '<div class="card empty" style="grid-column:1/-1">Пока нет проектов. Нажми «Новый проект».</div>'}
  </div>`;
  view.querySelectorAll('[data-id]').forEach(c => c.onclick = () => go('p/' + c.dataset.id));
  $('#taskf').onchange = guard(async e => {
    const text = await readFile(e.target.files[0]);
    const p = await api('/api/projects/from_task', {method: 'POST', body: {text, channel_id: ch || null, run: true}});
    toast('Задача принята — собираю ролик'); go('p/' + p.id);
  });
  $('#new').onclick = () => {
    const m = modal(`<h2>Новый проект</h2>
      <div class="grid g2">
        <label class="f">Название<input type="text" id="n" placeholder="Например: История Nike"></label>
        <label class="f">Формат<select id="fmt"><option value="16:9">16:9 — обычный ролик</option><option value="9:16">9:16 — Shorts</option></select></label>
        <label class="f">Канал<select id="chs"><option value="">— без канала —</option>${chs.map(c => `<option value="${c.id}" ${c.id === ch ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>
        <label class="f">Референс (стиль монтажа)<select id="ref"><option value="">— стиль канала / стандартный —</option>
          ${refs.filter(r => r.status === 'ready').map(r => `<option value="${r.id}">${esc(r.name)} (${r.analysis?.format})</option>`).join('')}</select></label>
      </div>
      <h2 style="margin-top:16px">Озвучка</h2><div id="vbox"></div>
      <label class="f" style="margin-top:12px">Описание — что хочешь получить<textarea id="brief" rows="4" placeholder="Например: ролик про братьев Франца Иосифа в стиле референса, спокойный голос, архивные фото + ИИ-картины, 12 минут."></textarea></label>
      <label class="dropf" id="dz"><input type="file" id="sf" accept=".md,.txt,.markdown,.docx" hidden>
        <span id="dzt">📎 Вставить файл со сценарием (.md / .txt) — нажми или перетащи сюда</span></label>
      <div class="row" style="margin-top:16px"><div class="spacer"></div><button class="btn p" id="ok">Создать</button></div>`);
    let scriptText = '';
    const takeFile = async f => { if (!f) return; scriptText = await readFile(f); $('#dzt', m).textContent = `✓ ${f.name} — ${scriptText.length} символов (нажми, чтобы заменить)`; $('#dz', m).classList.add('on'); };
    $('#sf', m).onchange = e => takeFile(e.target.files[0]);
    $('#dz', m).ondragover = e => { e.preventDefault(); $('#dz', m).classList.add('over'); };
    $('#dz', m).ondragleave = () => $('#dz', m).classList.remove('over');
    $('#dz', m).ondrop = e => { e.preventDefault(); $('#dz', m).classList.remove('over'); takeFile(e.dataTransfer.files[0]); };
    // голос по умолчанию — из референса (или референса канала); выбранный вручную не трогаем
    let voiceTouched = false;
    const drawVoice = () => {
      if (voiceTouched) return;
      const rid = $('#ref', m).value || chs.find(c => c.id === $('#chs', m).value)?.reference_id;
      $('#vbox', m).innerHTML = voiceForm({...SETTINGS.default_style, ...(refs.find(r => r.id === rid)?.style || {})});
      wireVoice($('#vbox', m));
    };
    $('#vbox', m).addEventListener('change', () => { voiceTouched = true; });
    drawVoice();
    $('#chs', m).onchange = drawVoice;
    $('#ref', m).onchange = e => { const r = refs.find(x => x.id === e.target.value); if (r) $('#fmt', m).value = r.analysis.format; drawVoice(); };
    $('#ok', m).onclick = guard(async () => {
      const p = await api('/api/projects', {method: 'POST', body: {name: $('#n', m).value, format: $('#fmt', m).value, reference_id: $('#ref', m).value || null, channel_id: $('#chs', m).value || null, brief: $('#brief', m).value, script: scriptText, voice: readStyle($('#vbox', m))}});
      m.remove(); go('p/' + p.id);
    });
  };
}

// ------------------------------------------------ проект
const TABS = [['script', '📝 Сценарий'], ['editor', '✂️ Редактор'], ['timeline', '🎞 Таймлайн'], ['style', '🎨 Стиль'], ['music', '🎵 Музыка'], ['meta', '📺 YouTube'], ['result', '🎬 Результат']];

async function pageProject(id, tab) {
  const p = state.project = await api('/api/projects/' + id);
  state.tab = tab;
  view.innerHTML = `
    <div class="row" style="margin-bottom:6px">
      <a href="#projects" style="color:var(--muted);text-decoration:none">← Проекты</a>
    </div>
    <div class="row" style="margin-bottom:16px">
      <h1 contenteditable="true" id="pname" spellcheck="false">${esc(p.name)}</h1>
      <span class="pill">${p.style.format}</span>
      <div class="spacer"></div>
      <button class="btn danger s" id="del">Удалить</button>
      <button class="btn p big" id="runall">⚡ Сделать ролик</button>
    </div>
    <div class="card" id="jobcard" style="margin-bottom:18px;display:none">
      <div class="row"><b id="jobstep"></b><div class="spacer"></div><span id="jobpct" class="pill"></span><button class="btn danger s" id="jobstop" style="margin-left:8px">⏹ Остановить</button></div>
      <div class="progress" style="margin-top:10px"><div id="jobbar" style="width:0"></div></div>
      <div class="stages" id="jobstages"></div>
      <details style="margin-top:8px"><summary class="muted">Подробный лог</summary><div class="log" id="joblog"></div></details>
    </div>
    <div class="tabs">${TABS.map(([k, n]) => `<div class="tab ${k === tab ? 'on' : ''}" data-t="${k}">${n}</div>`).join('')}</div>
    <div id="tab"></div>`;
  view.querySelectorAll('.tab').forEach(t => t.onclick = () => go(`p/${id}/${t.dataset.t}`));
  $('#pname').onblur = guard(async e => { await save({name: e.target.textContent.trim()}); });
  $('#del').onclick = guard(async () => { if (confirm('Удалить проект целиком?')) { await api('/api/projects/' + id, {method: 'DELETE'}); go('projects'); } });
  $('#runall').onclick = () => runSteps(['parse', 'voice', 'media', 'music', 'render']);
  $('#jobstop').onclick = guard(async () => { renderJob(await api(`/api/projects/${id}/stop`, {method: 'POST'})); toast('Останавливаю…'); });
  renderJob(p.job);
  if (p.job.running) pollJob();
  if (tab !== 'timeline') tlStop();
  refreshTab();
}

function refreshTab() {
  ({script: tabScript, editor: tabEditor, timeline: tabTimeline, style: tabStyle, music: tabMusic, meta: tabMeta, result: tabResult})[state.tab]();
}

async function save(patch) {
  state.project = await api('/api/projects/' + state.project.id, {method: 'PATCH', body: patch});
  return state.project;
}
const runSteps = guard(async steps => {
  await api(`/api/projects/${state.project.id}/run`, {method: 'POST', body: {steps}});
  pollJob();
});
function renderJob(j) {
  const card = $('#jobcard'); if (!card) return;
  if (!j.running && !j.log.length) { card.style.display = 'none'; return; }
  card.style.display = '';
  const names = {parse: 'Разбор сценария', media: 'Подбор медиа', voice: 'Озвучка', music: 'Музыка', render: 'Монтаж', 'готово': 'Готово'};
  $('#jobstep').textContent = j.error === 'Остановлено' ? '⏹ Остановлено' : j.error ? 'Ошибка' : (j.running ? '⏳ ' : '✅ ') + (names[j.step] || j.step);
  $('#jobpct').textContent = j.running ? j.progress + '%' : (j.error ? 'стоп' : 'готово');
  $('#jobpct').className = 'pill ' + (j.error ? 'err' : j.running ? '' : 'ok');
  $('#jobbar').style.width = (j.running ? j.progress : 100) + '%';
  const log = $('#joblog'); log.textContent = j.log.join('\n'); log.scrollTop = log.scrollHeight;
  $('#runall').disabled = j.running;
  $('#jobstop').style.display = j.running ? '' : 'none';
  const ic = {wait: '○', run: '⏳', done: '✅', err: '❌', stop: '⏹'};
  $('#jobstages').innerHTML = (j.stages || []).map(s => `<div class="stage ${s.status}"><span class="si">${ic[s.status] || '○'}</span><b>${esc(s.name)}</b>
    <span class="sd">${esc(s.status === 'run' ? s.detail + (j.progress ? ` · ${j.progress}%` : '') : s.status === 'done' ? s.detail : s.status === 'wait' ? 'ждёт' : s.detail)}</span></div>`).join('');
}
function pollJob() {
  clearInterval(state.poll);
  const pid = state.project.id;
  state.poll = setInterval(async () => {
    const j = await api(`/api/projects/${pid}/job`);
    renderJob(j);
    if (!j.running) {
      clearInterval(state.poll);
      if (!j.error) toast('Готово!');
      const tab = state.tab; await pageProject(pid, tab === 'script' ? (j.step === 'готово' && j.log.some(l => l.includes('Ролик')) ? 'result' : 'editor') : tab);
    }
  }, 1200);
}

// --- сценарий
async function tabScript() {
  const p = state.project;
  const refs = (await api('/api/refs')).filter(r => r.status === 'ready');
  $('#tab').innerHTML = `
  <div class="grid g2" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr)">
    <div class="card">
      <div class="row"><h2 style="margin:0">Сценарий</h2><div class="spacer"></div>
        <label class="btn s">📄 Загрузить .md / .txt<input type="file" id="mdf" accept=".md,.txt,.markdown" hidden></label></div>
      <p class="sub" style="margin:6px 0 12px">Вставь сценарий <b>в любом виде</b> — хоть сплошным текстом, с ремарками и пожеланиями. ИИ сам разберёт его на сцены, подберёт запросы для видео, музыку и данные для YouTube.</p>
      <textarea class="script" id="sc" placeholder="Просто вставь сюда текст ролика…">${esc(p.script)}</textarea>
      <div class="grid g2" style="margin-top:12px">
        <label class="f">Референс (стиль монтажа)<select id="ref"><option value="">— как у канала / стандартный —</option>
          ${refs.map(r => `<option value="${r.id}" ${r.id === p.reference_id ? 'selected' : ''}>${esc(r.name)} · ${r.analysis?.format}</option>`).join('')}</select></label>
        <label class="f">Формат<select id="fmt"><option value="16:9" ${p.style.format === '16:9' ? 'selected' : ''}>16:9 — обычный</option><option value="9:16" ${p.style.format === '9:16' ? 'selected' : ''}>9:16 — Shorts</option></select></label>
        <label class="f" style="grid-column:1/-1">Какая музыка нужна (своими словами, можно пусто)<input type="text" id="mus" value="${esc(p.music_wish || '')}" placeholder="например: тревожная, медленная, как в документалках про войну"></label>
      </div>
      ${p.claude_status === 'waiting' ? `<div class="card" style="margin-top:14px;background:var(--blue-l);border-color:#BFD3FF">
        <b>⏳ Ждёт разбора в чате Claude</b>
        <p class="sub" style="margin:6px 0 0">Claude в чате подхватит задачу сам (когда чат открыт и следит за ящиком). Как только ответит — сцены появятся здесь${p.auto_run ? ' и ролик начнёт собираться сам' : ''}.</p></div>` : ''}
      <div class="row" style="margin-top:14px">
        <div class="spacer"></div>
        <button class="btn" id="parsego" title="Разбор через API-ключ Google/Claude из настроек">⚡ Авто (API)</button>
        <button class="btn p big" id="toclaude">💬 Разобрать в чате Claude</button>
      </div>
    </div>
    <div class="card scenecard">
      <h2>Как ИИ понял сценарий: ${p.scenes.length} сцен</h2>
      ${p.music_prompt ? `<p class="sub" style="margin:0 0 10px">🎵 ${esc(p.music_prompt)}</p>` : ''}
      <div class="scenebox">${p.scenes.map((s, i) => `<div style="padding:8px 0;border-bottom:1px solid var(--line)">
        <b>${i + 1}.</b> ${s.title ? `<span class="pill">${esc(s.title)}</span> ` : ''}${esc(s.text)}
        <div style="font-size:12px;color:var(--muted);margin-top:3px">🔎 ${esc(s.queries.join('; ') || 'авто')} · кадров: ${s.shots.length}</div></div>`).join('') || '<p class="sub">Пока пусто — нажми «Сделать ролик» или «Только разобрать».</p>'}</div>
    </div>
  </div>`;
  $('#mdf').onchange = guard(async e => { $('#sc').value = await readFile(e.target.files[0]); toast('Файл загружен'); });
  const store_ = async () => {
    const body = {script: $('#sc').value, music_wish: $('#mus').value, style: {format: $('#fmt').value}};
    if ($('#ref').value && $('#ref').value !== p.reference_id) body.reference_id = $('#ref').value;
    await save(body);
    if (body.reference_id) await save({style: {format: $('#fmt').value}});  // формат, выбранный руками, главнее
  };
  $('#parsego').onclick = guard(async () => { await store_(); await runSteps(['parse', 'voice', 'media', 'music', 'render']); });
  $('#toclaude').onclick = guard(async () => {
    await store_();
    state.project = await api(`/api/projects/${p.id}/to_claude`, {method: 'POST', body: {auto_run: true}});
    toast('Отправлено! Claude подхватит задачу сам (чат Claude должен быть открыт в папке софта)'); tabScript();
  });
  if (p.claude_status === 'waiting') {
    clearInterval(state.poll);
    state.poll = setInterval(async () => {
      const np = await api('/api/projects/' + p.id);
      if (np.claude_status !== 'waiting') { clearInterval(state.poll); toast('Claude разобрал сценарий ✅'); pageProject(p.id, np.job.running ? 'script' : 'editor'); if (np.job.running) pollJob(); }
    }, 2500);
  }
}

// --- редактор
function tabEditor() {
  const p = state.project;
  if (!p.scenes.length) { $('#tab').innerHTML = '<div class="card empty">Сначала вставь сценарий во вкладке «Сценарий».</div>'; return; }
  const vert = p.style.format === '9:16';
  const total = p.scenes.reduce((a, s) => a + s.shots.filter(x => !x.disabled).reduce((b, x) => b + (+x.dur || 0), 0), 0);
  $('#tab').innerHTML = `
    <div class="card" style="margin-bottom:14px">
      <div class="row">
        <div><b>Монтажный стол</b><div class="sub" style="margin:2px 0 0">Кликни на кадр — превью. Меняй длительность и «начало» (обрезка), заменяй, выключай, двигай.</div></div>
        <div class="spacer"></div>
        <span class="pill">≈ ${total.toFixed(1)} сек</span>
        <button class="btn" id="bal" title="Пересчитать длительности кадров под озвучку">⚖️ Выровнять</button>
        <button class="btn" id="fetch">🔎 Добрать медиа</button>
        <button class="btn" id="voice">🎙 Озвучка</button>
        <button class="btn p" id="render">🎬 Пересобрать ролик</button>
      </div>
    </div>
    <div class="${vert ? 'vert' : ''}" id="scenes">${p.scenes.map((sc, i) => sceneHTML(sc, i)).join('')}</div>`;
  $('#bal').onclick = guard(async () => { state.project = await api(`/api/projects/${p.id}/rebalance`, {method: 'POST'}); tabEditor(); });
  $('#fetch').onclick = () => runSteps(['media']);
  $('#voice').onclick = () => runSteps(['voice']);
  $('#render').onclick = () => runSteps(['render']);
  bindEditor();
}
function sceneHTML(sc, i) {
  const pid = state.project.id;
  const sdur = sc.shots.filter(x => !x.disabled).reduce((b, x) => b + (+x.dur || 0), 0);
  return `<div class="scene" data-sc="${sc.id}">
    <div class="scene-h">
      <div class="scene-n">${i + 1}</div>
      <div style="flex:1;display:grid;gap:8px">
        <div class="row">
          <input type="text" data-f="title" value="${esc(sc.title)}" placeholder="Плашка (заголовок сцены)" style="max-width:340px">
          <input type="text" data-f="queries" value="${esc(sc.queries.join('; '))}" placeholder="запросы для стоков; через ;" style="max-width:340px">
          <div class="spacer"></div>
          <span class="pill gray">${sc.voice_dur ? '🎙 ' + sc.voice_dur + 'с · ' : ''}🎞 ${sdur.toFixed(1)}с</span>
          ${sc.voice ? `<button class="btn s" data-a="play" data-src="/api/projects/${pid}/file/${sc.voice}">▶ голос</button>` : ''}
          <button class="btn s" data-a="up">↑</button><button class="btn s" data-a="down">↓</button>
          <button class="btn s danger" data-a="delscene">✕</button>
        </div>
        <textarea data-f="text" style="min-height:52px">${esc(sc.text)}</textarea>
      </div>
    </div>
    <div class="shots">
      ${sc.shots.map((s, k) => `
        <div class="shot ${s.disabled ? 'off' : ''}" data-shot="${s.id}">
          <div class="th" data-a="preview" style="background-image:url('${s.media.thumb ? `/api/projects/${pid}/file/${s.media.thumb}` : ''}')">
            <span class="badge">${s.media.type === 'video' ? '🎥' : '🖼'} ${esc(s.media.source)}</span></div>
          <div class="b">
            <div class="row" style="gap:6px;flex-wrap:nowrap">
              <label class="f" style="flex:1">сек<input type="number" step="0.1" min="0.3" data-s="dur" value="${s.dur}"></label>
              ${s.media.type === 'video' ? `<label class="f" style="flex:1">старт<input type="number" step="0.1" min="0" data-s="trim_in" value="${s.trim_in || 0}"></label>` : ''}
            </div>
            <label class="chk" style="font-size:12px"><input type="checkbox" data-s="locked" ${s.locked ? 'checked' : ''}> фикс. длина</label>
            <div class="tools">
              <button class="btn s" data-a="left">◀</button><button class="btn s" data-a="right">▶</button>
              <button class="btn s" data-a="replace">⇄</button>
              <button class="btn s" data-a="toggle">${s.disabled ? 'вкл' : 'выкл'}</button>
              <button class="btn s danger" data-a="delshot">✕</button>
            </div>
          </div>
        </div>`).join('')}
      <div class="addshot">
        <button class="btn s" data-a="search">🔎 Найти</button>
        <label class="btn s">⬆ Файл<input type="file" data-a="upload" accept="video/*,image/*" hidden></label>
        <button class="btn s" data-a="gen">✨ Сгенерить</button>
      </div>
    </div>
  </div>`;
}
function bindEditor() {
  const p = state.project;
  const findSc = el => p.scenes.find(s => s.id === el.closest('[data-sc]').dataset.sc);
  const findShot = (sc, el) => sc.shots.find(s => s.id === el.closest('[data-shot]').dataset.shot);
  let timer;
  const persist = (rerender) => { clearTimeout(timer); timer = setTimeout(guard(async () => { await save({scenes: p.scenes}); if (rerender) tabEditor(); }), rerender ? 0 : 500); };
  const root = $('#scenes');
  root.addEventListener('input', e => {
    const sc = findSc(e.target);
    if (e.target.dataset.f) {
      const f = e.target.dataset.f;
      sc[f] = f === 'queries' ? e.target.value.split(';').map(x => x.trim()).filter(Boolean) : e.target.value;
      persist();
    } else if (e.target.dataset.s) {
      const s = findShot(sc, e.target), f = e.target.dataset.s;
      s[f] = e.target.type === 'checkbox' ? e.target.checked : parseFloat(e.target.value) || 0;
      if (f === 'dur') s.locked = true;
      persist(f === 'locked');
    }
  });
  root.addEventListener('change', e => {
    if (e.target.dataset.a === 'upload' && e.target.files[0]) guard(async () => {
      const fd = new FormData(); fd.append('file', e.target.files[0]);
      toast('Загружаю…');
      state.project = await api(`/api/projects/${p.id}/scenes/${findSc(e.target).id}/upload`, {method: 'POST', body: fd});
      refreshTab();
    })();
  });
  root.addEventListener('click', e => {
    const a = e.target.closest('[data-a]')?.dataset.a; if (!a || a === 'upload') return;
    const sc = findSc(e.target), i = p.scenes.indexOf(sc);
    const shotEl = e.target.closest('[data-shot]'); const s = shotEl && findShot(sc, e.target);
    const k = s ? sc.shots.indexOf(s) : -1;
    const swap = (arr, x, y) => { if (y < 0 || y >= arr.length) return; [arr[x], arr[y]] = [arr[y], arr[x]]; persist(true); };
    ({
      play: () => toggleVoice(e.target.closest('[data-a]'), e.target.closest('[data-a]').dataset.src),
      up: () => swap(p.scenes, i, i - 1), down: () => swap(p.scenes, i, i + 1),
      delscene: () => { if (confirm('Удалить сцену?')) { p.scenes.splice(i, 1); persist(true); } },
      left: () => swap(sc.shots, k, k - 1), right: () => swap(sc.shots, k, k + 1),
      toggle: () => { s.disabled = !s.disabled; persist(true); },
      delshot: () => { sc.shots.splice(k, 1); persist(true); },
      preview: () => previewShot(s),
      replace: () => searchModal(sc, s),
      search: () => searchModal(sc, null),
      gen: () => genModal(sc),
    })[a]?.();
  });
}
function previewShot(s) {
  const src = `/api/projects/${state.project.id}/file/${s.media.path}`;
  modal(`<div class="row"><h2 style="margin:0">Превью кадра</h2><div class="spacer"></div>${s.media.credit ? `<a href="${esc(s.media.credit)}" target="_blank" class="pill">источник ↗</a>` : ''}</div>
    <div style="margin-top:12px">${s.media.type === 'video'
      ? `<video src="${src}#t=${s.trim_in || 0},${(s.trim_in || 0) + (+s.dur)}" controls autoplay></video><p class="sub">Длина исходника: ${(+s.media.src_dur || 0).toFixed(1)} сек. Используется отрезок ${s.trim_in || 0} → ${((s.trim_in || 0) + +s.dur).toFixed(1)} сек.</p>`
      : `<img src="${src}" style="width:100%;border-radius:12px">`}</div>`);
}
function searchModal(sc, replace) {
  const fmt = state.project.style.format;
  const m = modal(`<h2>${replace ? 'Заменить кадр' : 'Добавить кадр'}</h2>
    <div class="row">
      <input type="text" id="q" value="${esc(sc.queries[0] || '')}" placeholder="запрос на английском" style="flex:1;min-width:240px">
      <select id="kind" style="width:auto"><option value="video">видео</option><option value="image">фото</option></select>
      <select id="src" style="width:auto"><option value="">все источники</option><option>pexels</option><option>pixabay</option><option value="wikimedia">Commons (Wikimedia)</option></select>
      <button class="btn p" id="go">Искать</button>
    </div>
    <div id="err" class="sub" style="margin:8px 0 0"></div>
    <div class="results" id="res"></div>`);
  const doSearch = guard(async () => {
    $('#res', m).innerHTML = '<div class="sub">Ищу…</div>';
    const r = await api(`/api/search?q=${encodeURIComponent($('#q', m).value)}&kind=${$('#kind', m).value}&fmt=${fmt}&source=${$('#src', m).value}`);
    $('#err', m).textContent = r.errors.join(' · ');
    $('#res', m).innerHTML = r.results.map((x, i) => `<div class="res" data-i="${i}"><img src="${esc(x.thumb || x.url)}" loading="lazy"><span>${x.type === 'video' ? '🎥 ' + (x.dur || '') + 'с' : '🖼'} ${x.source}</span></div>`).join('') || '<div class="sub">Ничего. Проверь ключи в настройках или смени запрос.</div>';
    $('#res', m).querySelectorAll('.res').forEach(el => el.onclick = guard(async () => {
      el.style.opacity = .4; toast('Скачиваю…');
      state.project = await api(`/api/projects/${state.project.id}/scenes/${sc.id}/add`, {method: 'POST', body: {...r.results[el.dataset.i], replace: replace?.id}});
      m.remove(); refreshTab();
    }));
  });
  $('#go', m).onclick = doSearch;
  $('#q', m).onkeydown = e => e.key === 'Enter' && doSearch();
  if ($('#q', m).value) doSearch();
}
function genModal(sc) {
  const m = modal(`<h2>Сгенерировать картинку (Google / Nano Banana)</h2>
    <textarea id="pr" placeholder="Промпт на английском">${esc(sc.ai_prompt || sc.text)}</textarea>
    <div class="row" style="margin-top:12px"><span class="sub" style="margin:0">Нужен Google API ключ в настройках</span><div class="spacer"></div><button class="btn p" id="go">Сгенерировать</button></div>`);
  $('#go', m).onclick = guard(async () => {
    $('#go', m).disabled = true; $('#go', m).textContent = 'Генерирую…';
    try { state.project = await api(`/api/projects/${state.project.id}/scenes/${sc.id}/generate`, {method: 'POST', body: {prompt: $('#pr', m).value}}); m.remove(); refreshTab(); }
    finally { $('#go', m).disabled = false; $('#go', m).textContent = 'Сгенерировать'; }
  });
}

// --- стиль
const TRANSITIONS = ['fade', 'dissolve', 'fadeblack', 'fadewhite', 'slideleft', 'slideright', 'slideup', 'wipeleft', 'wiperight', 'smoothleft', 'smoothright', 'circleopen', 'circleclose', 'radial', 'pixelize', 'zoomin', 'hblur', 'distance'];
const opt = (arr, v) => arr.map(x => { const [val, lab] = Array.isArray(x) ? x : [x, x]; return `<option value="${val}" ${val == v ? 'selected' : ''}>${lab}</option>`; }).join('');
function styleForm(st) {
  return `
  <div class="grid g4">
    <label class="f">Формат<select data-k="format">${opt([['16:9', '16:9 обычный'], ['9:16', '9:16 Shorts']], st.format)}</select></label>
    <label class="f">Доля кусков YouTube<input type="number" step="0.05" min="0" max="1" data-k="yt_share" value="${st.yt_share ?? 0.3}"></label>
    <label class="f">Длина куска YouTube, с<input type="number" step="0.5" min="2" max="8" data-k="yt_len" value="${st.yt_len ?? 4}"></label>
    <label class="f" style="grid-column:1/-1">Стиль ИИ-картинок (пусто — из паспорта или нейтральное фото)<input data-k="ai_style" value="${esc(st.ai_style || '')}" placeholder="например: modern documentary photo, natural light, no text"></label>
    <label class="f">ИИ-кадры<select data-k="ai_kind">${opt([['image', 'картинки'], ['video', 'видео G-Labs (Omni Flash)']], st.ai_kind || 'image')}</select></label>
    <label class="f">Средняя длина кадра, сек<input type="number" step="0.1" data-k="shot_len" value="${st.shot_len}"></label>
    <label class="f">Что искать<select data-k="media_pref">${opt([['video', 'сначала видео'], ['image', 'сначала фото']], st.media_pref)}</select></label>
    <label class="f">FPS<select data-k="fps">${opt([24, 25, 30, 60], st.fps)}</select></label>
  </div>
  <h2 style="margin-top:20px">Субтитры и плашки</h2>
  <div class="grid g4">
    <label class="chk"><input type="checkbox" data-k="subtitles" ${st.subtitles ? 'checked' : ''}> Субтитры</label>
    <label class="f">Слов на экране<input type="number" min="1" max="12" data-k="sub_words" value="${st.sub_words}"></label>
    <label class="f">Позиция<select data-k="sub_position">${opt([['bottom', 'снизу'], ['center', 'по центру']], st.sub_position)}</select></label>
    <label class="f">Размер шрифта<input type="number" data-k="sub_size" value="${st.sub_size}"></label>
    <label class="f">Цвет субтитров<input type="color" data-k="sub_color" value="${st.sub_color}"></label>
    <label class="chk"><input type="checkbox" data-k="plates" ${st.plates ? 'checked' : ''}> Плашки-заголовки</label>
    <label class="f">Цвет плашек<input type="color" data-k="plate_color" value="${esc(st.plate_color)}" oninput="this.nextElementSibling.value='1'"><input type="hidden" data-k="plate_color_own" value="${st.plate_color_own ? '1' : ''}"></label>
  </div>
  <h2 style="margin-top:20px">Монтаж</h2>
  <div class="grid g4">
    <label class="chk"><input type="checkbox" data-k="transitions" ${st.transitions ? 'checked' : ''}> Переходы между сценами</label>
    <label class="f">Переход<select data-k="transition">${opt(TRANSITIONS, st.transition)}</select></label>
    <label class="f">Длина перехода, сек<input type="number" step="0.05" data-k="transition_dur" value="${st.transition_dur}"></label>
    <label class="f">Фото карточкой<select data-k="photo_card">${opt([['auto', 'если фото не по формату'], ['mix', `вперемешку, как в референсе${st.photo_card_share != null ? ' (' + Math.round(st.photo_card_share * 100) + '%)' : ''}`], ['all', 'все фото'], ['off', 'нет, на весь кадр']], st.photo_card || 'auto')}</select></label>
    <label class="chk"><input type="checkbox" data-k="zoom" ${st.zoom ? 'checked' : ''}> Зум (Ken Burns) на фото</label>
    <label class="chk"><input type="checkbox" data-k="sfx" ${st.sfx ? 'checked' : ''}> Звуки-свуши на смене сцен</label>
    <label class="f">Громкость SFX<input type="number" step="0.05" data-k="sfx_volume" value="${st.sfx_volume}"></label>
    <label class="f">Звук перехода<select data-k="sfx_name" id="sfxSel"><option value="${esc(st.sfx_name || '')}">${esc(st.sfx_name || 'разные (случайно)')}</option></select></label>
    <label class="f">Громкость музыки под голосом<input type="number" step="0.02" data-k="music_volume" value="${st.music_volume}"></label>
  </div>
  <h2 style="margin-top:20px">Озвучка</h2>
  ${voiceForm(st)}`;
}
// сервис, язык, голос и проба — общий блок для пресета стиля и диалога «Новый проект»
function voiceForm(st) {
  return `<div class="grid g4">
    <label class="f">Сервис озвучки<select data-k="voice" class="vprov">${opt([['none', 'без озвучки'], ['edge', 'Edge TTS (бесплатно, онлайн)'], ['elevenlabs', 'ElevenLabs'], ['lmnt', 'LMNT'], ['fish', 'Fish Speech (локально, клон голоса)'], ['local', 'Локальный голос Windows']], st.voice)}</select></label>
    <label class="f">Язык озвучки<select data-k="lang" class="vlang">${opt(LANGS.some(l => l[0] === st.lang) ? LANGS : [[st.lang, st.lang], ...LANGS], st.lang)}</select></label>
    <label class="f">Голос (для этого языка)<select class="vlist"><option>…</option></select></label>
    <label class="f">Свой ID голоса (код из ElevenLabs / LMNT)<input type="text" data-k="voice_id" class="vid" value="${esc(st.voice_id || (st.voice === 'edge' ? st.edge_voice : '') || '')}" placeholder="вставь Voice ID"></label>
    <input type="hidden" data-k="edge_voice" value="${esc(st.edge_voice || '')}">
    <label class="f" style="grid-column:span 2">Фраза для пробы (пусто — стандартная)<input type="text" class="vtext" placeholder="вставь кусок своего сценария"></label>
    <div class="row" style="grid-column:span 2;flex-wrap:nowrap"><button class="btn s p vplay" type="button" style="white-space:nowrap">▶ послушать голос</button>
      <canvas class="vwave" height="36" style="flex:1;min-width:120px;height:36px;cursor:pointer;display:none" title="Нажми, чтобы перемотать"></canvas>
      <span class="sub vst" style="white-space:nowrap"></span></div>
  </div>`;
}
const LANGS = [['ru', 'русский'], ['en', 'английский'], ['de', 'немецкий'], ['uk', 'украинский'], ['es', 'испанский'], ['fr', 'французский'], ['it', 'итальянский'], ['pt', 'португальский'], ['pl', 'польский'], ['cs', 'чешский'], ['nl', 'нидерландский'], ['tr', 'турецкий'], ['ja', 'японский'], ['ko', 'корейский'], ['zh', 'китайский'], ['ar', 'арабский'], ['hi', 'хинди']];
// выбор голоса: сервис + язык → список голосов этого языка; можно вписать свой ID
function wireVoice(el) {
  const prov = el.querySelector('.vprov'), lang = el.querySelector('.vlang'), list = el.querySelector('.vlist'),
    vid = el.querySelector('.vid'), st = el.querySelector('.vst'), au = new Audio();
  let voices = [];
  const load = async () => {
    const p = prov.value;
    list.innerHTML = ''; voices = [];
    if (!['edge', 'elevenlabs', 'lmnt', 'fish'].includes(p)) { list.innerHTML = '<option>—</option>'; st.textContent = ''; return; }
    st.textContent = 'Загружаю голоса…';
    try { voices = await (await fetch(`/api/voices?provider=${p}&lang=${lang.value}`)).json(); if (!Array.isArray(voices)) throw new Error(voices.detail); }
    catch (e) { st.textContent = '❌ ' + e.message; list.innerHTML = '<option>—</option>'; return; }
    const cur = vid.value.trim();
    const own = cur && !voices.some(v => v.id === cur);
    list.innerHTML = (own ? `<option value="${esc(cur)}">свой: ${esc(cur)}</option>` : '<option value="">— выбери голос —</option>') +
      voices.map(v => `<option value="${esc(v.id)}" ${v.id === cur ? 'selected' : ''}>${esc(v.name)}</option>`).join('');
    st.textContent = `голосов: ${voices.length}`;
  };
  list.onchange = () => { if (list.value) vid.value = list.value; };
  vid.onchange = load;
  prov.onchange = () => { vid.value = ''; load(); };
  lang.onchange = load;
  // озвучиваем короткую фразу этим голосом — не надо пересобирать ролик, чтобы услышать голос.
  // Та же фраза тем же голосом — кнопка просто ставит на паузу / продолжает; волна показывает, где играет
  const btn = el.querySelector('.vplay'), wave = el.querySelector('.vwave');
  let key = '', peaks = [];
  const fmt = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
  const draw = () => {
    const w = wave.width = wave.clientWidth * devicePixelRatio, h = wave.height = 36 * devicePixelRatio, c = wave.getContext('2d');
    const css = getComputedStyle(document.body), n = peaks.length, bw = w / n, pos = au.duration ? au.currentTime / au.duration : 0;
    c.clearRect(0, 0, w, h);
    peaks.forEach((p, i) => {
      c.fillStyle = i / n < pos ? (css.getPropertyValue('--blue').trim() || '#2563eb') : '#cbd5e1';
      const bh = Math.max(2 * devicePixelRatio, p * h);
      c.fillRect(i * bw + bw * 0.2, (h - bh) / 2, bw * 0.6, bh);
    });
    if (au.duration) st.textContent = `${fmt(au.currentTime)} / ${fmt(au.duration)}`;
  };
  const sync = () => { btn.textContent = au.paused ? '▶ слушать' : '⏸ пауза'; draw(); };
  au.ontimeupdate = () => el.isConnected ? draw() : au.pause();  // закрыли окно — замолкаем
  au.onplay = au.onpause = sync;
  au.onended = () => { au.currentTime = 0; sync(); };
  wave.onclick = e => { if (au.duration) { au.currentTime = e.offsetX / wave.clientWidth * au.duration; au.play(); } };
  new ResizeObserver(() => peaks.length && draw()).observe(wave);
  btn.onclick = async () => {
    if (prov.value === 'none') return;
    const body = {voice: prov.value, lang: lang.value, voice_id: list.value || vid.value.trim(), text: el.querySelector('.vtext').value};
    if (JSON.stringify(body) === key && au.src) { au.paused ? au.play() : au.pause(); return; }
    au.pause(); st.textContent = 'Озвучиваю пробную фразу…'; btn.disabled = true;
    try {
      const r = await fetch('/api/voices/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      if (!r.ok) { st.textContent = '❌ ' + ((await r.json().catch(() => ({}))).detail || r.status); return; }
      const blob = await r.blob();
      // волна: максимум громкости по ~90 кусочкам
      const buf = await new AudioContext().decodeAudioData(await blob.arrayBuffer()), d = buf.getChannelData(0), N = 90, step = Math.floor(d.length / N);
      peaks = Array.from({length: N}, (_, i) => { let m = 0; for (let j = i * step; j < (i + 1) * step; j += 16) m = Math.max(m, Math.abs(d[j])); return m; });
      const top = Math.max(...peaks) || 1; peaks = peaks.map(p => p / top);
      wave.style.display = ''; key = JSON.stringify(body);
      au.src = URL.createObjectURL(blob); au.play();
    } finally { btn.disabled = false; }
  };
  load();
}
// один плеер голоса на весь софт: повторное нажатие — пауза, другая сцена — останавливает прежнюю
const VOICE = {au: new Audio(), btn: null, label: ''};
function toggleVoice(btn, src) {
  const v = VOICE, same = v.btn === btn && v.au.src.endsWith(src);
  if (same && !v.au.paused) { v.au.pause(); btn.textContent = v.label; return; }
  if (v.btn && v.btn !== btn) v.btn.textContent = v.label;
  if (!same) { v.au.pause(); v.au.src = src; v.label = btn.textContent; v.btn = btn; }
  v.au.play().catch(() => {});
  btn.textContent = '⏸ пауза';
  v.au.onended = () => { if (v.btn) v.btn.textContent = v.label; };
}
addEventListener('hashchange', () => VOICE.au.pause());
function readStyle(el) {
  const st = {};
  el.querySelectorAll('[data-k]').forEach(i => {
    const k = i.dataset.k;
    st[k] = i.type === 'checkbox' ? i.checked : i.type === 'number' ? parseFloat(i.value) : (k === 'fps' ? +i.value : i.value);
  });
  return st;
}
async function tabStyle() {
  const p = state.project;
  const refs = await api('/api/refs');
  $('#tab').innerHTML = `
  <div class="card" style="margin-bottom:14px">
    <div class="row"><b>Взять стиль из референса:</b>
      <select id="ref" style="max-width:340px"><option value="">—</option>${refs.filter(r => r.status === 'ready').map(r => `<option value="${r.id}" ${r.id === p.reference_id ? 'selected' : ''}>${esc(r.name)}</option>`).join('')}</select>
      <button class="btn" id="apply">Применить</button></div>
  </div>
  <div class="card" id="st">${styleForm(p.style)}
    <div class="row" style="margin-top:20px"><div class="spacer"></div><button class="btn p" id="save">Сохранить стиль</button></div></div>`;
  wireVoice($('#st'));
  $('#apply').onclick = guard(async () => { if (!$('#ref').value) return; state.project = await api(`/api/projects/${p.id}/apply_ref`, {method: 'POST', body: {reference_id: $('#ref').value}}); toast('Стиль применён'); tabStyle(); });
  $('#save').onclick = guard(async () => { await save({style: readStyle($('#st'))}); toast('Сохранено. Нажми «Пересобрать» в редакторе.'); });
}

// --- музыка
function tabMusic() {
  const p = state.project, mu = p.music;
  $('#tab').innerHTML = `
  <div class="grid g2">
    <div class="card">
      <h2>Текущий трек</h2>
      ${mu ? `<audio controls src="/api/projects/${p.id}/file/${mu.path}" style="width:100%"></audio>
        <div class="row" style="margin-top:12px">
          <label class="f">Режим<select id="mode"><option value="background" ${mu.mode === 'background' ? 'selected' : ''}>Фон (тихо, под голос)</option><option value="song" ${mu.mode === 'song' ? 'selected' : ''}>Песня (громко, ролик под трек)</option></select></label>
          <div class="spacer"></div><button class="btn danger" id="rm">Убрать</button></div>
        <p class="sub" style="margin-top:10px">«Песня» без озвучки — длительность ролика подгоняется под длину трека.</p>`
      : '<p class="sub">Музыки нет. Загрузи свой трек или сгенерируй в Suno.</p>'}
      <label class="btn" style="display:inline-block;margin-top:8px">⬆ Загрузить свой трек<input type="file" id="up" accept="audio/*" hidden></label>
    </div>
    <div class="card">
      <h2>Сгенерировать в Suno</h2>
      <textarea id="prompt" placeholder="epic cinematic orchestral, dark, slow build, documentary">${esc(p.music_prompt || '')}</textarea>
      <p class="sub" style="margin:6px 0 0">Если трека нет, «⚡ Сделать ролик» сам сгенерирует его по этому промпту (строка <code>MUSIC:</code> в задаче).</p>
      <div class="row" style="margin-top:10px">
        <label class="chk"><input type="checkbox" id="instr" checked> Инструментал</label>
        <select id="smode" style="width:auto"><option value="background">как фон</option><option value="song">как песня</option></select>
        <div class="spacer"></div><button class="btn p" id="gen">🎵 Сгенерировать</button>
      </div>
      <div class="log" id="slog" style="display:none"></div>
    </div>
  </div>`;
  $('#mode') && ($('#mode').onchange = guard(async e => { await save({music: {...mu, mode: e.target.value}}); toast('Сохранено'); }));
  $('#rm') && ($('#rm').onclick = guard(async () => { await save({music: null}); tabMusic(); }));
  $('#up').onchange = guard(async e => {
    const fd = new FormData(); fd.append('file', e.target.files[0]); fd.append('mode', mu?.mode || 'background');
    state.project = await api(`/api/projects/${p.id}/music/upload`, {method: 'POST', body: fd}); tabMusic();
  });
  const watch = () => {
    const t = setInterval(async () => {
      const s = await api(`/api/projects/${p.id}/music/status`);
      $('#slog').style.display = ''; $('#slog').textContent = s.log.join('\n') + (s.error ? '\nОШИБКА: ' + s.error : '');
      if (!s.running) { clearInterval(t); $('#gen').disabled = false; if (!s.error) { state.project = await api('/api/projects/' + p.id); tabMusic(); } }
    }, 3000);
  };
  $('#prompt').onchange = guard(async e => { await save({music_prompt: e.target.value}); });
  $('#gen').onclick = guard(async () => {
    $('#gen').disabled = true;
    await api(`/api/projects/${p.id}/music/suno`, {method: 'POST', body: {prompt: $('#prompt').value, instrumental: $('#instr').checked, mode: $('#smode').value}});
    watch();
  });
}

// --- YouTube мета
function tabMeta() {
  const m = state.project.meta;
  const copyBtn = id => `<button class="btn s" data-copy="${id}">копировать</button>`;
  $('#tab').innerHTML = `
  <div class="card grid">
    <label class="f"><div class="row">Название ${copyBtn('t')}</div><input type="text" id="t" value="${esc(m.title)}"></label>
    <label class="f"><div class="row">Описание ${copyBtn('d')}</div><textarea id="d" style="min-height:160px">${esc(m.description)}</textarea></label>
    <label class="f"><div class="row">Теги ${copyBtn('g')}</div><input type="text" id="g" value="${esc(m.tags)}"></label>
    <label class="f"><div class="row">Промпты для превью (Nano Banana), по одному на строку ${copyBtn('th')}</div><textarea id="th" style="min-height:120px">${esc((m.thumb_prompts || []).join('\n\n'))}</textarea></label>
    <div class="row"><div class="spacer"></div><button class="btn p" id="save">Сохранить</button></div>
  </div>`;
  document.querySelectorAll('[data-copy]').forEach(b => b.onclick = e => { e.preventDefault(); navigator.clipboard.writeText($('#' + b.dataset.copy).value); toast('Скопировано'); });
  $('#save').onclick = guard(async () => {
    await save({meta: {title: $('#t').value, description: $('#d').value, tags: $('#g').value, thumb_prompts: $('#th').value.split(/\n\s*\n/).map(x => x.trim()).filter(Boolean)}});
    toast('Сохранено');
  });
}

// --- результат
function tabResult() {
  const p = state.project;
  if (!p.output) { $('#tab').innerHTML = '<div class="card empty">Ролик ещё не собран. Нажми «⚡ Сделать ролик».</div>'; return; }
  const src = `/api/projects/${p.id}/file/${encodeURIComponent(p.output)}?v=${Date.parse(p.updated?.replace(' ', 'T')) || Date.now()}`;
  $('#tab').innerHTML = `<div class="card">
    <video src="${src}" controls style="${p.style.format === '9:16' ? 'max-width:420px;display:block;margin:auto' : ''}"></video>
    <div class="row" style="margin-top:14px"><b>${esc(p.output)}</b><div class="spacer"></div>
      <a class="btn" href="#p/${p.id}/editor">✂️ Поправить в редакторе</a>
      <a class="btn p" href="${src}" download="${esc(p.output)}">⬇ Скачать mp4</a></div>
  </div>`;
}

// ------------------------------------------------ референсы
async function pageRefs(id) {
  const [refs, chs] = await Promise.all([api('/api/refs'), api('/api/channels')]);
  const cur = refs.find(r => r.id === id);
  view.innerHTML = `
  <div class="hero">
    <div><h1>Референсы</h1><p class="sub">Дай ссылку на YouTube-канал — софт скачает пару его роликов, измерит темп, формат и соберёт пресет стиля.</p></div>
    <div class="spacer"></div>
    <label class="btn big ghost">⬆ Или видеофайл<input type="file" id="up" accept="video/*" hidden></label>
  </div>
  <div class="card row" style="margin-bottom:16px">
    <input type="text" id="yt" placeholder="https://www.youtube.com/@канал  (или /shorts, или ссылка на одно видео)" style="flex:1;min-width:260px">
    <select id="ytch" style="width:auto"><option value="">— для канала —</option>${chs.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select>
    <button class="btn p" id="ytgo">🔎 Разобрать канал</button>
  </div>
  <div class="grid" style="grid-template-columns:280px 1fr;align-items:start">
    <div class="grid">${refs.map(r => `<div class="card click" data-id="${r.id}" style="position:relative;${r.id === id ? 'border-color:var(--blue)' : ''}">
      <button class="ghost rdel1" data-del="${r.id}" title="Удалить референс" style="position:absolute;top:8px;right:8px;padding:2px 7px">🗑</button>
      <b style="display:block;padding-right:34px">${esc(r.name)}</b><div style="margin-top:6px">${r.status === 'ready' ? `<span class="pill ok">${r.analysis.format} · ${r.analysis.duration}с</span>` : r.status === 'error' ? '<span class="pill err">ошибка</span>' : r.status === 'deep' ? '<span class="pill gray">ждёт полного разбора в чате</span>' : '<span class="pill gray">анализ…</span>'}</div></div>`).join('') || '<div class="card empty">Пусто</div>'}</div>
    <div id="refview">${cur ? '' : '<div class="card empty">Выбери референс слева</div>'}</div>
  </div>`;
  view.querySelectorAll('[data-id]').forEach(c => c.onclick = () => go('refs/' + c.dataset.id));
  view.querySelectorAll('[data-del]').forEach(b => b.onclick = guard(async e => {
    e.stopPropagation();
    const r = refs.find(x => x.id === b.dataset.del);
    if (!confirm(`Удалить референс «${r.name}»?`)) return;
    await api('/api/refs/' + r.id, {method: 'DELETE'});
    r.id === id ? go('refs') : route();
  }));
  $('#ytgo').onclick = guard(async () => {
    const r = await api('/api/refs/youtube', {method: 'POST', body: {url: $('#yt').value, channel_id: $('#ytch').value || null}});
    toast('Качаю и разбираю канал — это займёт пару минут'); go('refs/' + r.id);
  });
  $('#up').onchange = guard(async e => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append('file', f); fd.append('name', f.name.replace(/\.[^.]+$/, ''));
    toast('Загружаю и анализирую…');
    const r = await api('/api/refs', {method: 'POST', body: fd}); go('refs/' + r.id);
  });
  // перерисовываем только при реальных изменениях — иначе сбрасывается ввод имени и видео
  const sig = rs => rs.map(r => r.id + r.status + (r.log || []).length).join('|');
  if (refs.some(r => r.status === 'analyzing')) state.poll = setInterval(async () => {
    const nr = await api('/api/refs');
    if (sig(nr) === sig(refs)) return;
    // запоминаем прокрутку лога: внизу — едем за новыми строками, прокрутил вверх — не сбиваем
    const lg = $('#refview .log');
    state.refLog = lg && {id: cur?.id, top: lg.scrollTop, end: lg.scrollHeight - lg.scrollTop - lg.clientHeight < 30};
    const y = window.scrollY;
    await route();
    window.scrollTo(0, y);
  }, 2500);
  if (!cur || cur.status !== 'ready') {
    if (cur?.status === 'error') $('#refview').innerHTML = `<div class="card">Ошибка анализа: ${esc(cur.error)}</div>`;
    else if (cur) {
      $('#refview').innerHTML = `<div class="card"><h2>⏳ Разбираю…</h2><div class="log">${esc((cur.log || ['Начинаю…']).join('\n'))}</div></div>`;
      const lg = $('#refview .log'), s = state.refLog;
      lg.scrollTop = s && s.id === cur.id && !s.end ? s.top : lg.scrollHeight;
    }
    return;
  }
  const a = cur.analysis;
  $('#refview').innerHTML = `
  <div class="card" style="margin-bottom:14px">
    <div class="row"><input type="text" id="rn" value="${esc(cur.name)}" style="max-width:360px;font-weight:700"><div class="spacer"></div><button class="btn danger s" id="rdel">Удалить</button></div>
    <div class="grid g4" style="margin-top:14px">
      <div class="stat"><b>${a.format}</b><span>${a.width}×${a.height}, ${a.fps} fps</span></div>
      <div class="stat"><b>${a.median_shot}с</b><span>типичная длина кадра</span></div>
      <div class="stat"><b>${a.cuts_per_min}</b><span>склеек в минуту</span></div>
      <div class="stat"><b>${a.duration}с</b><span>длина, ${a.has_audio ? 'со звуком' : 'без звука'}</span></div>
    </div>
    <div class="frames" style="margin-top:14px">${cur.frames.map(f => `<img src="/api/refs/${cur.id}/file/${f}">`).join('')}</div>
    ${(cur.videos || []).length > 1 ? `<div class="row" style="margin-top:14px">${cur.videos.map((v, i) => `<button class="btn s rvid${v === cur.file ? ' p' : ''}" data-v="${esc(v)}">Ролик ${i + 1}</button>`).join('')}</div>` : ''}
    ${cur.file ? `<video id="rvideo" src="/api/refs/${cur.id}/file/${cur.file}" controls preload="metadata" style="margin-top:14px;max-height:360px"></video>` : ''}
    ${cur.analysis_id ? `<p class="sub" style="margin:10px 0 0">Из разбора видео: <a href="#analyses/${cur.analysis_id}">открыть полный разбор</a></p>` : ''}
    <div class="row" style="margin-top:12px">
      ${cur.passport ? `<span class="sub">✅ Монтаж разобран в чате — паспорт и скил монтажа применяются к проектам</span>`
        : cur.deep_analysis ? `<span class="sub">⏳ Монтаж разбирается в чате Claude (откройте чат — я подхвачу задачу)</span>`
        : `<span class="sub">Монтаж ещё не разобран полностью — сейчас взяты только цифры и цвета</span>`}
      ${cur.deep_analysis ? `<a class="sub" href="#analyses/${cur.deep_analysis}">разбор</a>` : ''}
      <div class="spacer"></div>
      ${cur.url || cur.video_urls?.length || cur.file ? `<button class="btn s${cur.passport ? '' : ' p'}" id="rdeep">🔎 ${cur.passport ? 'Разобрать заново' : 'Разобрать монтаж в чате'}</button>` : ''}
    </div>
    ${cur.url ? `<p class="sub" style="margin:10px 0 0">Источник: <a href="${esc(cur.url)}" target="_blank">${esc(cur.channel_title || cur.url)}</a> · разобрано роликов: ${a.videos || 1}</p>` : ''}
    ${cur.titles?.length ? `<details style="margin-top:8px"><summary class="sub" style="cursor:pointer">Названия роликов канала (${cur.titles.length}) — ИИ пишет в похожем стиле</summary><div class="sub">${cur.titles.map(esc).join('<br>')}</div></details>` : ''}
    <div style="margin-top:14px"><b style="font-size:12px;color:var(--muted)">ДЛЯ КАНАЛОВ</b><div class="row" style="margin-top:6px">
      ${chs.map(c => `<label class="chk"><input type="checkbox" class="rch" value="${c.id}" ${c.reference_id === cur.id ? 'checked' : ''}> ${esc(c.name)}</label>`).join('') || '<span class="sub">Каналов пока нет — создай во вкладке «Каналы»</span>'}
    </div></div>
    <label class="f" style="margin-top:14px">Заметки по стилю (что видишь: шрифты, цвета, где плашки, какой голос…)<textarea id="notes">${esc(cur.notes)}</textarea></label>
  </div>
  <div class="card" id="rst"><h2>Пресет стиля</h2><p class="sub">Темп и формат подставлены автоматически. Субтитры/плашки/переходы — отметь, как в референсе.</p>${styleForm(cur.style)}
    <div class="row" style="margin-top:18px"><div class="spacer"></div><button class="btn p" id="rsave">Сохранить пресет</button></div></div>`;
  wireVoice($('#rst'));
  // имя сохраняем сразу — раньше оно уходило только с кнопкой «Сохранить пресет» и терялось
  $('#rn').onchange = guard(async () => {
    const name = $('#rn').value.trim(); if (!name || name === cur.name) return;
    await api('/api/refs/' + cur.id, {method: 'PATCH', body: {name}}); cur.name = name; toast('Название сохранено');
    const li = view.querySelector(`[data-id="${cur.id}"] b`); if (li) li.textContent = name;
  });
  $('#rn').onkeydown = e => { if (e.key === 'Enter') e.target.blur(); };
  view.querySelectorAll('.rvid').forEach(b => b.onclick = () => {
    $('#rvideo').src = `/api/refs/${cur.id}/file/${b.dataset.v}`;
    view.querySelectorAll('.rvid').forEach(x => x.classList.toggle('p', x === b));
  });
  $('#rdel').onclick = guard(async () => { if (confirm('Удалить референс?')) { await api('/api/refs/' + cur.id, {method: 'DELETE'}); go('refs'); } });
  if ($('#rdeep')) $('#rdeep').onclick = guard(async () => { await api('/api/refs/' + cur.id + '/deep', {method: 'POST'}); toast('Задача на разбор отправлена в чат Claude'); route(); });
  view.querySelectorAll('.rch').forEach(x => x.onchange = guard(async () => {
    await api(`/api/refs/${cur.id}/channel`, {method: 'POST', body: {channel_ids: [...view.querySelectorAll('.rch:checked')].map(i => i.value)}}); toast('Каналы обновлены');
  }));
  $('#rsave').onclick = guard(async () => { await api('/api/refs/' + cur.id, {method: 'PATCH', body: {name: $('#rn').value, notes: $('#notes').value, style: readStyle($('#rst'))}}); toast('Пресет сохранён'); });
}

// ------------------------------------------------ настройки
async function pageSettings() {
  const [s, g] = await Promise.all([api('/api/settings'), api('/api/google_chrome')]); SETTINGS = s;
  const f = (k, label, ph = '', type = 'password') => `<label class="f">${label}<input type="${type}" data-k="${k}" value="${esc(s[k])}" placeholder="${ph}"></label>`;
  view.innerHTML = `
  <h1>Настройки и API</h1><p class="sub">Ключи хранятся только у тебя на компьютере в <code>data/settings.json</code> и не попадают в GitHub.</p>
  <div class="grid g2">
    <div class="card grid"><h2>Стоки</h2>
      ${f('pexels_key', 'Pexels API key', 'pexels.com/api')}
      ${f('pixabay_key', 'Pixabay API key', 'pixabay.com/api/docs')}
      <label class="f">Источники по порядку (через запятую)<input type="text" id="sources" value="${esc(s.sources.join(', '))}"></label>
      <p class="sub" style="margin:0">Wikimedia Commons работает без ключа. Куски YouTube ищутся сами вместе со стоками (доля — в стиле проекта); моменты выбирает Gemini, если есть ключ Google.</p>
    </div>
    <div class="card grid"><h2>Картинки — Google (Nano Banana)</h2>
      ${f('google_key', 'Google AI Studio API key')}
      ${f('google_image_model', 'Модель', '', 'text')}
    </div>
    <div class="card grid"><h2>Картинки — G-Labs (Nano Banana Pro)</h2>
      ${f('glabs_key', 'G-Labs API key')}
      ${f('glabs_base', 'Адрес G-Labs (IP и порт)', '127.0.0.1:8765', 'text')}
      <p class="sub" style="margin:0">Адрес — где запущена программа G-Labs: можно написать только порт (9000), IP (192.168.1.5) или IP:порт. Пусто — 127.0.0.1:8765. Если ключ есть, ИИ-картинки идут через G-Labs. Видео-кадры (Omni Flash, 720p) включаются в стиле проекта: «ИИ-кадры → видео». Пробный клип можно сделать кнопкой ниже.</p>
      <label class="f">Промпт пробного клипа<input type="text" id="glvp" placeholder="slow cinematic shot of a rainy Tokyo street at night"></label>
      <div class="row"><span class="sub" id="glvst"></span><div class="spacer"></div><button class="btn s p" id="glvgo">Сделать видео</button></div>
      <video id="glvv" controls style="display:none;width:100%"></video>
      <div class="row"><span class="sub" id="glst"></span><div class="spacer"></div><button class="btn s" id="glck">Проверить</button></div>
    </div>
    <div class="card grid"><h2>Озвучка — Fish Speech (локально)</h2>
      ${f('fish_dir', 'Папка Fish Speech', '', 'text')}
      ${f('fish_port', 'Порт', '8180', 'text')}
      <p class="sub" style="margin:0">Голоса = папки в <code>references</code> (wav + .lab с текстом). Сервер запускается сам при первой озвучке.</p>
    </div>
    <div class="card grid"><h2>ИИ-разбор сценария</h2>
      <p class="sub" style="margin:0">Чтобы сценарий можно было вставлять в любом виде. Хватит Google-ключа выше (Gemini), или укажи ключ Claude.</p>
      ${f('anthropic_key', 'Claude API key (необязательно)', 'console.anthropic.com')}
      ${f('anthropic_model', 'Модель Claude', '', 'text')}
      ${f('google_text_model', 'Модель Gemini', '', 'text')}
    </div>
    <div class="card grid"><h2>Музыка — Suno</h2>
      ${f('suno_key', 'Suno API key')}
      ${f('suno_base', 'Адрес API', '', 'text')}
      ${f('suno_model', 'Модель', '', 'text')}
      <div class="row"><span class="sub" id="sunost"></span><div class="spacer"></div><button class="btn s" id="sunock">Проверить ключ</button><button class="btn s p" id="sunotest">Тест генерации</button></div>
      <audio id="sunoau" controls style="display:none;width:100%"></audio>
    </div>
    <div class="card grid"><h2>Озвучка — LMNT</h2>
      ${f('lmnt_key', 'LMNT API key', 'app.lmnt.com')}
      <p class="sub" style="margin:0">Голос выбирается в пресете референса или в стиле проекта.</p>
    </div>
    <div class="card grid"><h2>Монтаж — FFmpeg</h2>
      <p class="sub" style="margin:0">Без FFmpeg ролик не соберётся. Проверка: установлен ли он и есть ли нужные кодеки.</p>
      <div class="row"><span class="sub" id="ffst"></span><div class="spacer"></div><button class="btn s" id="ffck">Проверить FFmpeg</button></div>
    </div>
    <div class="card grid" id="gcard"><h2>Google-аккаунт (музыка Google)</h2>
      <p class="sub" style="margin:0">Откроется отдельный Chrome. Войди в аккаунт один раз — сессия запомнится в профиле
      <code>data/chrome_google</code>, и дальше вход уже не нужен. Пароль софт не хранит.</p>
      <label class="f">Аккаунт (для себя, необязательно)<input type="text" id="gacc" value="${esc(g.account)}" placeholder="name@gmail.com"></label>
      <label class="f">Страница<input type="text" id="gurl" value="${esc(g.url)}"></label>
      <div class="row"><span class="pill ${g.running ? '' : 'gray'}" id="gst">${!g.chrome ? 'Chrome не найден' : g.running ? 'открыт' : g.profile ? 'сессия сохранена' : 'ещё не входил'}</span>
        <div class="spacer"></div><button class="btn s danger" id="greset">Выйти (стереть сессию)</button>
        <button class="btn s p" id="gopen">Открыть Chrome</button></div>
    </div>
    <div class="card grid"><h2>Озвучка — ElevenLabs</h2>
      ${f('elevenlabs_key', 'ElevenLabs API key')}
      ${f('elevenlabs_voice', 'Voice ID по умолчанию', '', 'text')}
      <p class="sub" style="margin:0">Без ключа можно использовать бесплатный Edge TTS.</p>
      <div class="row"><span class="sub" id="elst"></span><div class="spacer"></div><button class="btn s" id="elck">Проверить озвучку</button></div>
      <audio id="elau" controls style="display:none;width:100%"></audio>
    </div>
  </div>
  <div class="row" style="margin-top:16px"><div class="spacer"></div><button class="btn p big" id="save">Сохранить</button></div>`;
  $('#glvgo').onclick = guard(async () => {
    const v = k => view.querySelector(`[data-k="${k}"]`).value.trim();
    $('#glvst').textContent = '⏳ генерирую (1-3 мин)…';
    const r = await api('/api/glabs/video', {method: 'POST', body: {glabs_key: v('glabs_key'), glabs_base: v('glabs_base'), prompt: $('#glvp').value}});
    $('#glvst').textContent = '✅ ' + r.path;
    const vv = $('#glvv'); vv.style.display = ''; vv.src = '/api/glabs/video.mp4?p=' + encodeURIComponent(r.path);
  });
  $('#glck').onclick = guard(async () => {
    const v = k => view.querySelector(`[data-k="${k}"]`).value.trim();
    const r = await api('/api/glabs/check', {method: 'POST', body: {glabs_key: v('glabs_key'), glabs_base: v('glabs_base')}});
    $('#glst').textContent = (r.ok ? '✅ ' : '❌ ') + r.msg;
  });
  $('#ffck').onclick = guard(async () => {
    const r = await api('/api/ffmpeg/check'); $('#ffst').textContent = (r.ok ? '✅ ' : '❌ ') + r.msg;
  });
  $('#elck').onclick = guard(async () => {
    const v = k => view.querySelector(`[data-k="${k}"]`).value.trim();
    $('#elst').textContent = 'Проверяю…';
    const r = await api('/api/elevenlabs/check', {method: 'POST', body: {elevenlabs_key: v('elevenlabs_key'), elevenlabs_voice: v('elevenlabs_voice')}});
    $('#elst').textContent = (r.ok ? '✅ ' : '❌ ') + r.msg;
    const au = $('#elau'); au.style.display = r.audio ? '' : 'none';
    if (r.audio) { au.src = '/api/elevenlabs/test.mp3?t=' + Date.now(); au.play().catch(() => {}); }
  });
  $('#sunotest').onclick = guard(async () => {
    if (!confirm('Сгенерировать пробный трек? Это потратит кредиты Suno на одну генерацию (1–3 мин).')) return;
    const v = k => view.querySelector(`[data-k="${k}"]`).value.trim();
    let r = await api('/api/suno/test', {method: 'POST', body: {suno_key: v('suno_key'), suno_base: v('suno_base'), suno_model: v('suno_model')}});
    while (r.state === 'running') {
      $('#sunost').textContent = '⏳ ' + r.msg;
      await new Promise(ok => setTimeout(ok, 3000));
      r = await api('/api/suno/test');
    }
    $('#sunost').textContent = (r.state === 'done' ? '✅ ' : '❌ ') + r.msg;
    const au = $('#sunoau'); au.style.display = r.state === 'done' ? '' : 'none';
    if (r.state === 'done') { au.src = '/api/suno/test.mp3?t=' + Date.now(); au.play().catch(() => {}); }
  });
  $('#sunock').onclick = guard(async () => {
    const v = k => view.querySelector(`[data-k="${k}"]`).value.trim();
    const r = await api('/api/suno/check', {method: 'POST', body: {suno_key: v('suno_key'), suno_base: v('suno_base')}});
    $('#sunost').textContent = (r.ok ? '✅ ' : '❌ ') + r.msg;
  });
  $('#gopen').onclick = guard(async () => {
    const r = await api('/api/google_chrome/open', {method: 'POST', body: {url: $('#gurl').value.trim(), account: $('#gacc').value.trim()}});
    $('#gst').textContent = r.running ? 'открыт' : 'не запустился'; toast(r.profile ? 'Chrome открыт' : 'Войди в аккаунт в открывшемся окне');
  });
  $('#greset').onclick = guard(async () => {
    if (!confirm('Стереть сохранённую сессию Google?')) return;
    await api('/api/google_chrome/reset', {method: 'POST'}); pageSettings();
  });
  $('#save').onclick = guard(async () => {
    const patch = {sources: $('#sources').value.split(',').map(x => x.trim()).filter(Boolean)};
    view.querySelectorAll('[data-k]').forEach(i => patch[i.dataset.k] = i.value.trim());
    await api('/api/settings', {method: 'POST', body: patch}); SETTINGS = null; toast('Сохранено');
  });
}

// ------------------------------------------------ формат сценария
function pageHelp() {
  view.innerHTML = `<h1>Формат сценария</h1><p class="sub">Попроси Claude написать сценарий в этом формате — и просто вставь его в проект.</p>
  <div class="grid g2">
  <div class="card"><h2>Пример</h2><pre class="ex">TITLE: Как Nike обогнал Reebok
DESCRIPTION: История самой громкой битвы кроссовок.
Подписывайся на канал!
TAGS: nike, reebok, история брендов, кроссовки
THUMB: two sneakers facing each other like boxers, dramatic blue light, bold text "WAR"

## 1984 год
> reebok aerobics 80s; vintage gym women
В 1984 году Reebok продавал больше кроссовок, чем Nike.

> basketball court night; michael jordan
Но у Nike был один молодой баскетболист.
! young basketball player 1984, cinematic photo, dramatic light</pre></div>
  <div class="card"><h2>Правила</h2>
    <p><code>TITLE:</code> <code>DESCRIPTION:</code> <code>TAGS:</code> <code>THUMB:</code> — данные для YouTube (можно по-русски: НАЗВАНИЕ, ОПИСАНИЕ, ТЕГИ, ПРЕВЬЮ).</p>
    <p><b>Пустая строка</b> — новая сцена.</p>
    <p><code>## текст</code> — плашка-заголовок сцены.</p>
    <p><code>&gt; запрос</code> — что искать на стоках (лучше на английском, несколько через <code>;</code>).</p>
    <p><code>! промпт</code> — сгенерировать картинку через Google (если есть ключ).</p>
    <p>Остальные строки — <b>текст озвучки и субтитров</b>.</p>
    <p class="sub">Сколько кадров в сцене — софт решает сам: длина озвучки ÷ средняя длина кадра из референса.</p>
    <button class="btn" id="cp">Скопировать промпт для Claude</button>
  </div></div>`;
  $('#cp').onclick = () => { navigator.clipboard.writeText(`Напиши сценарий для YouTube-ролика в формате YT Studio:\nсначала строки TITLE:, DESCRIPTION:, TAGS:, THUMB: (промпт превью на английском для Nano Banana).\nДальше сцены через пустую строку. В каждой сцене: необязательная строка "## плашка", строка "> запросы для стоковых видео на английском через ;", затем текст озвучки (1–3 предложения).\nТема: `); toast('Скопировано'); };
}

route();

// ------------------------------------------------ каналы
async function pageChannels() {
  const [chs, refs] = await Promise.all([api('/api/channels'), api('/api/refs')]);
  const refOpts = sel => `<option value="">— без референса —</option>` + refs.filter(r => r.status === 'ready')
    .map(r => `<option value="${r.id}" ${r.id === sel ? 'selected' : ''}>${esc(r.name)}</option>`).join('');
  view.innerHTML = `
  <div class="hero"><div><h1>Каналы</h1><p class="sub">У каждого канала свой референс (стиль) и своя папка — готовые ролики копируются туда.</p></div></div>
  <div class="grid g3">
    ${chs.map(c => `<div class="card" data-c="${c.id}">
      <label class="f">Название<input type="text" class="cn" value="${esc(c.name)}"></label>
      <label class="f" style="margin-top:10px">Ник на YouTube<input type="text" class="chh" value="${esc(c.handle || '')}" placeholder="@ник"></label>
      <label class="f" style="margin-top:10px">Описание канала<textarea class="chd" rows="4">${esc(c.description || '')}</textarea></label>
      <label class="f" style="margin-top:10px">Референс стиля<select class="cr">${refOpts(c.reference_id)}</select></label>
      <label class="f" style="margin-top:10px">📁 Папка для готовых роликов<input type="text" class="cdir" value="${esc(c.output_dir || '')}" placeholder="D:/YouTube/${esc(c.name)}"></label>
      <div class="row" style="margin-top:12px"><span class="pill gray">проектов: ${c.projects}</span><div class="spacer"></div>
        <button class="btn s danger cd">Удалить</button><button class="btn s p co">Открыть</button></div></div>`).join('')}
    <div class="card"><h2>Новый канал</h2>
      <label class="f">Название<input type="text" id="nn" placeholder="Например: История"></label>
      <label class="f" style="margin-top:10px">Референс стиля<select id="nr">${refOpts('')}</select></label>
      <label class="f" style="margin-top:10px">📁 Папка для готовых роликов<input type="text" id="nd" placeholder="D:/YouTube/История"></label>
      <div class="row" style="margin-top:12px"><div class="spacer"></div><button class="btn p" id="add">＋ Создать</button></div></div>
  </div>`;
  view.querySelectorAll('[data-c]').forEach(el => {
    const id = el.dataset.c;
    const upd = guard(async () => { await api('/api/channels', {method: 'POST', body: {id, name: $('.cn', el).value, handle: $('.chh', el).value.trim(), description: $('.chd', el).value, reference_id: $('.cr', el).value || null, output_dir: $('.cdir', el).value.trim()}}); toast('Сохранено'); chBar(); });
    $('.cn', el).onchange = upd; $('.chh', el).onchange = upd; $('.chd', el).onchange = upd; $('.cr', el).onchange = upd; $('.cdir', el).onchange = upd;
    $('.co', el).onclick = () => { setCh(id); go('projects'); };
    $('.cd', el).onclick = guard(async () => { if (confirm('Удалить канал? Проекты останутся.')) { await api('/api/channels/' + id, {method: 'DELETE'}); pageChannels(); } });
  });
  $('#add').onclick = guard(async () => {
    if (!$('#nn').value.trim()) return toast('Введи название', true);
    await api('/api/channels', {method: 'POST', body: {name: $('#nn').value.trim(), reference_id: $('#nr').value || null, output_dir: $('#nd').value.trim()}}); route();
  });
}


// --- таймлайн (монтаж как в CapCut)
const TL = {pps: 60, sel: null, t: 0, playing: false, pid: null, L: null, audio: {}, music: null, curV: null, raf: 0};
const TRACKS = [['video', '🎞 Видео'], ['plate', '🏷 Плашки'], ['text', 'T Текст'], ['sub', '💬 Субтитры'], ['voice', '🎙 Голос'], ['sfx', '🔊 Звуки'], ['music', '🎵 Музыка']];
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const r2 = v => Math.round(v * 100) / 100;
const fmtT = t => { t = Math.max(0, t); return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}.${Math.floor(t * 10 % 10)}`; };
const pfile = rel => `/api/projects/${state.project.id}/file/${rel}`;
const findScene = id => state.project.scenes.find(s => s.id === id);
const findShot = id => { for (const sc of state.project.scenes) { const s = sc.shots.find(x => x.id === id); if (s) return [sc, s]; } return [null, null]; };

function tlStop() {
  TL.playing = false;
  clearInterval(TL.raf);
  Object.values(TL.audio).forEach(a => a.pause());
  TL.music?.pause();
  TL.curV = null;
  const v = $('#pvVid'); if (v) v.pause();
  const b = $('#tlPlay'); if (b) b.textContent = '▶';
}

async function tabTimeline() {
  tlStop();
  const p = state.project;
  if (TL.pid !== p.id) { TL.pid = p.id; TL.t = 0; TL.sel = null; TL.fit = true; }
  const L = TL.L = await api(`/api/projects/${p.id}/timeline`);
  if (!L.shots.length) { $('#tab').innerHTML = '<div class="card empty">Пока нет кадров. Нажми «⚡ Сделать ролик» или добавь медиа во вкладке «Редактор».</div>'; return; }
  $('#tab').innerHTML = `
  <div class="tl-top">
    <div class="tl-pv-wrap" id="pvwrap"><div class="tl-pv" id="pv">
      <img id="pvImg" alt=""><video id="pvVid" muted playsinline preload="auto"></video>
      <div id="pvPlate" class="pv-plate"></div><div id="pvText"></div><div id="pvSub" class="pv-sub"></div>
    </div></div>
    <div class="card tl-insp" id="insp"></div>
  </div>
  <div class="card tl-bar">
    <button class="btn p s" id="tlPlay" title="Пробел">▶</button>
    <button class="btn s" id="tlStart" title="В начало">⏮</button>
    <span class="tl-time" id="tlTime"></span>
    <span id="tlWarn"></span>
    <div class="spacer"></div>
    <button class="btn s" id="tlAddText">＋ Текст</button>
    <button class="btn s" id="tlAddSfx">＋ Звук</button>
    <label class="tl-zoom" title="Масштаб (Ctrl + колесо)">🔍<input type="range" id="tlZoom" min="0" max="1" step="0.001"></label>
    <button class="btn s" id="tlFit">Вписать</button>
    <button class="btn p s" id="tlRender">🎬 Пересобрать ролик</button>
  </div>
  <div class="card tl" id="tl">
    <div class="tl-heads"><div class="tl-rul-h"></div>${TRACKS.map(([, n]) => `<div class="tl-h">${n}</div>`).join('')}</div>
    <div class="tl-sc" id="tlsc"><div class="tl-in" id="tlin"></div></div>
  </div>
  <p class="sub" style="margin:8px 0 0">Пробел — пуск/пауза · ← → — на кадр (Shift — на секунду) · колесо — листать · Ctrl+колесо — масштаб ·
    тяни правый край кадра/плашки/текста — длина · тяни плашку, текст, звук — сдвиг · Delete — удалить выбранное · после правок — «Пересобрать ролик»</p>`;
  if (TL.fit) TL.pps = tlFitPps();
  tlZoomUI();
  tlAudio();
  tlDraw();
  tlInsp();
  tlFitPreview();
  new ResizeObserver(() => { tlFitPreview(); tlFrame(TL.t, TL.playing); }).observe($('#pvwrap'));
  new ResizeObserver(() => { if (TL.fit && Math.abs(tlFitPps() - TL.pps) > 0.01) { TL.pps = tlFitPps(); tlZoomUI(); tlDraw(); } }).observe($('#tlsc'));
  tlFrame(TL.t, false);
  tlBind();
}

function tlAudio() {
  const L = TL.L;
  Object.values(TL.audio).forEach(a => a.pause());
  TL.music?.pause();
  TL.audio = {};
  L.voices.forEach(v => { const a = new Audio(pfile(v.file)); a.preload = 'auto'; TL.audio[v.file] = a; });
  TL.music = null;
  if (L.music) { TL.music = new Audio(pfile(L.music.file)); TL.music.loop = true; TL.music.preload = 'auto'; TL.music.volume = clamp(L.music.vol, 0, 1); }
}

function tlFitPreview() {
  const w = $('#pvwrap'), pv = $('#pv'); if (!w || !pv) return;
  const L = TL.L, cw = w.clientWidth - 24, ch = w.clientHeight - 24;
  const k = Math.min(cw / L.W, ch / L.H);
  pv.style.width = Math.floor(L.W * k) + 'px'; pv.style.height = Math.floor(L.H * k) + 'px';
}

function tlBlocks(k, items, cls, label, opt = {}) {
  const pps = TL.pps;
  return items.map(it => {
    const id = opt.id ? opt.id(it) : it.id;
    const sel = TL.sel && TL.sel.k === k && TL.sel.id === id ? ' sel' : '';
    const st = opt.style ? opt.style(it) : '';
    return `<div class="tl-b ${cls}${sel}${it.bad ? ' bad' : ''}${it.auto ? ' auto' : ''}" data-k="${k}" data-id="${esc(id)}" ${it.auto ? 'data-auto="1"' : ''}
      style="left:${it.start * pps}px;width:${Math.max(3, it.dur * pps)}px;${st}" title="${esc(label(it))}"><span>${esc(label(it))}</span>${opt.resize ? '<i class="h-r"></i>' : ''}</div>`;
  }).join('');
}

function tlDraw() {
  const L = TL.L, pps = TL.pps, p = state.project;
  const width = L.total * pps + 80;
  const step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find(s => s * pps >= 70) || 600;
  let ticks = '';
  for (let t = 0; t <= L.total + 80 / pps; t += step) ticks += `<div class="tl-tick" style="left:${t * pps}px">${fmtT(t).replace(/\.\d$/, '')}</div>`;
  const shotItems = L.shots.map(x => { const [, s] = findShot(x.shot); return {...x, id: x.shot, s}; });
  const trans = L.td ? L.scenes.slice(1).filter((s, i) => Math.abs(s.start - (L.scenes[i].start + L.scenes[i].dur)) > 0.01)
    .map(s => `<div class="tl-tr" style="left:${(s.start + L.td / 2) * pps}px" title="переход ${L.td} с"></div>`).join('') : '';
  const music = [];
  if (L.music) {
    const loop = L.music.loop > 0.5 ? L.music.loop : L.total;
    for (let x = 0, n = 1; x < L.total - 0.01; x += loop, n++) music.push({start: x, dur: Math.min(loop, L.total - x), id: 'm' + n, n});
  }
  const sfx = L.sfx.map((s, i) => ({...s, id: s.auto ? 'auto' + i : s.id}));
  const rows = {
    video: tlBlocks('shot', shotItems, 'b-video', x => `${x.dur.toFixed(1)}с · ${x.s?.media?.source || ''}`, {
      resize: true, style: x => x.s?.media?.thumb ? `background-image:url('${pfile(x.s.media.thumb)}')` : ''}) + trans,
    plate: tlBlocks('plate', L.plates, 'b-plate', x => x.text, {id: x => x.scene, resize: true}),
    text: tlBlocks('text', L.texts.map(t => ({...t, start: +t.start || 0, dur: +t.dur || 2})), 'b-text', x => x.text, {resize: true}),
    sub: tlBlocks('sub', L.subs, 'b-sub', x => x.text, {id: x => x.scene + ':' + x.start}),
    voice: tlBlocks('voice', L.voices.map((v, i) => ({...v, bad: i < L.voices.length - 1 && v.start + v.dur > L.voices[i + 1].start + 0.01})), 'b-voice', x => `🎙 ${(findScene(x.scene)?.text || '').slice(0, 60)}`, {id: x => x.scene}),
    sfx: tlBlocks('sfx', sfx, 'b-sfx', x => (x.auto ? 'авто: ' : '') + x.name.replace(/^_/, '')),
    music: tlBlocks('music', music, 'b-music', x => `🎵 ${L.music.name}${music.length > 1 ? ' · повтор ' + x.n : ''}`),
  };
  const bad = L.voices.filter((v, i) => i < L.voices.length - 1 && v.start + v.dur > L.voices[i + 1].start + 0.01).length;
  $('#tlWarn').innerHTML = bad ? `<span class="pill err" title="Голос сцены не успевает закончиться до следующей — фразы наложатся">⚠ голос налезает: ${bad}</span> <button class="btn s" id="tlFix">Подогнать кадры под голос</button>` : '';
  if (bad) $('#tlFix').onclick = guard(async () => { state.project = await api(`/api/projects/${p.id}/rebalance`, {method: 'POST'}); await tlCommit({}); toast('Кадры подогнаны под голос'); });
  $('#tlin').style.width = width + 'px';
  $('#tlin').innerHTML = `<div class="tl-rul" id="tlrul">${ticks}</div>
    ${TRACKS.map(([k]) => `<div class="tl-row" data-row="${k}">${rows[k]}</div>`).join('')}
    <div class="tl-ph" id="tlph"></div>`;
  tlPlayhead();
}

function tlPlayhead() {
  const ph = $('#tlph'); if (ph) ph.style.left = TL.t * TL.pps + 'px';
  const tt = $('#tlTime'); if (tt) tt.textContent = `${fmtT(TL.t)} / ${fmtT(TL.L.total)}`;
}

const ACTIVE = (arr, t) => arr.find(x => t >= x.start && t < x.start + x.dur);

function tlFrame(t, playing) {
  const L = TL.L, st = state.project.style, pv = $('#pv'); if (!pv) return;
  const k = pv.clientHeight / L.H;
  // кадр: последний начавшийся (на переходе — уже следующий)
  let sh = null;
  for (const x of L.shots) { if (x.start <= t + 1e-6) sh = x; else break; }
  const [, s] = sh ? findShot(sh.shot) : [null, null];
  const img = $('#pvImg'), vid = $('#pvVid');
  if (s?.media) {
    const src = pfile(s.media.path), pr = clamp((t - sh.start) / sh.dur, 0, 1);
    if (s.media.type === 'video') {
      img.style.display = 'none'; vid.style.display = '';
      if (vid.dataset.src !== src) { vid.dataset.src = src; vid.src = src; }
      let want = (+s.trim_in || 0) + (t - sh.start);
      const sd = +s.media.src_dur || vid.duration || 0;
      if (sd > 0.2) want %= sd;
      if (playing) {
        if (vid.paused) { vid.currentTime = want; vid.play().catch(() => {}); }
        else if (Math.abs(vid.currentTime - want) > 0.35) vid.currentTime = want;
      } else {
        vid.pause();
        if (Math.abs(vid.currentTime - want) > 0.04) vid.currentTime = want;
      }
    } else {
      vid.pause(); vid.style.display = 'none'; img.style.display = '';
      if (img.dataset.src !== src) { img.dataset.src = src; img.src = src; }
      let tr = 'none';
      if (st.zoom) tr = [`scale(${1 + 0.12 * pr})`, `scale(${1.12 - 0.12 * pr})`,
        `scale(1.1) translateX(${(0.5 - pr) * 9}%)`, `scale(1.1) translateX(${(pr - 0.5) * 9}%)`][sh.idx % 4];
      img.style.transform = tr;
    }
  }
  // плашка
  const pl = ACTIVE(L.plates, t), pe = $('#pvPlate');
  pe.style.display = pl ? '' : 'none';
  if (pl) {
    const fs = st.sub_size * (L.H > L.W ? 1.1 : 1) * 0.85 * k;
    Object.assign(pe.style, {fontSize: fs + 'px', left: 70 * k + 'px', top: 70 * k + 'px', background: st.plate_color, padding: `${fs * 0.12}px ${fs * 0.3}px`});
    pe.textContent = pl.text;
  }
  // свой текст
  const fsz = st.sub_size * (L.H > L.W ? 1.1 : 1) * k;
  $('#pvText').innerHTML = L.texts.filter(x => t >= +x.start && t < +x.start + (+x.dur || 2))
    .map(x => `<div class="pv-text pos-${x.pos || 'top'}" style="font-size:${fsz}px;${x.pos === 'bottom' ? `bottom:${L.H * 0.08 * k}px` : x.pos === 'center' ? '' : `top:${L.H * 0.08 * k}px`}">${esc(x.text)}</div>`).join('');
  // субтитры
  const sb = ACTIVE(L.subs, t), se = $('#pvSub');
  se.style.display = sb ? '' : 'none';
  if (sb) {
    Object.assign(se.style, {fontSize: fsz + 'px', color: st.sub_color, WebkitTextStroke: `${Math.max(1, fsz / 22)}px #000`});
    if (st.sub_position === 'center') { se.style.top = '50%'; se.style.bottom = ''; se.style.transform = 'translateY(-50%)'; }
    else { se.style.top = ''; se.style.bottom = L.H * (L.H > L.W ? 0.22 : 0.08) * k + 'px'; se.style.transform = ''; }
    se.textContent = sb.text;
  }
  // звук
  if (playing) {
    const v = ACTIVE(L.voices, t);
    if (TL.curV && (!v || v.file !== TL.curV)) { TL.audio[TL.curV]?.pause(); TL.curV = null; }
    if (v) {
      const a = TL.audio[v.file], want = t - v.start;
      if (TL.curV !== v.file) { a.currentTime = want; a.play().catch(() => {}); TL.curV = v.file; }
      else if (Math.abs(a.currentTime - want) > 0.3) a.currentTime = want;
    }
    if (TL.music && L.music.loop > 0.5 && TL.music.readyState >= 1) {
      const want = t % L.music.loop, d = Math.abs(TL.music.currentTime - want);
      if (d > 0.3 && d < L.music.loop - 0.3) TL.music.currentTime = want;
      if (TL.music.paused) TL.music.play().catch(() => {});
    }
    for (const x of L.sfx) if (x.start > TL.prevT && x.start <= t) {
      const a = new Audio(x.auto && x.name === '_whoosh.wav' ? '/api/sfx/_whoosh.wav' : '/api/sfx/' + encodeURIComponent(x.name));
      a.volume = clamp(+x.vol || 0.5, 0, 1); a.play().catch(() => {});
    }
  }
  TL.prevT = t;
}

function tlSeek(t) {
  TL.t = clamp(t, 0, TL.L.total);
  if (TL.playing) { TL.t0 = TL.t; TL.p0 = performance.now(); Object.values(TL.audio).forEach(a => a.pause()); TL.curV = null; if (TL.music) TL.music.currentTime = TL.t % (TL.L.music.loop || 1e9); }
  TL.prevT = TL.t;
  tlPlayhead();
  tlFrame(TL.t, TL.playing);
}

function tlToggle() {
  if (TL.playing) return tlStop();
  const L = TL.L;
  if (TL.t >= L.total - 0.05) TL.t = 0;
  TL.playing = true; TL.t0 = TL.t; TL.p0 = performance.now(); TL.prevT = TL.t - 0.001;
  $('#tlPlay').textContent = '⏸';
  if (TL.music) { TL.music.currentTime = TL.t % (L.music.loop || 1e9); TL.music.play().catch(() => {}); }
  const tick = () => {
    if (!TL.playing) return;
    const t = TL.t0 + (performance.now() - TL.p0) / 1000;
    if (t >= L.total) { TL.t = L.total; tlPlayhead(); tlStop(); return; }
    TL.t = t;
    tlPlayhead();
    tlFrame(t, true);
    const sc = $('#tlsc'), x = t * TL.pps;
    if (sc && (x > sc.scrollLeft + sc.clientWidth - 40 || x < sc.scrollLeft)) sc.scrollLeft = x - 40;
  };
  TL.raf = setInterval(tick, 33);
}

const ZMIN = 4, ZMAX = 400;
function tlFitPps() { const w = $('#tlsc')?.clientWidth || 0; return clamp((w - 90) / Math.max(1, TL.L.total), ZMIN, ZMAX); }
function tlZoomUI() { $('#tlZoom').value = Math.log(TL.pps / ZMIN) / Math.log(ZMAX / ZMIN); }

function tlZoom(pps, anchorX, fit = false) {
  const sc = $('#tlsc');
  const ax = anchorX ?? clamp(TL.t * TL.pps - sc.scrollLeft, 0, sc.clientWidth);
  const tAt = (sc.scrollLeft + ax) / TL.pps;
  TL.pps = clamp(pps, ZMIN, ZMAX);
  TL.fit = fit;
  tlZoomUI();
  tlDraw();
  sc.scrollLeft = tAt * TL.pps - ax;
}

async function tlCommit(patch) {
  await save(patch);
  TL.L = await api(`/api/projects/${state.project.id}/timeline`);
  TL.t = Math.min(TL.t, TL.L.total);
  if (TL.fit) { TL.pps = tlFitPps(); tlZoomUI(); }
  tlAudio(); tlDraw(); tlInsp(); tlFrame(TL.t, false);
}

function tlSelect(k, id) {
  TL.sel = id == null ? null : {k, id};
  document.querySelectorAll('#tlin .tl-b.sel').forEach(b => b.classList.remove('sel'));
  if (TL.sel) document.querySelectorAll(`#tlin .tl-b[data-k="${k}"]`).forEach(b => b.dataset.id === id && b.classList.add('sel'));
  tlInsp();
}

function tlBind() {
  const sc = $('#tlsc'), tin = $('#tlin');
  $('#tlPlay').onclick = e => { e.currentTarget.blur(); tlToggle(); };
  $('#tlStart').onclick = e => { e.currentTarget.blur(); tlSeek(0); sc.scrollLeft = 0; };
  $('#tlZoom').oninput = e => tlZoom(ZMIN * (ZMAX / ZMIN) ** +e.target.value);
  $('#tlFit').onclick = () => { tlZoom(tlFitPps(), 0, true); sc.scrollLeft = 0; };
  $('#tlRender').onclick = () => { tlStop(); runSteps(['render']); };
  $('#tlAddText').onclick = guard(async () => {
    const id = Math.random().toString(16).slice(2, 12);
    const texts = [...(state.project.texts || []), {id, text: 'Новый текст', start: r2(TL.t), dur: 2.5, pos: 'top'}];
    TL.sel = {k: 'text', id};
    await tlCommit({texts});
  });
  $('#tlAddSfx').onclick = guard(sfxModal);
  sc.addEventListener('wheel', e => {
    if (e.ctrlKey) { e.preventDefault(); const r = sc.getBoundingClientRect(); tlZoom(TL.pps * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX - r.left); return; }
    if (Math.abs(e.deltaY) > Math.abs(e.deltaX) && sc.scrollWidth > sc.clientWidth) { e.preventDefault(); sc.scrollLeft += e.deltaY; }
  }, {passive: false});
  let drag = null;
  const tAt = ev => clamp((ev.clientX - tin.getBoundingClientRect().left) / TL.pps, 0, TL.L.total);
  tin.onpointerdown = e => {
    if (e.button !== 0) return;
    const b = e.target.closest('.tl-b');
    if (!b) { drag = {mode: 'scrub'}; tlSeek(tAt(e)); }
    else {
      const k = b.dataset.k, resize = e.target.classList.contains('h-r');
      const movable = ['plate', 'text', 'sfx'].includes(k) && !b.dataset.auto;
      drag = {mode: resize ? 'resize' : movable ? 'move' : 'click', b, k, id: b.dataset.id, x0: e.clientX, left: b.offsetLeft, width: b.offsetWidth, moved: false, t: tAt(e)};
    }
    tin.setPointerCapture(e.pointerId);
    e.preventDefault();
  };
  tin.onpointermove = e => {
    if (!drag) return;
    if (drag.mode === 'scrub') return tlSeek(tAt(e));
    const dx = e.clientX - drag.x0;
    if (Math.abs(dx) > 3) drag.moved = true;
    if (!drag.moved) return;
    if (drag.mode === 'resize') { drag.b.style.width = Math.max(0.3 * TL.pps, drag.width + dx) + 'px'; drag.b.querySelector('span').textContent = (Math.max(0.3 * TL.pps, drag.width + dx) / TL.pps).toFixed(2) + ' с'; }
    if (drag.mode === 'move') drag.b.style.left = Math.max(0, drag.left + dx) + 'px';
  };
  const up = guard(async e => {
    const d = drag; drag = null;
    if (!d || d.mode === 'scrub') return;
    if (!d.moved || d.mode === 'click') { tlSelect(d.k, d.id); tlSeek(d.t); return; }
    const ds = (e.clientX - d.x0) / TL.pps, p = state.project;
    TL.sel = {k: d.k, id: d.id};
    if (d.k === 'shot') {
      const [, s] = findShot(d.id); s.dur = r2(Math.max(0.3, d.width / TL.pps + ds)); s.locked = true;
      return tlCommit({scenes: p.scenes});
    }
    if (d.k === 'plate') {
      const s = findScene(d.id);
      if (d.mode === 'move') s.plate_at = r2(clamp((+s.plate_at || 0) + ds, 0, 3600));
      else s.plate_dur = r2(Math.max(0.3, (+s.plate_dur || 3) + ds));
      return tlCommit({scenes: p.scenes});
    }
    if (d.k === 'text') {
      const t = (p.texts || []).find(x => x.id === d.id);
      if (d.mode === 'move') t.start = r2(clamp(+t.start + ds, 0, TL.L.total - 0.1));
      else t.dur = r2(Math.max(0.3, (+t.dur || 2) + ds));
      return tlCommit({texts: p.texts});
    }
    if (d.k === 'sfx') {
      const s = (p.sfx_items || []).find(x => x.id === d.id);
      s.start = r2(clamp(+s.start + ds, 0, TL.L.total - 0.05));
      return tlCommit({sfx_items: p.sfx_items});
    }
  });
  tin.onpointerup = up;
  tin.onpointercancel = () => { drag = null; tlDraw(); };
}

document.addEventListener('keydown', e => {
  if (state.tab !== 'timeline' || !$('#tlin') || document.querySelector('.modal-bg')) return;
  const tag = e.target.tagName;
  if (/INPUT|TEXTAREA|SELECT/.test(tag) || e.target.isContentEditable) return;
  if (e.code === 'Space') { e.preventDefault(); if (tag === 'BUTTON') e.target.blur(); tlToggle(); }
  else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    e.preventDefault();
    const st = e.shiftKey ? 1 : 1 / (state.project.style.fps || 30);
    tlSeek(TL.t + (e.key === 'ArrowLeft' ? -st : st));
  } else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); guard(tlDelete)(); }
  else if (e.key === 'Escape') tlSelect(null, null);
});

async function tlDelete() {
  const sel = TL.sel, p = state.project; if (!sel) return;
  if (sel.k === 'text') { TL.sel = null; return tlCommit({texts: (p.texts || []).filter(x => x.id !== sel.id)}); }
  if (sel.k === 'sfx' && !sel.id.startsWith('auto')) { TL.sel = null; return tlCommit({sfx_items: (p.sfx_items || []).filter(x => x.id !== sel.id)}); }
  if (sel.k === 'shot') {
    const [sc] = findShot(sel.id);
    if (sc.shots.filter(s => !s.disabled && s.media).length <= 1 && !confirm('Это последний кадр сцены — сцена пропадёт из ролика. Удалить?')) return;
    sc.shots = sc.shots.filter(s => s.id !== sel.id); TL.sel = null;
    return tlCommit({scenes: p.scenes});
  }
}

function tlInsp() {
  const el = $('#insp'); if (!el) return;
  const L = TL.L, p = state.project, sel = TL.sel;
  const field = (lab, inner) => `<label class="f">${lab}${inner}</label>`;
  if (!sel) {
    el.innerHTML = `<h2>Ролик</h2>
      <div class="tl-kv"><span>Длина</span><b>${fmtT(L.total)}</b><span>Сцен</span><b>${L.scenes.length}</b><span>Кадров</span><b>${L.shots.length}</b>
      <span>Субтитров</span><b>${L.subs.length}</b><span>Звуков</span><b>${L.sfx.length}</b><span>Музыка</span><b>${L.music ? esc(L.music.name) : '—'}</b></div>
      <p class="sub" style="margin:12px 0 0">Кликни по любому блоку на дорожке, чтобы его настроить.</p>`;
    return;
  }
  if (sel.k === 'shot') {
    const [sc, s] = findShot(sel.id); if (!s) return tlSelect(null, null);
    const n = p.scenes.indexOf(sc) + 1, k = sc.shots.indexOf(s);
    el.innerHTML = `<h2>Кадр · сцена ${n}</h2>
      <div class="tl-th" style="background-image:url('${s.media.thumb ? pfile(s.media.thumb) : ''}')"></div>
      <p class="sub" style="margin:6px 0 10px">${s.media.type === 'video' ? '🎥 видео' : '🖼 фото'} · ${esc(s.media.source)}${s.media.credit ? ` · <a href="${esc(s.media.credit)}" target="_blank">источник</a>` : ''}</p>
      <div class="grid g2">
        ${field('Длина, с', `<input type="number" step="0.1" min="0.3" id="iDur" value="${s.dur}">`)}
        ${s.media.type === 'video' ? field('Начало в исходнике, с', `<input type="number" step="0.1" min="0" id="iTrim" value="${s.trim_in || 0}">`) : '<div></div>'}
      </div>
      <label class="chk" style="margin:10px 0"><input type="checkbox" id="iLock" ${s.locked ? 'checked' : ''}> фикс. длина (не подгонять под голос)</label>
      <div class="row" style="gap:6px">
        <button class="btn s" id="iL" ${k ? '' : 'disabled'}>◀ раньше</button><button class="btn s" id="iR" ${k < sc.shots.length - 1 ? '' : 'disabled'}>позже ▶</button>
        <button class="btn s" id="iRep">⇄ Заменить</button><button class="btn s danger" id="iDel">✕ Удалить</button>
      </div>`;
    const commit = () => tlCommit({scenes: p.scenes});
    $('#iDur').onchange = guard(async e => { s.dur = r2(Math.max(0.3, +e.target.value || 0.3)); s.locked = true; await commit(); });
    if ($('#iTrim')) $('#iTrim').onchange = guard(async e => { s.trim_in = r2(Math.max(0, +e.target.value || 0)); await commit(); });
    $('#iLock').onchange = guard(async e => { s.locked = e.target.checked; await commit(); });
    const mv = d => guard(async () => { [sc.shots[k], sc.shots[k + d]] = [sc.shots[k + d], sc.shots[k]]; await commit(); });
    $('#iL').onclick = mv(-1); $('#iR').onclick = mv(1);
    $('#iRep').onclick = () => searchModal(sc, s);
    $('#iDel').onclick = guard(tlDelete);
    return;
  }
  if (['plate', 'sub', 'voice'].includes(sel.k)) {
    const sid = sel.k === 'sub' ? sel.id.split(':')[0] : sel.id;
    const sc = findScene(sid); if (!sc) return tlSelect(null, null);
    const n = p.scenes.indexOf(sc) + 1;
    el.innerHTML = `<h2>Сцена ${n}</h2>
      ${field('Плашка (заголовок)', `<input type="text" id="iTitle" value="${esc(sc.title)}" placeholder="пусто — без плашки">`)}
      <div class="grid g2" style="margin-top:10px">
        ${field('Плашка появляется через, с', `<input type="number" step="0.1" min="0" id="iPAt" value="${+sc.plate_at || 0}">`)}
        ${field('Плашка держится, с', `<input type="number" step="0.1" min="0.3" id="iPDur" value="${+sc.plate_dur || 3}">`)}
      </div>
      <div style="margin-top:10px">${field('Текст озвучки = субтитры', `<textarea id="iText" style="min-height:90px">${esc(sc.text)}</textarea>`)}</div>
      <p class="sub" id="iWarn" style="margin:6px 0;display:none;color:var(--warn)">Текст поменялся — нажми «Переозвучить», иначе голос скажет старое.</p>
      <div class="row" style="gap:6px;margin-top:6px">
        ${sc.voice ? `<button class="btn s" id="iPlay">▶ голос (${sc.voice_dur || '?'} с)</button>` : ''}
        <button class="btn s" id="iVoice">🎙 Переозвучить</button>
      </div>
      ${p.style.plates ? '' : '<p class="sub" style="margin:8px 0 0">Плашки выключены во вкладке «Стиль».</p>'}
      ${p.style.subtitles ? '' : '<p class="sub" style="margin:8px 0 0">Субтитры выключены во вкладке «Стиль».</p>'}`;
    const commit = () => tlCommit({scenes: p.scenes});
    $('#iTitle').onchange = guard(async e => { sc.title = e.target.value.trim(); await commit(); });
    $('#iPAt').onchange = guard(async e => { sc.plate_at = r2(Math.max(0, +e.target.value || 0)); await commit(); });
    $('#iPDur').onchange = guard(async e => { sc.plate_dur = r2(Math.max(0.3, +e.target.value || 3)); await commit(); });
    $('#iText').oninput = () => { $('#iWarn').style.display = ''; };
    $('#iText').onchange = guard(async e => { sc.text = e.target.value.trim(); await commit(); $('#iWarn').style.display = ''; });
    if ($('#iPlay')) $('#iPlay').onclick = () => { tlStop(); toggleVoice($('#iPlay'), pfile(sc.voice)); };
    $('#iVoice').onclick = () => { tlStop(); runSteps(['voice']); };
    return;
  }
  if (sel.k === 'text') {
    const t = (p.texts || []).find(x => x.id === sel.id); if (!t) return tlSelect(null, null);
    el.innerHTML = `<h2>Текст на экране</h2>
      ${field('Текст', `<textarea id="iT" style="min-height:70px">${esc(t.text)}</textarea>`)}
      <div class="grid g2" style="margin-top:10px">
        ${field('Начало, с', `<input type="number" step="0.1" min="0" id="iS" value="${t.start}">`)}
        ${field('Длина, с', `<input type="number" step="0.1" min="0.3" id="iD" value="${t.dur}">`)}
      </div>
      <div style="margin-top:10px">${field('Где', `<select id="iP">${[['top', 'сверху'], ['center', 'по центру'], ['bottom', 'снизу']].map(([v, n]) => `<option value="${v}" ${t.pos === v ? 'selected' : ''}>${n}</option>`).join('')}</select>`)}</div>
      <div class="row" style="margin-top:10px"><button class="btn s" id="iHere">⤓ Поставить на курсор</button><div class="spacer"></div><button class="btn s danger" id="iDel">✕ Удалить</button></div>`;
    const commit = () => tlCommit({texts: p.texts});
    $('#iT').onchange = guard(async e => { t.text = e.target.value; await commit(); });
    $('#iS').onchange = guard(async e => { t.start = r2(clamp(+e.target.value || 0, 0, L.total - 0.1)); await commit(); });
    $('#iD').onchange = guard(async e => { t.dur = r2(Math.max(0.3, +e.target.value || 2)); await commit(); });
    $('#iP').onchange = guard(async e => { t.pos = e.target.value; await commit(); });
    $('#iHere').onclick = guard(async () => { t.start = r2(clamp(TL.t, 0, L.total - 0.1)); await commit(); });
    $('#iDel').onclick = guard(tlDelete);
    return;
  }
  if (sel.k === 'sfx') {
    if (sel.id.startsWith('auto')) {
      const x = L.sfx[+sel.id.slice(4)];
      el.innerHTML = `<h2>Авто-звук перехода</h2><p class="sub">${esc(x?.name || '')} на ${fmtT(x?.start || 0)}. Такие звуки ставятся сами между сценами.</p>
        <button class="btn s" id="iOff">Выключить авто-звуки</button>`;
      $('#iOff').onclick = guard(async () => { TL.sel = null; await tlCommit({style: {sfx: false}}); });
      return;
    }
    const s = (p.sfx_items || []).find(x => x.id === sel.id); if (!s) return tlSelect(null, null);
    el.innerHTML = `<h2>Звук · ${esc(s.name)}</h2>
      <div class="grid g2">${field('Начало, с', `<input type="number" step="0.1" min="0" id="iS" value="${s.start}">`)}
      ${field('Громкость', `<input type="range" min="0" max="1.5" step="0.05" id="iV" value="${s.vol ?? 0.8}">`)}</div>
      <div class="row" style="margin-top:10px"><button class="btn s" id="iPl">▶ послушать</button><button class="btn s" id="iHere">⤓ На курсор</button><div class="spacer"></div><button class="btn s danger" id="iDel">✕ Удалить</button></div>`;
    const commit = () => tlCommit({sfx_items: p.sfx_items});
    $('#iS').onchange = guard(async e => { s.start = r2(clamp(+e.target.value || 0, 0, L.total - 0.05)); await commit(); });
    $('#iV').onchange = guard(async e => { s.vol = +e.target.value; await commit(); });
    $('#iPl').onclick = () => { const a = new Audio('/api/sfx/' + encodeURIComponent(s.name)); a.volume = clamp(s.vol ?? 0.8, 0, 1); a.play(); };
    $('#iHere').onclick = guard(async () => { s.start = r2(clamp(TL.t, 0, L.total - 0.05)); await commit(); });
    $('#iDel').onclick = guard(tlDelete);
    return;
  }
  if (sel.k === 'music') {
    el.innerHTML = `<h2>Музыка</h2><p class="sub">${esc(L.music.name)} · ${L.music.mode === 'song' ? 'песня' : 'фон'} · трек ${fmtT(L.music.loop)}, повторяется по кругу до конца ролика.</p>
      ${L.music.mode === 'song' ? '' : field('Громкость фона под голосом', `<input type="range" min="0" max="1" step="0.02" id="iMV" value="${p.style.music_volume}">`)}
      <p class="sub" style="margin:10px 0 0">Сменить трек — вкладка «Музыка».</p>`;
    if ($('#iMV')) $('#iMV').onchange = guard(async e => { await tlCommit({style: {music_volume: +e.target.value}}); });
  }
}

function sfxModal() {
  const m = modal(`<h2>Добавить звук на ${fmtT(TL.t)}</h2>
    <p class="sub" style="margin:0 0 10px">Звуки лежат в папке <code>data/sfx</code>. Можно загрузить свой mp3/wav.</p>
    <div class="row"><label class="btn s">⬆ Загрузить звук<input type="file" id="sfxUp" accept="audio/*" hidden></label></div>
    <div id="sfxList" style="margin-top:12px;display:grid;gap:6px;max-height:50vh;overflow:auto"></div>`);
  const load = guard(async () => {
    const lib = await api('/api/sfx');
    $('#sfxList', m).innerHTML = lib.map((x, i) => `<div class="row" style="gap:8px;padding:6px 0;border-bottom:1px solid var(--line)">
      <button class="btn s" data-pl="${i}">▶</button><span style="flex:1">${esc(x.name)}</span><span class="pill gray">${(+x.dur || 0).toFixed(1)} с</span>
      <button class="btn p s" data-add="${i}">Добавить</button></div>`).join('') || '<p class="sub">Пока пусто — загрузи звук.</p>';
    $('#sfxList', m).onclick = guard(async e => {
      const pl = e.target.dataset.pl, ad = e.target.dataset.add;
      if (pl != null) new Audio('/api/sfx/' + encodeURIComponent(lib[pl].name)).play();
      if (ad != null) {
        const id = Math.random().toString(16).slice(2, 12);
        TL.sel = {k: 'sfx', id};
        m.remove();
        await tlCommit({sfx_items: [...(state.project.sfx_items || []), {id, name: lib[ad].name, start: r2(TL.t), vol: 0.8}]});
      }
    });
  });
  $('#sfxUp', m).onchange = guard(async e => {
    const fd = new FormData(); fd.append('file', e.target.files[0]);
    await api('/api/sfx', {method: 'POST', body: fd}); toast('Звук загружен'); load();
  });
  load();
}

document.addEventListener('focusin', async e => {
  if (e.target.id !== 'sfxSel' || e.target.dataset.filled) return;
  e.target.dataset.filled = 1;
  const cur = e.target.value, lib = await api('/api/sfx');
  e.target.innerHTML = [['', 'разные (случайно)'], ['_whoosh.wav', 'свуш (встроенный)'], ...lib.map(x => [x.name, x.name])]
    .map(([v, n]) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(n)}</option>`).join('');
});


// ------------------------------------------------ музыкальные ролики (lofi / jazz)
const fmtDur = s => { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60; return h ? `${h} ч ${m} мин` : `${m} мин ${s % 60} с`; };

async function pageMixes(id) {
  if (id) return pageMix(id);
  const list = await api('/api/mixes');
  view.innerHTML = `
  <div class="hero"><div><h1>🎷 Музыкальные ролики</h1>
    <p class="sub">Lofi / jazz / ambient на 1–4 часа: библиотека треков и картинок → каждый раз новая смесь.</p></div>
    <div class="spacer"></div><button class="btn big" id="new">＋ Новая библиотека</button></div>
  <div class="grid g3">${list.map(m => `<div class="card click" data-id="${m.id}"><b style="font-size:15px">${esc(m.name)}</b>
    <p class="sub" style="margin:8px 0 0">Треков: ${m.tracks} · картинок/клипов: ${m.visuals} · роликов: ${m.videos}</p></div>`).join('')
    || '<div class="card empty" style="grid-column:1/-1">Создай библиотеку — одну на канал или стиль.</div>'}</div>`;
  view.querySelectorAll('[data-id]').forEach(c => c.onclick = () => go('mixes/' + c.dataset.id));
  $('#new').onclick = guard(async () => {
    const name = prompt('Название (например: Jazz Cafe)'); if (!name) return;
    const m = await api('/api/mixes', {method: 'POST', body: {name, channel_id: curCh() || null}}); go('mixes/' + m.id);
  });
}

async function pageMix(id) {
  const m = await api('/api/mixes/' + id);
  const F = rel => `/api/mixes/${id}/file/${rel}`;
  const job = m.job;
  const fld = (k, label, type = 'number') => `<label class="f">${label}<input type="${type}" data-k="${k}" value="${esc(m[k])}"></label>`;
  view.innerHTML = `
  <div class="hero"><div><h1>🎷 ${esc(m.name)}</h1><p class="sub"><a href="#mixes">← все библиотеки</a></p></div>
    <div class="spacer"></div><button class="btn big" id="build" ${job.running ? 'disabled' : ''}>🎬 Собрать ролик (${m.target_min} мин)</button></div>
  ${job.running || job.error || job.log.length ? `<div class="card" style="margin-bottom:14px"><b>${job.running ? '⏳ Работаю…' : job.error ? '❌ Ошибка' : 'Последняя задача'}</b>
    <pre class="sub" style="white-space:pre-wrap;margin:8px 0 0">${esc(job.log.slice(-6).join('\n'))}${job.error ? '\n' + esc(job.error) : ''}</pre></div>` : ''}
  <div class="grid g2">
    <div class="card"><h3>Длина и смешивание</h3>
      <div class="row"><input type="text" id="refurl" placeholder="Ссылка на канал-референс (youtube.com/@…)" value="${esc(m.ref_url || '')}" style="flex:1">
        <button class="btn" id="ref">Взять длину</button></div>
      ${m.ref_durations ? `<p class="sub">Референс: ${m.ref_durations.length} роликов, от ${fmtDur(Math.min(...m.ref_durations))} до ${fmtDur(Math.max(...m.ref_durations))}</p>` : ''}
      <div class="grid g2">
        ${fld('target_min', 'Длина ролика, мин')}
        ${fld('block_min', 'Один трек играет, мин (через запятую; пусто = 1 раз)', 'text')}
        ${fld('crossfade', 'Переход между треками, с')}
        ${fld('visuals_per_video', 'Клипов в одном ролике')}
        ${fld('clip_len', 'Клип из картинки, с')}
        ${fld('max_clip', 'Видео-клип максимум, с')}
      </div>
      <label class="f">Формат<select data-k="format"><option ${m.format === '16:9' ? 'selected' : ''}>16:9</option><option ${m.format === '9:16' ? 'selected' : ''}>9:16</option></select></label>
    </div>
    <div class="card"><h3>Генерация</h3>
      <label class="f">Музыка (Suno, на английском)<textarea data-k="music_prompt" rows="2">${esc(m.music_prompt)}</textarea></label>
      <div class="row"><input type="number" id="nm" value="4" style="width:70px"><button class="btn" id="suno">🎵 Сгенерировать треки</button></div>
      <label class="f" style="margin-top:10px">Картинки (Nano Banana)<textarea data-k="image_prompt" rows="2">${esc(m.image_prompt)}</textarea></label>
      <div class="row"><input type="number" id="ni" value="6" style="width:70px"><button class="btn" id="img">🖼 Сгенерировать картинки</button></div>
      <label class="btn ghost" style="margin-top:12px">⬆ Загрузить свои треки / картинки / клипы<input type="file" id="up" multiple accept="audio/*,image/*,video/*" hidden></label>
    </div>
  </div>
  <div class="card" style="margin-top:14px"><h3>Готовые ролики</h3>${m.videos.map(v => `
    <div class="row" style="align-items:flex-start;gap:14px;margin-top:10px">
      <video src="${F(v.file)}" controls preload="none" style="width:320px;border-radius:8px;background:#000"></video>
      <div style="flex:1"><b>${esc(v.file.split('/').pop())}</b> · ${fmtDur(v.dur)} · ${esc(v.created)}
        <p class="sub">Треков ${v.tracks} (новых ${v.new_tracks}) · клипов ${v.visuals} (новых ${v.new_visuals})</p>
        <label class="f">Треклист для описания<textarea rows="4" readonly>${esc(v.tracklist)}</textarea></label>
        <button class="btn ghost" data-del="${v.id}">Удалить</button></div></div>`).join('') || '<p class="sub">Пока нет.</p>'}</div>
  <div class="card" style="margin-top:14px"><h3>Музыка · ${m.tracks.length}</h3>${m.tracks.map(t => `
    <div class="row" style="margin-top:6px"><span class="pill ${t.uses ? 'gray' : 'ok'}">${t.uses ? 'исп. ' + t.uses : 'новый'}</span>
      <span style="flex:1">${esc(t.name)} · ${fmtDur(t.dur)}${t.scan ? `<br><span class="sub">~${t.scan.bpm} BPM · ${esc(t.scan.key)} · ${esc(t.scan.bright)}</span>` : ''}
        ${t.note ? `<br><span class="sub">💬 ${esc(t.note)}</span>` : ''}</span><audio src="${F(t.file)}" controls preload="none" style="height:30px"></audio>
      <button class="btn s ${t.rate > 0 ? 'p' : 'ghost'}" data-rate="${t.id}" data-v="1" title="Нравится">👍</button>
      <button class="btn s ${t.rate < 0 ? 'danger' : 'ghost'}" data-rate="${t.id}" data-v="-1" title="Не то — напиши что">👎</button>
      <button class="btn ghost" data-del="${t.id}">✕</button></div>`).join('') || '<p class="sub">Сгенерируй или загрузи 6–15 похожих треков.</p>'}</div>
  <div class="card" style="margin-top:14px"><h3>Картинки и клипы · ${m.visuals.length}</h3>
    <div class="grid g4">${m.visuals.map(v => `<div style="position:relative">
      <img src="${F(v.thumb || v.file)}" style="width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:8px">
      <span class="pill ${v.uses ? 'gray' : 'ok'}" style="position:absolute;left:6px;top:6px">${v.kind === 'video' ? '🎞 ' : ''}${v.uses ? 'исп. ' + v.uses : 'новая'}</span>
      <button class="btn ghost" data-del="${v.id}" style="position:absolute;right:6px;top:6px;padding:2px 8px">✕</button></div>`).join('')
      || '<p class="sub">Одна картинка — статичный фон; 20–30 — перемешанные клипы с плавным наездом.</p>'}</div></div>`;

  view.querySelectorAll('[data-k]').forEach(el => el.onchange = guard(async () => {
    await api('/api/mixes/' + id, {method: 'PATCH', body: {[el.dataset.k]: el.type === 'number' ? +el.value : el.value}});
    if (el.dataset.k === 'target_min') pageMix(id);
  }));
  const run = (url, body) => guard(async () => { await api(url, {method: 'POST', body: body()}); pageMix(id); });
  $('#ref').onclick = run(`/api/mixes/${id}/ref`, () => ({url: $('#refurl').value}));
  $('#suno').onclick = run(`/api/mixes/${id}/suno`, () => ({n: +$('#nm').value, prompt: $('[data-k=music_prompt]').value}));
  $('#img').onclick = run(`/api/mixes/${id}/images`, () => ({n: +$('#ni').value, prompt: $('[data-k=image_prompt]').value}));
  $('#build').onclick = run(`/api/mixes/${id}/build`, () => ({}));
  $('#up').onchange = guard(async e => {
    const fd = new FormData(); [...e.target.files].forEach(f => fd.append('files', f));
    toast('Загружаю…'); await api(`/api/mixes/${id}/upload`, {method: 'POST', body: fd}); pageMix(id);
  });
  view.querySelectorAll('[data-rate]').forEach(b => b.onclick = guard(async () => {
    const v = +b.dataset.v;
    const note = prompt(v > 0 ? 'Что понравилось? (можно пусто)' : 'Что не так? (темп, инструменты, настроение…)', '');
    if (note === null) return;
    await api(`/api/mixes/${id}/rate/${b.dataset.rate}`, {method: 'POST', body: {rate: v, note}});
    toast('Записал в журнал — учту в следующих треках'); pageMix(id);
  }));
  view.querySelectorAll('[data-del]').forEach(b => b.onclick = guard(async () => {
    if (!confirm('Удалить?')) return;
    await api(`/api/mixes/${id}/item/${b.dataset.del}`, {method: 'DELETE'}); pageMix(id);
  }));
  if (job.running) state.poll = setInterval(async () => {
    const j = (await api('/api/mixes/' + id)).job;
    if (!j.running) { clearInterval(state.poll); pageMix(id); }
    else { const pre = view.querySelector('pre'); if (pre) pre.textContent = j.log.slice(-6).join('\n'); }
  }, 1500);
}


// ------------------------------------------------ разбор чужих роликов (через чат Claude)
const AN_ST = {preparing: ['⏳ готовлю', 'gray'], waiting: ['💬 ждёт Claude', 'gray'], done: ['✓ разобран', 'ok'], error: ['❌ ошибка', 'gray']};

async function pageAnalyses(id) {
  if (id) return pageAnalysis(id);
  const [list, chs] = await Promise.all([api('/api/analyses'), api('/api/channels')]);
  view.innerHTML = `
  <div class="hero"><div><h1>🔎 Разбор видео</h1>
    <p class="sub">Кидаешь ролик → софт готовит раскадровку, темп и текст → Claude в чате разбирает и, если попросишь, делает похожий.</p></div></div>
  <div class="card">
    <label class="f">Ссылка на ролик (YouTube и др.)<input type="text" id="url" placeholder="https://www.youtube.com/watch?v=…"></label>
    <label class="f">Что сделать (необязательно)<textarea id="note" rows="3" placeholder="Например: сделай такой же ролик про Adidas, картинки через ИИ.  Или: сделай музыкальный ролик в этом стиле."></textarea></label>
    <label class="f">Для какого канала делать<select id="ch"><option value="">— без канала —</option>
      ${chs.map(c => `<option value="${c.id}" ${c.id === curCh() ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>
    <div class="row" style="margin-top:4px"><button class="btn p" id="go">🔎 Отправить на разбор</button>
      <label class="btn ghost">или файл<input type="file" id="file" accept="video/*" hidden></label></div>
    <p class="sub" style="margin:10px 0 0;font-size:12px">💬 Разбор подхватит Claude сам — достаточно, чтобы чат Claude был открыт в папке софта.</p>
  </div>
  <div class="grid g3" style="margin-top:14px">${list.map(a => `<div class="card click" data-id="${a.id}">
    <b>${esc(a.title)}</b><p class="sub" style="margin:6px 0">${esc(a.note || 'только разбор')}</p>
    <span class="pill ${AN_ST[a.status]?.[1]}">${AN_ST[a.status]?.[0] || a.status}</span> <span class="sub">${esc(a.created)}</span></div>`).join('')}</div>`;
  view.querySelectorAll('[data-id]').forEach(c => c.onclick = () => go('analyses/' + c.dataset.id));
  const send = guard(async f => {
    const fd = new FormData(); fd.append('url', $('#url').value); fd.append('note', $('#note').value); fd.append('channel_id', $('#ch').value);
    if (f) fd.append('file', f);
    const a = await api('/api/analyses', {method: 'POST', body: fd}); go('analyses/' + a.id);
  });
  $('#go').onclick = () => send();
  $('#file').onchange = e => send(e.target.files[0]);
}

async function pageAnalysis(id) {
  const a = await api('/api/analyses/' + id);
  const F = rel => `/api/analyses/${id}/file/${rel}`;
  const e = a.edit || {};
  const st = AN_ST[a.status] || [a.status, 'gray'];
  view.innerHTML = `
  <div class="hero"><div><h1>${esc(a.title)}</h1><p class="sub"><a href="#analyses">← все разборы</a>
    ${a.url ? ` · <a href="${esc(a.url)}" target="_blank">открыть оригинал</a>` : ''}</p></div>
    <div class="spacer"></div><span class="pill ${st[1]}">${st[0]}</span>
    <button class="btn ghost" id="del">Удалить</button></div>
  ${a.note ? `<div class="card" style="margin-bottom:14px"><b>Задача:</b> ${esc(a.note)}</div>` : ''}
  ${a.status === 'error' ? `<div class="card row" style="margin-bottom:14px"><span style="flex:1">❌ ${esc(a.error)}</span><button class="btn p" id="retry">↻ Повторить</button></div>` : ''}
  ${a.status === 'preparing' ? `<div class="card" style="margin-bottom:14px"><pre class="sub" style="white-space:pre-wrap;margin:0">${esc((a.log || []).join('\n'))}</pre></div>` : ''}
  ${a.status === 'waiting' ? `<div class="card" style="margin-bottom:14px">💬 Всё готово — Claude подхватит разбор сам.</div>` : ''}
  ${a.report ? `<div class="card" style="margin-bottom:14px"><h3>Разбор Claude</h3><div style="white-space:pre-wrap;line-height:1.5">${esc(a.report)}</div></div>` : ''}
  <div class="grid g2">
    <div class="card"><h3>Цифры</h3><p class="sub" style="line-height:1.8">
      Канал: ${esc(a.channel || '—')} · просмотров ${a.views ?? '—'}<br>
      Длина: ${a.duration ? fmtDur(a.duration) : '—'} · ${a.format || ''}<br>
      Переходов: ${e.cuts ?? '—'} (склеек ${e.hard_cuts ?? '—'}, плавных ${e.dissolves ?? '—'}) за ${e.analyzed_sec ?? '—'} с · план ~${e.median_shot ?? '—'} с · ${e.cuts_per_min ?? '—'}/мин<br>
      Текст: ${a.transcript_words ?? '—'} слов (${esc(a.transcript_source || 'нет речи')})</p></div>
    <div class="card"><h3>Раскадровка</h3>${a.sheet ? `<img src="${F(a.sheet)}" style="width:100%;border-radius:8px">` : '<p class="sub">ещё нет</p>'}</div>
  </div>
  ${(a.sheets || []).length ? `<div class="card" style="margin-top:14px"><h3>Весь ролик · кадр каждые ${a.step} с</h3>${a.sheets.map(x => `
    <p class="sub" style="margin:10px 0 4px">${x.from}–${x.to}</p><img src="${F(x.file)}" style="width:100%;border-radius:8px">`).join('')}</div>` : ''}`;
  if ($('#retry')) $('#retry').onclick = guard(async () => { await api(`/api/analyses/${id}/retry`, {method: 'POST'}); pageAnalysis(id); });
  $('#del').onclick = guard(async () => { if (!confirm('Удалить разбор?')) return; await api('/api/analyses/' + id, {method: 'DELETE'}); go('analyses'); });
  if (a.status === 'preparing' || a.status === 'waiting') state.poll = setInterval(async () => {
    const b = await api('/api/analyses/' + id);
    if (b.status !== a.status || (b.log || []).length !== (a.log || []).length) { clearInterval(state.poll); pageAnalysis(id); }
  }, 2000);
}
