"use strict";
// Pajé (TUPIOS) — interface estilo ChatGPT (roda dentro do app GTK, servida localmente)

const params = new URLSearchParams(location.search);
const TOKEN = params.get("token") || "";
const MODE = params.get("mode") === "mini" ? "mini" : "full";
const PLACEHOLDER = MODE === "mini" ? "Pergunte ao Pajé…" : "Mensagem para o Pajé…";
const $ = (id) => document.getElementById(id);
$("input").placeholder = PLACEHOLDER;

// ------------------------------------------------------------------ ícones
const ICONS = {
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  menu: '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
  expand: '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  clip: '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  camera: '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/>',
  mic: '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  send: '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
  sliders: '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  brain: '<circle cx="12" cy="12" r="3"/><path d="M12 2a4 4 0 0 1 4 4 4 4 0 0 1 4 4 4 4 0 0 1-2 3.5A4 4 0 0 1 16 20a4 4 0 0 1-4 2 4 4 0 0 1-4-2 4 4 0 0 1-2-6.5A4 4 0 0 1 4 10a4 4 0 0 1 4-4 4 4 0 0 1 4-4z"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  alert: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
  ban: '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>',
  undo: '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
};
const icon = (n) => `<svg class="i" viewBox="0 0 24 24">${ICONS[n] || ""}</svg>`;
document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.icon); });

// --------------------------------------------------------------------- API
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Tupi-Token": TOKEN } };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error("O app do Pajé não respondeu.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `erro HTTP ${res.status}`);
  return data;
}

// ------------------------------------------------------------------- estado
const state = {
  status: null, models: [], convs: [], conv: null, pending: [], posting: false,
  recording: false, chosenModel: "", agent: null, policy: null, convSig: "",
};
// A IA está trabalhando nesta conversa? (o laço do agente roda no app; a página acompanha)
const busy = () => state.posting || !!(state.conv && state.conv.job && state.conv.job.running);

const TOOL_LABELS = {
  run_command: "Executar comando", read_file: "Ler arquivo", write_file: "Escrever arquivo",
  list_dir: "Listar pasta", system_info: "Informações do sistema", manage_service: "Controlar serviço",
  install_package: "Instalar programa", open_app: "Abrir programa", take_screenshot: "Print da tela",
  set_kernel_param: "Parâmetro do kernel", st_run: "Comando no ST", st_stage_change: "Ensaiar no ST",
  st_diff: "Diff do ensaio", st_promote: "Promover para o PD", st_discard: "Descartar ensaio",
  listar_agentes: "Ver a equipe", criar_subagente: "Criar subagente", delegar_tarefas: "Delegar trabalhos",
  aguardar_tarefas: "Aguardar subagentes", ver_tarefas: "Ver trabalhos", cancelar_tarefa: "Cancelar trabalho",
  use_computer: "Usar o computador (mouse e teclado)", open_file: "Abrir arquivo na tela",
};
const ACTION_LABELS = { allow: "Permitir", ask: "Perguntar", deny: "Negar" };
const catLabel = (c) => (state.policy && state.policy.labels && state.policy.labels[c]) || c;
const riskClass = (r) => ({ "baixo": "low", "médio": "mid", "alto": "high", "crítico": "crit" })[r] || "mid";

const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function toast(msg, ok = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (ok ? " ok" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), ok ? 2500 : 6000);
}

// --------------------------------------------------------------- markdown
function inline(s) {
  return s
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2">$2</a>');
}

function markdown(src) {
  const codes = [];
  src = String(src || "").replace(/```([\w+#.-]*)[^\S\n]*\n?([\s\S]*?)```/g, (m, lang, code) => {
    codes.push({ lang, code: code.replace(/\n$/, "") });
    return `\n\u0000${codes.length - 1}\u0000\n`;
  });
  const lines = esc(src).split("\n");
  const out = [];
  let para = [];
  let list = null;
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join("<br>"))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; } };
  for (const line of lines) {
    let m;
    if ((m = line.match(/^\u0000(\d+)\u0000$/))) {
      flushPara(); flushList();
      const c = codes[+m[1]];
      out.push(`<pre><div class="code-head"><span>${esc(c.lang || "código")}</span>` +
        `<button class="mini-copy" data-copy-code>${icon("copy")} Copiar</button></div><code>${esc(c.code)}</code></pre>`);
    } else if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      flushPara(); flushList();
      const n = Math.min(m[1].length + 1, 4);
      out.push(`<h${n}>${inline(m[2])}</h${n}>`);
    } else if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) {
      flushPara();
      if (!list || list.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; }
      list.items.push(m[1]);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flushPara();
      if (!list || list.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; }
      list.items.push(m[1]);
    } else if (!line.trim()) {
      flushPara(); flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara(); flushList();
  return out.join("");
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast("Copiado!", true);
}

// ------------------------------------------------------------- renderização
function renderBanner() {
  const b = $("banner");
  const st = state.status;
  if (st && !st.configured) {
    b.innerHTML = `<span>${icon("alert")} ${esc(st.hint || "A IA ainda não está configurada.")}</span>` +
      `<button id="bannerConfig">Configurar IA</button>`;
    b.classList.remove("hidden");
    $("bannerConfig").onclick = () => api("/api/window", { method: "POST", body: { action: "open_config" } });
  } else {
    b.classList.add("hidden");
  }
}

function renderModels() {
  const sel = $("modelSelect");
  const configured = state.status && state.status.model;
  const current = (state.conv && state.conv.model) || state.chosenModel || "";
  const opts = [`<option value="">${esc(configured ? `Padrão: ${configured}` : "Modelo padrão")}</option>`];
  const list = state.models.slice();
  if (current && !list.includes(current)) list.unshift(current);
  for (const m of list) opts.push(`<option value="${esc(m)}">${esc(m)}</option>`);
  sel.innerHTML = opts.join("");
  sel.value = current;
}

function renderConvs() {
  const nav = $("convList");
  nav.innerHTML = state.convs.map((c) =>
    `<div class="conv${state.conv && state.conv.id === c.id ? " active" : ""}" data-id="${c.id}">` +
    `<span class="t">${esc(c.title)}</span>` +
    `<button class="del" title="Apagar conversa" data-del="${c.id}">${icon("trash")}</button></div>`).join("");
}

function attachmentHtml(a) {
  if (a.kind === "image") {
    return `<img class="att-img" src="${esc(a.thumb)}?token=${encodeURIComponent(TOKEN)}" alt="${esc(a.name)}" data-zoom>`;
  }
  if (a.kind === "pdf") {
    const pages = a.pages ? `${a.pages} pág.` : "PDF";
    return `<div class="att-file">${icon("file")}<div><div>${esc(a.name)}</div><div class="meta">${pages}</div></div></div>`;
  }
  if (a.kind === "audio") {
    return `<div class="att-file">${icon("music")}<div><div>${esc(a.name)}</div>` +
      `<div class="meta">${a.seconds ? `${a.seconds}s · ` : ""}transcrito</div>` +
      `<details><summary>ver transcrição</summary>${esc(a.transcript || "")}</details></div></div>`;
  }
  return `<div class="att-file">${esc(a.name)}</div>`;
}

function emptyHtml() {
  const sug = [
    ["Explique um conceito", "Explique de forma simples o que é o NixOS e para que serve."],
    ["Analise minha tela", "__shot__"],
    ["Ajude com um texto", "Revise e melhore este texto para ficar mais claro: "],
    ["Resuma um PDF", "__attach__"],
  ];
  return `<div class="empty"><h1>Como posso ajudar?</h1>` +
    `<div>Converse, envie fotos, PDFs e áudios, ou tire um print da tela.</div>` +
    `<div class="suggestions">${sug.map(([t, p]) =>
      `<button class="suggestion" data-prompt="${esc(p)}"><b>${esc(t)}</b></button>`).join("")}</div></div>`;
}

function pendingForConv() {
  const id = state.conv && state.conv.id;
  return ((state.agent && state.agent.pending) || []).filter((p) => p.session === id);
}

// Cartão de uma ação da IA (ferramenta): o que pediu, o que a política decidiu, resultado, Desfazer
function actionHtml(m) {
  const a = m.action || {};
  const st = a.status || "running";
  const waiting = st === "running" && pendingForConv().some((p) => p.tool === a.tool);
  const [ic, stText] = ({
    ok: ["check", "feito"], error: ["alert", "falhou"], denied: ["ban", "negado"], cancelled: ["ban", "cancelado"],
    running: ["clock", waiting ? "aguardando sua aprovação" : "executando…"],
  })[st] || ["alert", st];
  const cats = (a.categories || []).map((c) => `<span class="cat">${esc(catLabel(c))}</span>`).join("");
  const risk = a.risk ? `<span class="risk r-${riskClass(a.risk)}">risco ${esc(a.risk)}</span>` : "";
  const by = a.approved_by ? `<span class="by">liberado por: ${esc(a.approved_by)}</span>` : "";
  const code = a.exit_code !== undefined && a.exit_code !== null ? `<span class="by">código de saída ${esc(a.exit_code)}</span>` : "";
  const reason = a.reason && st !== "ok" ? `<div class="reason">${esc(a.reason)}</div>` : "";
  const args = a.args && Object.keys(a.args).length
    ? `<details data-k="${esc(m.id)}-a"><summary>detalhes do pedido</summary><pre>${esc(JSON.stringify(a.args, null, 1))}</pre></details>` : "";
  const out = a.output ? `<details data-k="${esc(m.id)}-o"><summary>ver resultado</summary><pre>${esc(a.output)}</pre></details>` : "";
  const undo = a.undone ? `<span class="by">desfeito ✓</span>`
    : a.can_undo ? `<button class="undo" data-undo="${esc(a.action_id)}">${icon("undo")} Desfazer</button>` : "";
  const summary = a.summary && a.summary !== a.tool ? a.summary : "";
  const whereTip = { ST: "Rodou no ST: ambiente de ensaio, o seu sistema não mudou", PD: "Rodou no PD: o seu sistema real",
    "Pajé": "Orquestração: o Pajé coordenando subagentes (sem mexer no sistema)" }[a.onde] || "";
  const where = a.onde ? `<span class="where w-${esc(a.onde === "Pajé" ? "PJ" : a.onde)}" title="${whereTip}">${a.onde === "Pajé" ? "orquestração" : "no " + esc(a.onde)}</span>` : "";
  return `<div class="msg act-row"><div class="avatar"></div><div class="action st-${esc(st)}">` +
    `<div class="a-head">${icon(ic)}<b>${esc(TOOL_LABELS[a.tool] || a.tool || m.name || "ação")}</b>${where}` +
    `<span class="a-sum">${esc(summary)}</span><span class="a-st">${esc(stText)}</span></div>` +
    `<div class="a-meta">${risk}${cats}${by}${code}${undo}</div>${reason}${args}${out}</div></div>`;
}

function renderMessages(scroll = true) {
  const box = $("messages");
  const msgs = (state.conv && state.conv.messages) || [];
  if (!msgs.length && !busy()) {
    box.innerHTML = emptyHtml();
    return;
  }
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  const open = new Set([...box.querySelectorAll("details[open][data-k]")].map((d) => d.dataset.k));
  let html = "";
  for (const m of msgs) {
    if (m.role === "tool") { html += actionHtml(m); continue; }
    if (m.role === "user") {
      const atts = (m.attachments || []).map(attachmentHtml).join("");
      if (m.auto) {   // print tirado pela IA (take_screenshot), anexado para ela ver
        html += `<div class="msg act-row"><div class="avatar"></div><div class="auto-note">${icon("camera")} ${esc(m.text)}` +
          (atts ? `<div class="atts left">${atts}</div>` : "") + `</div></div>`;
        continue;
      }
      html += `<div class="msg user"><div style="max-width:82%">` +
        (atts ? `<div class="atts">${atts}</div>` : "") +
        (m.text ? `<div class="bubble" style="max-width:100%">${esc(m.text)}</div>` : "") + `</div></div>`;
    } else {
      if (!m.error && !(m.text || "").trim()) continue;   // só chamou ferramentas, sem texto
      html += `<div class="msg assistant${m.error ? " error" : ""}">` +
        `<div class="avatar"><span class="logo">PJ</span></div><div class="body">` +
        (m.error ? `<p>${icon("alert")} ${esc(m.text)}</p><div class="actions"><button class="retry" data-retry>Tentar de novo</button></div>`
          : markdown(m.text) + `<div class="actions"><button class="mini-copy" data-copy-msg="${esc(m.id)}">${icon("copy")} Copiar</button></div>`) +
        `</div></div>`;
    }
  }
  if (busy()) {
    const step = pendingForConv().length ? "aguardando sua aprovação…" : (state.conv && state.conv.job && state.conv.job.step) || "pensando…";
    html += `<div class="msg assistant"><div class="avatar"><span class="logo">PJ</span></div>` +
      `<div class="body"><div class="typing"><span></span><span></span><span></span></div>` +
      `<span class="step">${esc(step)}</span></div></div>`;
  }
  box.innerHTML = html;
  box.querySelectorAll("details[data-k]").forEach((d) => { if (open.has(d.dataset.k)) d.open = true; });
  if (scroll || nearBottom) box.scrollTop = box.scrollHeight;
}

function renderChips() {
  $("chips").innerHTML = state.pending.map((p, i) => {
    const thumb = p.meta && p.meta.kind === "image"
      ? `<img src="${esc(p.meta.thumb)}?token=${encodeURIComponent(TOKEN)}">`
      : `<span class="ic">${icon(p.kind === "pdf" ? "file" : p.kind === "audio" ? "music" : "file")}</span>`;
    const st = p.error ? esc(p.error) : p.meta ? (p.meta.kind === "audio" ? "transcrito" : "pronto")
      : p.kind === "audio" ? "transcrevendo…" : "processando…";
    return `<div class="chip${p.error ? " err" : ""}">${thumb}<div style="min-width:0">` +
      `<div class="n">${esc(p.name)}</div><div class="st">${st}</div></div>` +
      `<button title="Remover" data-rm="${i}">${icon("x")}</button></div>`;
  }).join("");
  updateSend();
}

function updateSend() {
  const btn = $("sendBtn");
  const working = busy();
  // Enquanto a IA trabalha, o botão vira "parar esta resposta" (o PARAR IA geral fica no topo)
  if (btn.dataset.mode !== (working ? "stop" : "send")) {
    btn.dataset.mode = working ? "stop" : "send";
    btn.innerHTML = icon(working ? "stop" : "send");
    btn.title = working ? "Parar esta resposta" : "Enviar (Enter)";
  }
  if (working) { btn.disabled = state.posting; return; }
  const uploading = state.pending.some((p) => !p.meta && !p.error);
  const has = $("input").value.trim() || state.pending.some((p) => p.meta);
  btn.disabled = uploading || !has;
}

// ------------------------------------------------------------------ ações
async function loadStatus() {
  try { state.status = await api("/api/status"); } catch (e) { state.status = { configured: false, hint: e.message }; }
  renderBanner();
  renderModels();
}

async function loadModels() {
  try { state.models = (await api("/api/models")).models || []; } catch (e) { state.models = []; }
  renderModels();
}

async function loadConvs() {
  state.convs = (await api("/api/conversations")).conversations;
  renderConvs();
}

async function openConv(id) {
  if (!id) { state.conv = null; renderAll(); return; }
  try {
    state.conv = await api(`/api/conversations/${id}`);
    state.convSig = JSON.stringify(state.conv);
    localStorage.setItem("tupi.current", id);
  } catch (e) {
    state.conv = null;
  }
  renderAll();
  updateSend();
  if (busy()) watchConv();
}

function renderAll() { renderConvs(); renderModels(); renderMessages(); }

function newChat() {
  state.conv = null;
  localStorage.removeItem("tupi.current");
  renderAll();
  focusInput();
}

async function ensureConv() {
  if (!state.conv) {
    state.conv = await api("/api/conversations", { method: "POST", body: { model: state.chosenModel || null } });
    localStorage.setItem("tupi.current", state.conv.id);
  }
  return state.conv;
}

async function send(retry = false) {
  if (busy()) return;
  const input = $("input");
  let text = input.value.trim();
  let attachments = state.pending.filter((p) => p.meta).map((p) => p.meta.id);
  if (retry) {
    // Reenvia a última mensagem do usuário (depois de um erro)
    const lastUser = [...state.conv.messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    text = lastUser.text;
    attachments = (lastUser.attachments || []).map((a) => a.id);
    state.conv.messages = state.conv.messages.filter((m) => !m.error && m.id !== lastUser.id);
  } else if (!text && !attachments.length) {
    return;
  }
  try {
    const conv = await ensureConv();
    const shown = state.pending.filter((p) => p.meta).map((p) => p.meta);
    if (!retry) {
      conv.messages.push({ id: "tmp", role: "user", text, attachments: shown });
      input.value = "";
      autosize();
      state.pending = [];
      renderChips();
    } else {
      const lastUser = { id: "tmp", role: "user", text, attachments: [] };
      conv.messages.push(lastUser);
    }
    state.posting = true;
    renderMessages();
    updateSend();
    state.conv = await api(`/api/conversations/${conv.id}/messages`, {
      method: "POST", body: { text, attachments, model: $("modelSelect").value || null, retry },
    });
  } catch (e) {
    toast(e.message);
    if (state.conv) await openConv(state.conv.id);
  } finally {
    state.posting = false;
    renderMessages();
    updateSend();
    loadConvs().catch(() => {});
    watchConv();
  }
}

// Acompanha a conversa enquanto o agente trabalha (passos, ações, aprovações, resposta final)
async function refreshConv() {
  if (!state.conv || state.conv.id === undefined) return;
  const id = state.conv.id;
  try {
    const c = await api(`/api/conversations/${id}`);
    if (!state.conv || state.conv.id !== id) return;
    const sig = JSON.stringify(c);
    const wasBusy = busy();
    state.conv = c;
    if (sig !== state.convSig) { state.convSig = sig; renderMessages(false); }
    updateSend();
    if (wasBusy && !busy()) loadConvs().catch(() => {});
  } catch (e) { /* conversa apagada */ }
}

function watchConv() {
  if (watchConv.timer) return;
  watchConv.timer = setInterval(async () => {
    if (!busy()) { clearInterval(watchConv.timer); watchConv.timer = null; return; }
    await refreshConv();
  }, 900);
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.onerror = () => reject(new Error("não foi possível ler o arquivo"));
    r.readAsDataURL(file);
  });
}

function kindOf(file) {
  const t = (file.type || "").toLowerCase();
  const n = file.name.toLowerCase();
  if (t.startsWith("image/")) return "image";
  if (t === "application/pdf" || n.endsWith(".pdf")) return "pdf";
  if (t.startsWith("audio/") || /\.(mp3|wav|ogg|oga|opus|m4a|aac|flac|webm|amr|3gp)$/.test(n)) return "audio";
  return null;
}

async function addFiles(files) {
  for (const file of files) {
    const kind = kindOf(file);
    if (!kind) { toast(`Tipo não suportado: ${file.name}. Use imagem, PDF ou áudio.`); continue; }
    if (file.size > 40 * 1024 * 1024) { toast(`${file.name} passa de 40 MB.`); continue; }
    const item = { name: file.name || "arquivo", kind, meta: null, error: null };
    state.pending.push(item);
    renderChips();
    try {
      const data = await readAsBase64(file);
      item.meta = await api("/api/attachments", { method: "POST", body: { name: item.name, mime: file.type, data } });
    } catch (e) {
      item.error = e.message;
    }
    renderChips();
  }
}

async function screenshot(mode) {
  const item = { name: mode === "region" ? "recorte da tela" : "print da tela", kind: "image", meta: null, error: null };
  state.pending.push(item);
  renderChips();
  try {
    item.meta = await api("/api/screenshot", { method: "POST", body: { mode } });
    item.name = item.meta.name;
    if (!$("input").value.trim()) $("input").value = mode === "region" ? "O que é isto na minha tela?" : "O que você vê na minha tela?";
    autosize();
  } catch (e) {
    state.pending.splice(state.pending.indexOf(item), 1);
    toast(e.message);
  }
  renderChips();
  focusInput();
}

async function toggleMic() {
  const btn = $("micBtn");
  if (!state.recording) {
    try {
      await api("/api/record/start", { method: "POST", body: {} });
      state.recording = true;
      btn.classList.add("rec");
      btn.innerHTML = icon("stop");
      btn.title = "Parar e transcrever";
      $("input").placeholder = "Gravando… clique no quadrado para parar";
    } catch (e) { toast(e.message); }
    return;
  }
  state.recording = false;
  btn.classList.remove("rec");
  btn.innerHTML = icon("mic");
  btn.title = "Falar (gravar voz e transcrever)";
  $("input").placeholder = "Transcrevendo o áudio…";
  btn.disabled = true;
  try {
    const r = await api("/api/record/stop", { method: "POST", body: {} });
    const input = $("input");
    input.value = (input.value ? input.value.trimEnd() + " " : "") + r.text;
    autosize();
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
    $("input").placeholder = PLACEHOLDER;
    focusInput();
    updateSend();
  }
}

function autosize() {
  const ta = $("input");
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
  updateSend();
}

function focusInput() { setTimeout(() => $("input").focus(), 30); }

// ---------------------------------------------------------------- eventos
$("input").addEventListener("input", autosize);
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!$("sendBtn").disabled && !busy()) send(); }
});
$("sendBtn").onclick = () => {
  if (busy()) {
    if (state.conv) api(`/api/conversations/${state.conv.id}/cancel`, { method: "POST", body: {} }).catch((e) => toast(e.message));
    toast("Parando esta resposta… (pedidos pendentes: negue no cartão de aprovação)", true);
    return;
  }
  send();
};
$("attachBtn").onclick = () => $("fileInput").click();
$("fileInput").onchange = (e) => { addFiles([...e.target.files]); e.target.value = ""; };
$("shotBtn").onclick = () => screenshot("full");
$("cropBtn").onclick = () => screenshot("region");
$("micBtn").onclick = toggleMic;
$("newChat").onclick = newChat;
$("miniNew").onclick = newChat;
$("toggleSide").onclick = () => $("app").classList.toggle("side-hidden");
$("openConfig").onclick = () => api("/api/window", { method: "POST", body: { action: "open_config" } });
$("miniExpand").onclick = () => api("/api/window", { method: "POST", body: { action: "open_full" } });
$("miniClose").onclick = () => api("/api/window", { method: "POST", body: { action: "close_mini" } });
$("modelSelect").onchange = async (e) => {
  state.chosenModel = e.target.value;
  if (state.conv) {
    state.conv.model = e.target.value || null;
    await api(`/api/conversations/${state.conv.id}/model`, { method: "POST", body: { model: e.target.value } }).catch(() => {});
  }
};

$("convList").addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del]");
  if (del) {
    e.stopPropagation();
    if (!confirm("Apagar esta conversa?")) return;
    await api(`/api/conversations/${del.dataset.del}`, { method: "DELETE" });
    if (state.conv && state.conv.id === del.dataset.del) newChat();
    loadConvs();
    return;
  }
  const c = e.target.closest(".conv");
  if (c) openConv(c.dataset.id);
});

$("chips").addEventListener("click", (e) => {
  const rm = e.target.closest("[data-rm]");
  if (rm) { state.pending.splice(+rm.dataset.rm, 1); renderChips(); }
});

$("messages").addEventListener("click", (e) => {
  const sug = e.target.closest("[data-prompt]");
  if (sug) {
    const p = sug.dataset.prompt;
    if (p === "__shot__") screenshot("full");
    else if (p === "__attach__") $("fileInput").click();
    else { $("input").value = p; autosize(); focusInput(); }
    return;
  }
  if (e.target.closest("[data-retry]")) { send(true); return; }
  const un = e.target.closest("[data-undo]");
  if (un) { undoAction(un.dataset.undo, un); return; }
  const cm = e.target.closest("[data-copy-msg]");
  if (cm) {
    const m = state.conv.messages.find((x) => x.id === cm.dataset.copyMsg);
    if (m) copyText(m.text);
    return;
  }
  const cc = e.target.closest("[data-copy-code]");
  if (cc) { copyText(cc.closest("pre").querySelector("code").textContent); return; }
  const z = e.target.closest("[data-zoom]");
  if (z) {
    const div = document.createElement("div");
    div.className = "zoom";
    div.innerHTML = `<img src="${z.src}">`;
    div.onclick = () => div.remove();
    document.body.appendChild(div);
  }
});

// arrastar e soltar / colar arquivos
let dragDepth = 0;
window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("dropOverlay").classList.remove("hidden"); });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("dropOverlay").classList.add("hidden"); } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("dropOverlay").classList.add("hidden");
  if (e.dataTransfer && e.dataTransfer.files.length) addFiles([...e.dataTransfer.files]);
});
window.addEventListener("paste", (e) => {
  const files = [...(e.clipboardData ? e.clipboardData.files : [])];
  if (files.length) { e.preventDefault(); addFiles(files); }
});
window.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("dialog").classList.contains("hidden")) { const c = $("dialogBox").querySelector("[data-close]"); if (c) c.click(); return; }
  if (!$("modal").classList.contains("hidden")) { $("modal").classList.add("hidden"); return; }
  if (MODE === "mini") api("/api/window", { method: "POST", body: { action: "close_mini" } });
});

// Ao voltar para a janela, sincroniza com o que foi feito na outra (mini <-> completa)
document.addEventListener("visibilitychange", async () => {
  if (document.hidden) return;
  await loadConvs().catch(() => {});
  const id = localStorage.getItem("tupi.current");
  if (id && (!state.conv || state.conv.id !== id || !busy())) openConv(id);
  loadStatus();
});

// =================================================================== AGENTE
const MODE_INFO = {
  restrito: { badge: "Restrito", icon: "lock", title: "Restrito (padrão)",
    text: "Só ler fica liberado. Alterar arquivos da sua pasta, usar a internet e executar programas: a IA pergunta antes. Todo o resto é negado." },
  perguntar: { badge: "Perguntar", icon: "help", title: "Perguntar",
    text: "A IA pergunta antes de qualquer ação, exceto as críticas (boot, discos, root, apagar arquivos do sistema), que continuam negadas." },
  livre: { badge: "MODO LIVRE", icon: "alert", title: "Livre",
    text: "Tudo liberado, inclusive as categorias críticas. Exige confirmação dupla e prazo. Comandos destrutivos ainda pedem confirmação; a IA nunca mexe na própria política nem nos seus segredos." },
};
const ALWAYS_RULES = [
  "A IA nunca altera a própria política de permissões nem o log de auditoria (negado em qualquer modo).",
  "Categorias críticas só podem ser liberadas por você, aqui, nunca pela IA.",
  "Segredos (~/.ssh, chave da API, senhas do navegador) ficam protegidos; só uma liberação explícita da categoria \"Ler segredos\" muda isso.",
  "Comandos destrutivos (rm -rf, dd, mkfs, chmod -R /…) pedem confirmação extra, mesmo no modo livre.",
  "Cada comando tem tempo e saída limitados e há um limite de ações por minuto. Tudo fica auditado.",
];

function livreScopeText(lv) {
  if (!lv) return "";
  if (lv.scope === "1h" && lv.until) return `até ${new Date(lv.until * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
  return lv.scope === "boot" ? "até reiniciar" : "sem prazo (sempre)";
}

async function pollAgent() {
  try { state.agent = await api("/api/agent/state"); } catch (e) { state.agent = null; }
  renderAgent();
}

function renderAgent() {
  const ag = state.agent;
  const on = !!(ag && ag.available);
  document.querySelectorAll(".agent-only").forEach((el) => el.classList.toggle("hidden", !on));
  const badge = $("modeBadge"), stop = $("stopBtn"), bar = $("agentBar");
  badge.classList.toggle("hidden", !on);
  stop.classList.toggle("hidden", !on);
  if (!on) { bar.classList.add("hidden"); renderApprovals(); return; }
  const info = MODE_INFO[ag.mode] || { badge: ag.mode || "?" };
  const loopOn = ag.loop && ["pensando", "observando"].includes(ag.loop.state);
  badge.innerHTML = icon(info.icon || "shield") + `<span class="txt"> ${esc(info.badge)}</span>` + (loopOn ? " " + icon("eye") : "");
  badge.title = `Modo de permissões: ${info.title || ag.mode}` + (loopOn ? ` — modo proativo ${ag.loop.state}` : "") + " (clique para mudar)";
  badge.className = "mode-badge mode-" + (ag.mode || "x");
  stop.innerHTML = ag.halted ? `▶<span class="txt"> Retomar IA</span>` : `■<span class="txt"> PARAR IA</span>`;
  stop.title = ag.halted ? "Retomar a IA" : "PARAR IA: cancela tudo o que a IA está fazendo agora";
  stop.className = "stop-btn" + (ag.halted ? " resume" : "");
  if (ag.halted) {
    bar.className = "agent-bar halted";
    bar.innerHTML = `<span>■ <b>IA PARADA.</b> Nada será executado até você retomar.</span><button data-bar="resume">Retomar IA</button>`;
  } else if (ag.mode === "livre") {
    bar.className = "agent-bar livre";
    bar.innerHTML = `<span>${icon("alert")} <b>MODO LIVRE ATIVO</b> (${esc(livreScopeText(ag.livre))}) — a IA age sem pedir permissão.</span>` +
      `<button data-bar="off">Desativar agora</button>`;
  } else {
    bar.className = "agent-bar hidden";
  }
  document.body.classList.toggle("livre", ag.mode === "livre" && !ag.halted);
  renderApprovals();
  // modo mudou por fora (terminal, outra janela, prazo do livre): atualiza a tela de permissões aberta
  const modeSig = `${ag.mode}|${ag.livre ? ag.livre.scope : ""}`;
  if (renderAgent.lastMode !== undefined && modeSig !== renderAgent.lastMode && settings.tab === "perms" &&
      !$("modal").classList.contains("hidden") && $("dialog").classList.contains("hidden")) renderTab();
  renderAgent.lastMode = modeSig;
  // subagentes trabalhando: contador no botão "Agentes e trabalhos"; quadro aberto se atualiza sozinho
  const pj = ag.paje || {}, ativos = (pj.rodando || 0) + (pj.na_fila || 0);
  $("pajeCount").textContent = ativos ? `${pj.rodando || 0} rodando${pj.na_fila ? ` · ${pj.na_fila} na fila` : ""}` : "";
  $("pajeCount").classList.toggle("hidden", !ativos);
  const pjSig = `${pj.rodando}|${pj.na_fila}`;
  if (pjSig !== renderAgent.lastPaje && ["trabalhos", "agentes"].includes(settings.tab) &&
      !$("modal").classList.contains("hidden") && $("dialog").classList.contains("hidden")) renderTab();
  renderAgent.lastPaje = pjSig;
  // mudança em ensaio no ST: faixa com atalho para o painel (diff, Promover, Descartar)
  const sb = $("stBar"), ens = ag.st && ag.st.ensaio;
  if (ens) {
    sb.className = "st-bar" + (ens.verificacoes_ok ? "" : " st-bad");
    sb.innerHTML = `<span>${icon("clock")} <b>Mudança em ensaio no ST:</b> ${esc(ens.descricao || ens.resumo || "")} — ` +
      `verificações ${ens.verificacoes_ok ? "OK" : "com problemas"}.</span><button data-stbar>Ver e decidir</button>`;
  } else {
    sb.className = "st-bar hidden";
  }
  // conversas iniciadas em outro lugar (chat rápido, terminal, sugestão): atualiza a lista
  const jobs = JSON.stringify(ag.jobs || []);
  if (jobs !== renderAgent.lastJobs) { renderAgent.lastJobs = jobs; loadConvs().catch(() => {}); }
  if (busy() && state.conv) {   // mostra "aguardando sua aprovação" no passo atual
    const sig = JSON.stringify(pendingForConv().map((p) => p.id));
    if (sig !== renderAgent.lastPending) { renderAgent.lastPending = sig; renderMessages(false); }
  }
}

// ------------------------------------------------------------ aprovações
function approvalHtml(p) {
  const labels = (p.labels && p.labels.length ? p.labels : (p.categories || []).map(catLabel));
  const args = p.args && Object.keys(p.args).length ? `<details><summary>ver o pedido completo</summary><pre>${esc(JSON.stringify(p.args, null, 1))}</pre></details>` : "";
  const reasons = (p.reasons || []).length ? `<ul class="ap-reasons">${p.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : "";
  const details = p.detalhes ? `<details open class="ap-details"><summary>${p.tool === "st_promote" ? "o que muda no seu sistema (diff do ensaio)" : "detalhes"}</summary><pre>${esc(p.detalhes)}</pre></details>` : "";
  const where = p.onde === "ST" ? "no ST (ensaio — o seu sistema não muda)" : "no PD (o seu sistema real)";
  const quem = p.agente ? `${esc(p.agente)} (subagente do Pajé) pede sua permissão` : "O Pajé pede sua permissão";
  return `<div class="ap-title">${icon("shield")} ${quem}</div>` +
    `<div class="ap-sum">${esc(p.summary || TOOL_LABELS[p.tool] || p.tool)}</div>` +
    `<div class="ap-grid"><span>Ação</span><b>${esc(TOOL_LABELS[p.tool] || p.tool)}</b>` +
    `<span>Onde</span><span>${esc(where)}</span>` +
    (p.target ? `<span>Alvo</span><code>${esc(p.target)}</code>` : "") +
    `<span>Risco</span><span><span class="risk r-${riskClass(p.risk)}">${esc(p.risk || "?")}</span></span>` +
    `<span>Categorias</span><span>${labels.map((l) => `<span class="cat">${esc(l)}</span>`).join("")}</span></div>` +
    reasons + details + args +
    (p.destructive ? `<label class="destr"><input type="checkbox" data-destr> ${icon("alert")} Comando DESTRUTIVO. Entendo o risco e quero executar mesmo assim.</label>` : "") +
    `<div class="ap-btns"><button class="ok" data-ap="once">Permitir uma vez</button>` +
    (p.destructive || (p.categories || []).includes("promote_change") ? ""
      : (p.categories || []).includes("control_desktop")
        ? `<button data-ap="session" title="Até você mexer no mouse, clicar em Devolver o controle ou ficar 10 minutos sem uso">Permitir nesta conversa (até você mexer no mouse)</button>`
        : `<button data-ap="session" title="Vale para as mesmas categorias nesta conversa">Permitir sempre nesta sessão</button>`) +
    `<button class="deny" data-ap="deny">Negar</button></div>` +
    `<div class="ap-count">Sem resposta, é negado em <b data-count></b> s</div>`;
}

function renderApprovals() {
  const box = $("approvals");
  const list = (state.agent && state.agent.available && state.agent.pending) || [];
  const ids = new Set(list.map((p) => p.id));
  box.querySelectorAll(".approval").forEach((el) => { if (!ids.has(el.dataset.id)) el.remove(); });
  for (const p of list) {
    if ([...box.children].some((el) => el.dataset.id === p.id)) continue;
    const el = document.createElement("div");
    el.className = "approval" + (p.destructive ? " destructive" : "");
    el.dataset.id = p.id;
    el.dataset.expires = p.expires;
    el.dataset.destructive = p.destructive ? "1" : "";
    el.innerHTML = approvalHtml(p);
    box.appendChild(el);
  }
  box.classList.toggle("hidden", !list.length);
  tickCountdowns();
}

function tickCountdowns() {
  document.querySelectorAll(".approval").forEach((el) => {
    const left = Math.max(0, Math.round(+el.dataset.expires - Date.now() / 1000));
    const b = el.querySelector("[data-count]");
    if (b) b.textContent = left;
  });
}
setInterval(tickCountdowns, 1000);

$("approvals").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-ap]");
  if (!b) return;
  const card = b.closest(".approval");
  const decision = b.dataset.ap;
  const destr = card.dataset.destructive === "1";
  const confirmed = !!(card.querySelector("[data-destr]") || {}).checked;
  if (decision !== "deny" && destr && !confirmed) { toast("Comando destrutivo: marque a caixa de confirmação antes de permitir."); return; }
  card.querySelectorAll("button").forEach((x) => { x.disabled = true; });
  try {
    const r = await api("/api/agent/decide", { method: "POST", body: { id: card.dataset.id, decision, confirm_destructive: confirmed } });
    if (r.ok === false) throw new Error(r.error || "não foi possível responder");
    card.remove();
    toast(decision === "deny" ? "Negado." : "Permitido.", true);
  } catch (err) {
    toast(err.message);
    card.querySelectorAll("button").forEach((x) => { x.disabled = false; });
  }
  pollAgent();
});

async function undoAction(actionId, btn) {
  if (!confirm("Desfazer esta ação da IA?")) return;
  if (btn) btn.disabled = true;
  try {
    const r = await api("/api/agent/undo", { method: "POST", body: { action_id: actionId, conversation: state.conv && state.conv.id } });
    if (!r.ok) throw new Error(r.error || (r.result && (r.result.error || r.result.output)) || "não foi possível desfazer");
    toast("Ação desfeita.", true);
  } catch (e) {
    toast(e.message);
    if (btn) btn.disabled = false;
  }
  if (state.conv) await refreshConv();
  if (!$("modal").classList.contains("hidden") && ["history", "st"].includes(settings.tab)) renderTab();
}

// --------------------------------------------------------- PARAR / retomar
async function stopAI() {
  try {
    await api("/api/agent/stop", { method: "POST", body: {} });
    toast("IA PARADA. Tudo o que ela fazia foi cancelado.", true);
  } catch (e) { toast(e.message); }
  pollAgent();
  if (state.conv) refreshConv();
}
async function resumeAI() {
  try { await api("/api/agent/resume", { method: "POST", body: {} }); toast("IA retomada.", true); } catch (e) { toast(e.message); }
  pollAgent();
}
$("stopBtn").onclick = () => (state.agent && state.agent.halted ? resumeAI() : stopAI());
$("modeBadge").onclick = () => openSettings("perms");
$("agentBar").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-bar]");
  if (!b) return;
  if (b.dataset.bar === "resume") return resumeAI();
  if (b.dataset.bar === "off") {
    const r = await api("/api/policy", { method: "POST", body: { mode: "padrao" } }).catch((err) => ({ ok: false, error: err.message }));
    toast(r.ok === false ? r.error : "Modo livre desativado.", r.ok !== false);
    pollAgent();
  }
});

// ------------------------------------------------------------ diálogos
function dialog(html, onReady) {
  return new Promise((resolve) => {
    const box = $("dialogBox");
    box.className = "dialog-box" + (/class="form"|<h4>Instruções<\/h4>/.test(html) ? " wide" : "");
    box.innerHTML = html;
    $("dialog").classList.remove("hidden");
    const close = (v) => { $("dialog").classList.add("hidden"); box.innerHTML = ""; resolve(v); };
    box.querySelectorAll("[data-close]").forEach((b) => { b.onclick = () => close(null); });
    if (onReady) onReady(box, close);
  });
}

// Pede para digitar uma frase exata (confirmações fortes: modo livre, categorias críticas)
function typedConfirm(title, bodyHtml, phrase, okLabel) {
  return dialog(`<h3>${title}</h3>${bodyHtml}<p>Para confirmar, digite <b class="phrase">${esc(phrase)}</b>:</p>` +
    `<input class="typed" autocomplete="off" spellcheck="false"><div class="dlg-btns">` +
    `<button data-close>Cancelar</button><button class="danger" data-ok disabled>${esc(okLabel)}</button></div>`, (box, close) => {
    const inp = box.querySelector(".typed"), ok = box.querySelector("[data-ok]");
    inp.oninput = () => { ok.disabled = inp.value.trim() !== phrase; };
    ok.onclick = () => close(phrase);
    setTimeout(() => inp.focus(), 30);
  });
}

async function activateLivre() {
  const scope = await dialog(`<h3 class="warn">${icon("alert")} Ativar o MODO LIVRE?</h3>` +
    `<p>No modo livre a IA pode <b>fazer qualquer coisa sem pedir sua permissão</b>: instalar programas, mudar a configuração ` +
    `do sistema, mexer no boot, nos discos e virar administrador. Um erro dela pode danificar o sistema ou apagar dados.</p>` +
    `<p>Continuam valendo: comandos destrutivos pedem confirmação, a IA não mexe na própria política, os segredos ficam ` +
    `protegidos, tudo é auditado e o botão <b>PARAR IA</b> cancela tudo na hora. A bolinha e a barra ficam vermelhas enquanto estiver ativo.</p>` +
    `<p><b>Por quanto tempo?</b></p><div class="scopes">` +
    `<label><input type="radio" name="sc" value="1h" checked> 1 hora</label>` +
    `<label><input type="radio" name="sc" value="boot"> Até reiniciar o computador</label>` +
    `<label><input type="radio" name="sc" value="sempre"> Sempre (até eu desativar)</label></div>` +
    `<div class="dlg-btns"><button data-close>Cancelar</button><button class="danger" data-next>Entendi os riscos, continuar</button></div>`,
  (box, close) => { box.querySelector("[data-next]").onclick = () => close(box.querySelector("input[name=sc]:checked").value); });
  if (!scope) return;
  const phrase = "ATIVAR MODO LIVRE";
  const ok = await typedConfirm("Segunda confirmação", `<p>Prazo escolhido: <b>${esc({ "1h": "1 hora", boot: "até reiniciar", sempre: "sempre" }[scope])}</b>.</p>`, phrase, "Ativar modo livre");
  if (!ok) return;
  await savePolicy({ mode: "livre", livre_scope: scope, confirm: phrase }, "Modo livre ATIVADO.");
}

async function savePolicy(body, okMsg) {
  try {
    const r = await api("/api/policy", { method: "POST", body });
    if (r.ok === false) throw new Error(r.error);
    if (r.policy) state.policy = r.policy;
    toast(okMsg || "Salvo.", true);
  } catch (e) { toast(e.message); }
  await pollAgent();
  if (settings.tab === "perms" && !$("modal").classList.contains("hidden")) renderTab();
}

// ------------------------------------------------- configurações (abas)
const settings = { tab: "perms" };

function openSettings(tab) {
  if (MODE === "mini") { api("/api/window", { method: "POST", body: { action: "open_settings" } }); return; }
  settings.tab = tab || settings.tab;
  $("modal").classList.remove("hidden");
  renderTab();
}
$("modalClose").onclick = () => $("modal").classList.add("hidden");
$("modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("modal").classList.add("hidden"); });
$("tabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-tab]");
  if (b) { settings.tab = b.dataset.tab; renderTab(); }
});
$("openPerms").onclick = () => openSettings("perms");
$("openMemory").onclick = () => openSettings("memory");
$("openAgents").onclick = () => openSettings("agentes");

async function renderTab() {
  document.querySelectorAll("#tabs [data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === settings.tab));
  const body = $("modalBody");
  body.onclick = null;
  body.innerHTML = `<div class="muted">Carregando…</div>`;
  try {
    await ({ perms: tabPerms, st: tabST, memory: tabMemory, loop: tabLoop, sugs: tabSugs, history: tabHistory,
      agentes: tabAgentes, trabalhos: tabTrabalhos, harness: tabHarness })[settings.tab](body);
  } catch (e) {
    body.innerHTML = `<div class="reason">${esc(e.message)}</div>`;
  }
}

async function tabPerms(body) {
  const pol = await api("/api/policy");
  if (pol.error) throw new Error(pol.error);
  state.policy = pol;
  const ov = (pol.runtime && pol.runtime.overrides) || {};
  const crit = new Set([...(pol.critical || []), ...(pol.explicit_only || [])]);
  const cards = pol.modes.map((m) => `<button class="mode-card${pol.mode === m ? " active" : ""} m-${m}" data-mode="${m}">` +
    `<b>${esc(MODE_INFO[m].title)}</b><span>${esc(MODE_INFO[m].text)}</span>` +
    (pol.mode === m ? `<em>ativo${m === "livre" ? " — " + esc(livreScopeText(pol.livre)) : ""}</em>` : "") + `</button>`).join("");
  const rows = pol.categories.map((c) => {
    const def = (pol.defaults[pol.mode] || {})[c];
    const cur = pol.table[c];
    const sel = ov[c] || "default";
    // aplicar no PD e usar o mouse/teclado SEMPRE perguntam: não existe "Permitir"
    const choices = (pol.never_auto || ["promote_change"]).includes(c) ? [["ask", "Perguntar (sempre)"], ["deny", "Negar"]]
      : [["allow", "Permitir"], ["ask", "Perguntar"], ["deny", "Negar"]];
    const opts = [["default", `Padrão do modo (${ACTION_LABELS[def] || def})`], ...choices]
      .map(([v, l]) => `<option value="${v}"${v === sel ? " selected" : ""}>${esc(l)}</option>`).join("");
    return `<tr><td>${esc(pol.labels[c] || c)}${crit.has(c) ? ` <span class="risk r-crit">${(pol.explicit_only || []).includes(c) ? "explícita" : "crítica"}</span>` : ""}</td>` +
      `<td><span class="risk r-${riskClass(pol.risk[c])}">${esc(pol.risk[c])}</span></td>` +
      `<td><span class="act act-${cur}">${esc(ACTION_LABELS[cur] || cur)}</span><div class="src">${esc(pol.sources[c] || "")}</div></td>` +
      `<td><select data-cat="${c}">${opts}</select></td></tr>`;
  }).join("");
  body.innerHTML = `<h3>Modo</h3><div class="mode-cards">${cards}</div>` +
    `<div class="muted small">Modo definido em: ${esc(pol.mode_source)}${(pol.notes || []).map((n) => " · " + esc(n)).join("")}</div>` +
    `<h3>Por categoria</h3><table class="cat-table"><thead><tr><th>O que a IA pode fazer</th><th>Risco</th><th>Agora</th><th>Ajuste</th></tr></thead><tbody>${rows}</tbody></table>` +
    `<div class="dlg-btns left"><button data-reset>Restaurar padrão</button></div>` +
    `<h3>Sempre valem</h3><ul class="rules">${ALWAYS_RULES.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` +
    `<div class="muted small">Também pelo terminal: <code>tupi-agent policy</code> · no NixOS: <code>tupi.agent.mode</code> e <code>tupi.agent.policyOverrides</code>.</div>`;
  body.querySelectorAll("[data-mode]").forEach((b) => {
    b.onclick = () => {
      const m = b.dataset.mode;
      if (m === pol.mode) return;
      if (m === "livre") activateLivre();
      else savePolicy({ mode: m }, `Modo ${MODE_INFO[m].title} ativado.`);
    };
  });
  body.querySelectorAll("select[data-cat]").forEach((s) => {
    s.onchange = async () => {
      const c = s.dataset.cat, v = s.value;
      const req = { overrides: { [c]: v } };
      if (crit.has(c) && (v === "allow" || v === "ask")) {
        const phrase = "LIBERAR CATEGORIAS CRÍTICAS";
        const ok = await typedConfirm(`Liberar "${esc(pol.labels[c])}"?`,
          `<p class="warn">Esta é uma categoria ${(pol.explicit_only || []).includes(c) ? "protegida (segredos)" : "crítica"}. ` +
          `Liberá-la permite à IA ${v === "ask" ? "pedir para" : ""} fazer coisas que podem danificar o sistema ou expor seus dados.</p>`, phrase, "Liberar");
        if (!ok) { s.value = ov[c] || "default"; return; }
        req.confirm = phrase;
      }
      savePolicy(req, "Permissão atualizada.");
    };
  });
  body.querySelector("[data-reset]").onclick = async () => {
    if (!confirm("Voltar todas as permissões ao padrão do sistema (modo do NixOS, sem ajustes)?")) return;
    const r = await api("/api/policy/reset", { method: "POST", body: {} }).catch((e) => ({ ok: false, error: e.message }));
    toast(r.ok === false ? r.error : "Permissões restauradas ao padrão.", r.ok !== false);
    await pollAgent();
    renderTab();
  };
}

// ======================================== Pajé: agentes, trabalhos, harness
const ST_LABEL = { na_fila: "na fila", rodando: "rodando", concluida: "concluído", falhou: "falhou", cancelada: "cancelado" };
const ORIGEM = { paje: "pelo Pajé", usuario: "por você", rotina: "rotina" };
const ago = (t) => { if (!t) return ""; const s = Math.round(Date.now() / 1000 - t); return s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`; };
let HARNESS_CACHE = null;
async function harness() { if (!HARNESS_CACHE) HARNESS_CACHE = await api("/api/paje/harness"); return HARNESS_CACHE; }

function agentCard(a) {
  const pct = Math.min(100, Math.round(100 * (a.tokens_hoje || 0) / (a.orcamento_tokens_dia || 1)));
  return `<div class="agent-card ${a.estado === "trabalhando" ? "busy" : ""}">` +
    `<div class="ac-head"><span class="ac-avatar">${esc((a.nome || "?").slice(0, 2).toUpperCase())}</span>` +
    `<div><b>${esc(a.nome)}</b><div class="small muted">${esc(a.papel || "")}</div></div>` +
    `<span class="ac-state s-${esc(a.estado.replace(" ", "-"))}">${esc(a.estado)}</span></div>` +
    `<div class="a-meta"><span class="cat">${a.conexao && a.conexao.startsWith("ag-") ? "provedor próprio" : esc(a.conexao)}${a.modelo ? " / " + esc(a.modelo) : ""}</span>` +
    (a.criado_por === "paje" ? `<span class="where w-PJ">criado pelo Pajé</span>` : "") +
    (a.rotina_minutos ? `<span class="cat">rotina a cada ${a.rotina_minutos} min</span>` : "") + `</div>` +
    `<div class="small muted">ferramentas: ${(a.ferramentas || []).map(esc).join(", ") || "nenhuma (só conversa)"}</div>` +
    `<div class="budget" title="tokens hoje"><div style="width:${pct}%"></div></div>` +
    `<div class="small muted">${a.tokens_hoje || 0} de ${a.orcamento_tokens_dia} tokens hoje · ${a.trabalhos_ativos} trabalho(s) ativo(s)</div>` +
    `<div class="dlg-btns left"><button data-edit="${esc(a.id)}">Editar alma</button><button data-task="${esc(a.id)}">Dar trabalho</button>` +
    `<button class="danger" data-del="${esc(a.id)}">Apagar</button></div></div>`;
}

async function tabAgentes(body) {
  const r = await api("/api/paje/agentes");
  const ags = r.agentes || [], tpls = (r.modelos && r.modelos.modelos) || [];
  body.innerHTML = `<h3>Equipe do Pajé</h3>` +
    `<p class="muted">O Pajé conversa com você e coordena os subagentes: divide trabalhos, roda em paralelo e junta os ` +
    `resultados. Cada subagente tem a própria alma (.soul.md), ferramentas e modelo de IA — e passa pelas mesmas ` +
    `permissões do sistema. O Pajé também pode criar subagentes quando achar necessário.</p>` +
    `<div class="org"><div class="agent-card paje-card"><div class="ac-head"><span class="ac-avatar paje">PJ</span>` +
    `<div><b>Pajé</b><div class="small muted">IA principal — coordena a equipe</div></div></div>` +
    `<div class="dlg-btns left"><button data-harness>Alma e harness do Pajé</button></div></div>` +
    `<div class="org-line"></div><div class="org-grid">${ags.map(agentCard).join("") ||
      `<div class="muted">Nenhum subagente ainda. Crie um abaixo, ou peça ao Pajé um trabalho grande — ele cria se precisar.</div>`}</div></div>` +
    `<div class="dlg-btns left"><button class="ok" data-new>Novo subagente</button>` +
    tpls.map((t) => `<button data-tpl="${esc(t.chave)}" title="${esc(t.papel)}">+ ${esc(t.nome)}</button>`).join("") + `</div>` +
    `<p class="muted small">Até ${r.max_subagentes} subagentes. As almas ficam em <code>~/.local/share/tupios-ia/paje/agentes/</code> ` +
    `(edite aqui ou em qualquer editor). Terminal: <code>paje agentes</code>.</p>`;
  body.onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.harness !== undefined) { settings.tab = "harness"; renderTab(); return; }
    if (b.dataset.new !== undefined) return agentDialog(null);
    if (b.dataset.edit) return agentDialog(b.dataset.edit);
    if (b.dataset.task) return taskDialog(b.dataset.task, ags);
    if (b.dataset.tpl) {
      const r2 = await api("/api/paje/agentes", { method: "POST", body: { modelo_base: b.dataset.tpl } }).catch((err) => ({ error: err.message }));
      toast(r2.error || `Subagente ${r2.agente.nome} criado.`, !r2.error);
      return renderTab();
    }
    if (b.dataset.del) {
      if (!confirm("Apagar este subagente? Os trabalhos dele em andamento são cancelados.")) return;
      const r2 = await api(`/api/paje/agentes/${b.dataset.del}/apagar`, { method: "POST", body: {} }).catch((err) => ({ error: err.message }));
      toast(r2.error || "Subagente apagado.", !r2.error);
      return renderTab();
    }
  };
}

async function agentDialog(id) {
  const h = await harness();
  const a = id ? (await api(`/api/paje/agentes/${id}`)) : null;
  const ag = a ? a.agente : { nome: "", papel: "", conexao: "principal", modelo: "", ferramentas: ["list_dir", "read_file", "system_info"],
    orcamento_tokens_dia: h.limites.orcamento_padrao, rotina_minutos: 0, rotina: "",
    alma: "# Quem eu sou\nSou ... um subagente do Pajé.\n\n# Como trabalho\n- ...\n\n# O que não faço\n- ...\n" };
  const tools = (h.ferramentas_sistema || []).filter((t) => (h.ferramentas_de_subagente || []).includes(t.nome));
  const conns = (h.conexoes || []).map((c) => c.nome).filter((c) => !c.startsWith("ag-"));
  const own = a ? a.provedor : null;       // provedor próprio salvo (sem a chave)
  const pres = h.provedores || [];
  dialog(`<h3>${a ? "Editar " + esc(ag.nome) : "Novo subagente"}</h3>` +
    `<div class="form"><label>Nome <input id="fNome" value="${esc(ag.nome)}" ${a ? "disabled" : ""} maxlength="40"></label>` +
    `<label>Papel (uma frase) <input id="fPapel" value="${esc(ag.papel)}" maxlength="300"></label>` +
    `<fieldset><legend>Modelo de IA deste agente</legend>` +
    `<div class="seg"><label><input type="radio" name="fOrig" value="conexao" ${own ? "" : "checked"}> Usar uma conexão do Pajé</label>` +
    `<label><input type="radio" name="fOrig" value="proprio" ${own ? "checked" : ""}> Provedor próprio (URL e chave só dele)</label></div>` +
    `<div id="fConnBox" class="row2 ${own ? "hidden" : ""}"><label>Conexão <select id="fConn">${conns.map((c) => `<option ${c === ag.conexao ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>` +
    `<label>Modelo (vazio = padrão) <input id="fModelo" value="${own ? "" : esc(ag.modelo)}" list="fModelos"><datalist id="fModelos"></datalist></label>` +
    `<button data-listar>Listar modelos</button></div>` +
    `<div id="fOwnBox" class="${own ? "" : "hidden"}"><div class="row2"><label>Provedor <select id="pPre">${pres.map((p) =>
      `<option value="${esc(p.id)}" ${own ? (own.preset === p.id ? "selected" : "") : (p.id === "custom" ? "selected" : "")}>${esc(p.nome)}</option>`).join("")}</select></label>` +
    `<label>URL base da API <input id="pUrl" placeholder="https://… (vazio = padrão do provedor)" value="${esc(own ? own.base_url : "")}"></label></div>` +
    `<div class="row2"><label id="pKeyL">Chave da API <input id="pKey" type="password" autocomplete="off" placeholder="${own && own.tem_chave ? "salva — deixe em branco para manter" : "cole a chave aqui"}"></label>` +
    `<button data-pmod>Buscar modelos</button>` +
    `<label>Modelo <select id="pMod">${own && own.model ? `<option selected>${esc(own.model)}</option>` : `<option value="">(busque os modelos)</option>`}</select></label></div>` +
    `<p class="muted small">A chave fica em /etc/tupi/ai-config.json, protegida: nem o Pajé nem os subagentes conseguem lê-la. Na alma só aparece o nome da conexão.</p></div></fieldset>` +
    `<fieldset><legend>Ferramentas (mínimo necessário)</legend>${tools.map((t) =>
      `<label class="tool-opt" title="${esc(t.descricao)}"><input type="checkbox" value="${esc(t.nome)}" ${(ag.ferramentas || []).includes(t.nome) ? "checked" : ""}> ${esc(TOOL_LABELS[t.nome] || t.nome)}</label>`).join("")}</fieldset>` +
    `<div class="row2"><label>Orçamento de tokens/dia <input id="fOrc" type="number" min="1000" value="${ag.orcamento_tokens_dia}"></label>` +
    `<label>Rotina a cada (min, 0 = sem) <input id="fRotMin" type="number" min="0" value="${ag.rotina_minutos}"></label></div>` +
    `<label>Instrução da rotina <input id="fRot" value="${esc(ag.rotina)}" placeholder="ex.: verifique o espaço em disco e avise se passar de 90%"></label>` +
    `<label>Alma (.soul.md — comportamento deste agente, em Markdown) <textarea id="fAlma" rows="10">${esc(ag.alma)}</textarea></label>` +
    `<p class="muted small">A alma descreve personalidade e método. As permissões continuam no Permission Manager: uma alma nunca libera nada.</p></div>` +
    `<div class="dlg-btns"><button data-close>Cancelar</button><button class="ok" data-save>Salvar</button></div>`, (box, close) => {
    const q = (s) => box.querySelector(s);
    const isOwn = () => (box.querySelector("input[name=fOrig]:checked") || {}).value === "proprio";
    const syncOwn = () => {
      q("#fConnBox").classList.toggle("hidden", isOwn());
      q("#fOwnBox").classList.toggle("hidden", !isOwn());
      const p = pres.find((x) => x.id === q("#pPre").value) || {};
      q("#pKeyL").classList.toggle("hidden", !!p.semchave);
    };
    box.querySelectorAll("input[name=fOrig]").forEach((r) => { r.onchange = syncOwn; });
    q("#pPre").onchange = syncOwn;
    syncOwn();
    q("[data-pmod]").onclick = async () => {
      const pre = q("#pPre").value, url = q("#pUrl").value.trim(), key = q("#pKey").value.trim();
      let models = [], rec = "";
      // chave em branco + mesmo provedor/URL: usa a chave salva (o roteador busca, a página nunca vê a chave)
      if (!key && own && own.tem_chave && own.preset === pre && (own.base_url || "") === url) {
        const r = await api(`/api/paje/conexoes/${encodeURIComponent(own.conexao)}/modelos`).catch((err) => ({ error: err.message }));
        if (r.error) return toast(r.error);
        models = (r.data || []).map((m) => m.id);
      } else {
        const r = await api("/api/paje/conexoes/modelos", { method: "POST", body: { preset: pre, base_url: url, api_key: key } }).catch((err) => ({ error: err.message }));
        if (r.error) return toast(r.error);
        models = r.models || [];
        rec = r.recommended || "";
      }
      const keep = q("#pMod").value || (own && own.model) || rec;
      q("#pMod").innerHTML = models.map((m) => `<option ${m === keep ? "selected" : ""}>${esc(m)}</option>`).join("") ||
        `<option value="">(nenhum modelo encontrado)</option>`;
      toast(`${models.length} modelo(s) disponível(is) neste provedor.`, true);
    };
    box.querySelector("[data-listar]").onclick = async () => {
      const c = box.querySelector("#fConn").value;
      const r = await api(`/api/paje/conexoes/${encodeURIComponent(c)}/modelos`).catch((err) => ({ error: err.message }));
      if (r.error) return toast(r.error);
      box.querySelector("#fModelos").innerHTML = (r.data || []).map((m) => `<option value="${esc(m.id)}">`).join("");
      toast(`${(r.data || []).length} modelos disponíveis em "${c}".`, true);
    };
    box.querySelector("[data-save]").onclick = async () => {
      const data = { papel: box.querySelector("#fPapel").value, alma: box.querySelector("#fAlma").value,
        ferramentas: [...box.querySelectorAll(".tool-opt input:checked")].map((i) => i.value),
        orcamento_tokens_dia: +box.querySelector("#fOrc").value, rotina_minutos: +box.querySelector("#fRotMin").value,
        rotina: box.querySelector("#fRot").value };
      if (isOwn()) {
        if (!q("#pMod").value) return toast("Busque os modelos do provedor e escolha um.");
        data.provedor = { preset: q("#pPre").value, base_url: q("#pUrl").value.trim(), api_key: q("#pKey").value.trim(),
          model: q("#pMod").value };
      } else {
        data.conexao = q("#fConn").value;
        data.modelo = q("#fModelo").value;
        if (own) data.provedor = null;        // voltou a usar uma conexão do Pajé: apaga a conexão só dele
      }
      if (!a) data.nome = box.querySelector("#fNome").value;
      const r = await api(a ? `/api/paje/agentes/${id}` : "/api/paje/agentes", { method: "POST", body: data }).catch((err) => ({ error: err.message }));
      if (r.error) return toast(r.error);
      toast(a ? "Alma salva." : `Subagente ${r.agente.nome} criado.`, true);
      close(true);
      renderTab();
    };
  });
}

async function taskDialog(agentId, ags) {
  const list = ags || (await api("/api/paje/agentes")).agentes || [];
  if (!list.length) return toast("Crie um subagente primeiro.");
  dialog(`<h3>Novo trabalho</h3><div class="form">` +
    `<label>Subagente <select id="tAg">${list.map((a) => `<option value="${esc(a.id)}" ${a.id === agentId ? "selected" : ""}>${esc(a.nome)} — ${esc(a.papel)}</option>`).join("")}</select></label>` +
    `<label>Título <input id="tTit" maxlength="200"></label>` +
    `<label>Instruções <textarea id="tIns" rows="6" placeholder="O que ele deve fazer e entregar"></textarea></label></div>` +
    `<p class="muted small">Os trabalhos rodam em paralelo (até ${(await harness()).limites.max_paralelo} ao mesmo tempo). Ações no sistema pedem aprovação conforme as permissões.</p>` +
    `<div class="dlg-btns"><button data-close>Cancelar</button><button class="ok" data-go>Começar</button></div>`, (box, close) => {
    box.querySelector("[data-go]").onclick = async () => {
      const ins = box.querySelector("#tIns").value.trim();
      if (!ins) return toast("Escreva as instruções.");
      const r = await api("/api/paje/trabalhos", { method: "POST", body: { agente: box.querySelector("#tAg").value,
        titulo: box.querySelector("#tTit").value || ins.slice(0, 60), instrucoes: ins } }).catch((err) => ({ error: err.message }));
      if (r.error) return toast(r.error);
      toast("Trabalho na fila.", true);
      close(true);
      settings.tab = "trabalhos";
      renderTab();
    };
  });
}

async function tabTrabalhos(body) {
  const r = await api("/api/paje/trabalhos");
  const ts = r.trabalhos || [];
  const col = (t) => t.status === "rodando" ? (t.aguardando_aprovacao ? "aguardando" : "rodando")
    : t.status === "cancelada" ? "falhou" : t.status;
  const cols = [["na_fila", "Na fila"], ["rodando", "Rodando"], ["aguardando", "Aguardando você"], ["concluida", "Concluídos"], ["falhou", "Falharam / cancelados"]];
  body.innerHTML = `<h3>Trabalhos</h3><p class="muted">Tudo o que o Pajé e você deram para os subagentes. Vários rodam ao mesmo tempo.</p>` +
    `<div class="dlg-btns left"><button class="ok" data-newtask>Novo trabalho</button></div>` +
    `<div class="kanban">${cols.map(([k, label]) => {
      const items = ts.filter((t) => col(t) === k);
      return `<div class="kcol k-${k}"><h4>${label} <span>${items.length}</span></h4>${items.slice(0, 30).map((t) =>
        `<div class="kcard" data-id="${esc(t.id)}"><div class="small"><b>${esc(t.agente_nome)}</b> · ${esc(ORIGEM[t.origem] || t.origem)}</div>` +
        `<div>${esc(t.titulo)}</div><div class="small muted">${ago(t.fim || t.inicio || t.criado)} · ${t.tokens || 0} tokens` +
        `${(t.passos || []).length ? ` · ${(t.passos || []).length} passo(s)` : ""}</div></div>`).join("")}</div>`;
    }).join("")}</div>`;
  body.onclick = (e) => {
    if (e.target.closest("[data-newtask]")) return taskDialog(null);
    const c = e.target.closest(".kcard");
    if (c) taskDetail(c.dataset.id);
  };
}

async function taskDetail(id) {
  const r = await api(`/api/paje/trabalhos/${id}`).catch((err) => ({ error: err.message }));
  if (r.error) return toast(r.error);
  const t = r.trabalho;
  const passos = (t.passos || []).map((s) => `<li><b>${esc(TOOL_LABELS[s.ferramenta] || s.ferramenta)}</b> — ${esc(s.status)}` +
    `${s.onde ? ` (no ${esc(s.onde)})` : ""}: ${esc(s.resumo || "")}${s.motivo ? `<div class="small muted">${esc(s.motivo)}</div>` : ""}</li>`).join("");
  dialog(`<h3>${esc(t.titulo)}</h3><div class="small muted">${esc(t.agente_nome)} · ${esc(ORIGEM[t.origem] || t.origem)} · ` +
    `${esc(t.aguardando_aprovacao ? "aguardando sua aprovação" : ST_LABEL[t.status] || t.status)} · ${t.tokens || 0} tokens` +
    `${t.modelo_usado ? " · modelo " + esc(t.modelo_usado) : ""}</div>` +
    `<h4>Instruções</h4><pre>${esc(t.instrucoes)}</pre>` +
    (passos ? `<h4>Passos</h4><ul class="rules">${passos}</ul>` : "") +
    `<h4>${t.erro && t.status !== "concluida" ? "Erro" : "Relatório para o Pajé"}</h4>` +
    `<div class="report">${t.resultado ? markdown(t.resultado) : esc(t.erro || "(ainda trabalhando)")}</div>` +
    `<div class="dlg-btns">${["na_fila", "rodando"].includes(t.status) ? `<button class="danger" data-cancel>Cancelar trabalho</button>` : ""}` +
    `<button data-close>Fechar</button></div>`, (box, close) => {
    const c = box.querySelector("[data-cancel]");
    if (c) c.onclick = async () => {
      await api(`/api/paje/trabalhos/${id}/cancelar`, { method: "POST", body: {} }).catch((err) => toast(err.message));
      toast("Cancelamento pedido.", true);
      close(true);
      renderTab();
    };
  });
}

async function tabHarness(body) {
  HARNESS_CACHE = null;
  const h = await harness();
  const toolRow = (t) => `<tr><td><code>${esc(t.nome)}</code></td><td>${esc(t.descricao)}</td></tr>`;
  body.innerHTML = `<h3>Como o Pajé funciona (harness)</h3>` +
    `<div class="flow"><span>Você</span>→<span>Pajé<br><small>constituição fixa + alma</small></span>→` +
    `<span>Subagentes<br><small>constituição + alma própria + modelo</small></span>→<span>Executor + Permission Manager<br><small>aprovação, sandbox, auditoria</small></span>→<span>Sistema</span></div>` +
    `<h4>Alma do Pajé (editável)</h4><p class="muted small">Personalidade e jeito de trabalhar. Arquivo: <code>${esc(h.arquivos.alma_paje)}</code></p>` +
    `<textarea id="hAlma" rows="10">${esc(h.alma_paje.texto)}</textarea><div class="dlg-btns left"><button class="ok" data-salma>Salvar alma do Pajé</button></div>` +
    `<details><summary>Constituição do Pajé (fixa — vem antes da alma, não é editável)</summary><pre>${esc(h.constituicao_paje)}</pre></details>` +
    `<details><summary>Constituição dos subagentes (fixa)</summary><pre>${esc(h.constituicao_subagente)}</pre></details>` +
    `<h4>Conexões de IA (cada agente pode usar uma)</h4><table class="cat-table"><thead><tr><th>Nome</th><th>Provedor</th><th>Modelo padrão</th><th></th></tr></thead><tbody>` +
    (h.conexoes || []).map((c) => `<tr><td><b>${esc(c.nome)}</b></td><td>${esc(c.provider || "-")}</td><td>${esc(c.model || "-")}${c.configured ? "" : ` <span class="act-deny">sem chave</span>`}</td>` +
      `<td>${c.nome === "principal" ? `<span class="small muted">Configurar IA</span>` : c.nome.startsWith("ag-") ? `<span class="small muted">provedor próprio de um subagente (edite nele)</span>` : `<button data-delconn="${esc(c.nome)}">Apagar</button>`}</td></tr>`).join("") + `</tbody></table>` +
    `<details class="conn-form"><summary>+ Adicionar conexão</summary><div class="form">` +
    `<div class="row2"><label>Nome <input id="cNome" placeholder="ex.: claude-rapido" maxlength="30"></label>` +
    `<label>Provedor <select id="cPre">${(h.provedores || []).map((p) => `<option value="${esc(p.id)}">${esc(p.nome)}</option>`).join("")}</select></label></div>` +
    `<label id="cUrlL" class="hidden">URL base <input id="cUrl" placeholder="https://..."></label>` +
    `<label id="cKeyL">Chave da API <input id="cKey" type="password" autocomplete="off"></label>` +
    `<div class="row2"><button data-cmod>Listar modelos</button><label>Modelo <select id="cMod"><option value="">(liste os modelos)</option></select></label></div>` +
    `<div class="dlg-btns left"><button class="ok" data-csave>Salvar conexão</button></div>` +
    `<p class="muted small">A chave fica em /etc/tupi/ai-config.json, protegida: a IA e os subagentes não conseguem lê-la.</p></div></details>` +
    `<h4>Ferramentas do Pajé</h4><table class="cat-table"><tbody>${(h.ferramentas_sistema || []).map(toolRow).join("")}</tbody></table>` +
    `<h4>Ferramentas de orquestração (só o Pajé)</h4><table class="cat-table"><tbody>${(h.ferramentas_orquestracao || []).map(toolRow).join("")}</tbody></table>` +
    `<p class="small muted">Subagentes podem receber: ${(h.ferramentas_de_subagente || []).map((n) => `<code>${esc(n)}</code>`).join(" ")} ` +
    `(nunca promover para o PD, criar agentes ou delegar).</p>` +
    `<h4>Limites</h4><ul class="rules"><li>Até ${h.limites.max_paralelo} trabalhos ao mesmo tempo; fila de até ${h.limites.max_fila}</li>` +
    `<li>Até ${h.limites.max_subagentes} subagentes; ${h.limites.max_passos} passos por trabalho</li><li>Orçamento padrão: ${h.limites.orcamento_padrao} tokens/dia por subagente</li>` +
    `<li>PARAR IA cancela todos os trabalhos; tudo fica na auditoria</li></ul>` +
    `<h4>Acesso ao harness</h4><ul class="rules"><li>Arquivos: <code>${esc(h.arquivos.pasta)}</code> (almas em <code>agentes/*.soul.md</code>, quadro em <code>trabalhos.json</code>)</li>` +
    `<li>Terminal: <code>paje</code>, <code>paje agentes</code>, <code>paje novo NOME --base pesquisador</code>, <code>paje trabalho AGENTE "…"</code>, <code>paje trabalhos</code>, <code>paje ver ID</code>, <code>paje harness</code></li>` +
    `<li>API local (JSON, só o seu usuário): <code>${esc(h.socket)}</code> — métodos agents/*, tasks/*, harness, paje/soul</li>` +
    (h.arquivos.documentacao ? `<li>Documentação completa: <code>${esc(h.arquivos.documentacao)}</code></li>` : "") + `</ul>`;
  const pre = body.querySelector("#cPre");
  const syncPre = () => {
    const p = (h.provedores || []).find((x) => x.id === pre.value) || {};
    body.querySelector("#cUrlL").classList.toggle("hidden", !p.url);
    body.querySelector("#cKeyL").classList.toggle("hidden", !!p.semchave);
  };
  pre.onchange = syncPre; syncPre();
  const connBody = () => ({ nome: body.querySelector("#cNome").value, preset: pre.value, base_url: body.querySelector("#cUrl").value,
    api_key: body.querySelector("#cKey").value, model: body.querySelector("#cMod").value });
  body.onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.salma !== undefined) {
      const r = await api("/api/paje/alma", { method: "POST", body: { texto: body.querySelector("#hAlma").value } }).catch((err) => ({ error: err.message }));
      return toast(r.error || "Alma do Pajé salva.", !r.error);
    }
    if (b.dataset.cmod !== undefined) {
      const r = await api("/api/paje/conexoes/modelos", { method: "POST", body: connBody() }).catch((err) => ({ error: err.message }));
      if (r.error) return toast(r.error);
      body.querySelector("#cMod").innerHTML = (r.models || []).map((m) => `<option ${m === r.recommended ? "selected" : ""}>${esc(m)}</option>`).join("");
      return toast(`${(r.models || []).length} modelos.`, true);
    }
    if (b.dataset.csave !== undefined) {
      const r = await api("/api/paje/conexoes", { method: "POST", body: connBody() }).catch((err) => ({ error: err.message }));
      toast(r.error || `Conexão ${r.nome} salva.`, !r.error);
      if (!r.error) renderTab();
      return;
    }
    if (b.dataset.delconn) {
      if (!confirm(`Apagar a conexão "${b.dataset.delconn}"? Agentes que a usam voltam a falhar até você trocar.`)) return;
      await api(`/api/paje/conexoes/${encodeURIComponent(b.dataset.delconn)}/apagar`, { method: "POST", body: {} }).catch((err) => toast(err.message));
      renderTab();
    }
  };
}

// ------------------------------------------- Mudanças em ensaio (ST)
async function tabST(body) {
  const s = await api("/api/st");
  if (s.error && s.disponivel === undefined) throw new Error(s.error);
  const intro = `<h3>Mudanças em ensaio</h3><p class="muted">${esc(s.o_que_e || "")}</p>`;
  if (!s.disponivel) {
    body.innerHTML = intro + `<div class="reason">${icon("alert")} ${esc(s.motivo)}</div>`;
    return;
  }
  const last = s.ultima_promocao;
  let lastHtml = "";
  if (last) {
    const txt = { aguardando: "Pedido de promoção aguardando a sua aprovação (cartão no topo da conversa).",
      ok: "Aplicado no PD.", denied: "Promoção não aplicada: " + (last.reason || ""),
      error: "A promoção falhou: " + (last.reason || (last.result && last.result.error) || "") }[last.status] || last.status;
    lastHtml = `<div class="sug"><b>Última promoção:</b> ${esc(txt)}` +
      (last.status === "ok" && last.result ? `<div class="small">Geração anterior: <code>${esc(last.result.geracao_anterior || "")}</code>` +
        (last.result.snapshot_btrfs ? ` · snapshot Btrfs: <code>${esc(last.result.snapshot_btrfs)}</code>` : "") + `</div>` : "") +
      (last.can_undo ? `<div class="dlg-btns left"><button data-undo="${esc(last.action_id)}">${icon("undo")} Desfazer (voltar a geração anterior)</button></div>` : "") +
      `</div>`;
  }
  const e = s.ensaio;
  if (!e) {
    body.innerHTML = intro + `<div class="status-line">ST: ${esc(s.container)} · nenhuma mudança em ensaio.</div>` + lastHtml +
      `<p class="muted small">Peça à IA, por exemplo: "teste no ST e depois aplique: instale o htop". ${esc(s.kernel || "")}</p>`;
    body.onclick = (ev) => { const u = ev.target.closest("[data-undo]"); if (u) undoAction(u.dataset.undo, u); };
    return;
  }
  const v = s.verificacoes || {};
  const checks = [
    `<li>ST ligou com a mudança: <b>${v.st_ligou ? "sim" : "NÃO"}</b></li>`,
    `<li>Unidades com falha <b>novas</b>: ${(v.unidades_com_falha_novas || []).length ? `<span class="act-deny">${esc(v.unidades_com_falha_novas.join(", "))}</span>` : "nenhuma"}` +
      ((v.unidades_com_falha_ja_existentes || []).length ? ` <span class="muted">(já falhavam no ST sem a mudança: ${esc(v.unidades_com_falha_ja_existentes.join(", "))})</span>` : "") + `</li>`,
    v.servico_alvo ? `<li>Serviço ${esc(v.servico_alvo.unidade)}: <b>${esc(v.servico_alvo.estado)}</b></li>` : "",
    (v.programas_novos || []).length ? `<li>Programas novos: ${v.programas_novos.map((p) => `${esc(p.programa)} ${p.encontrado_no_st ? "✓" : "✗"}`).join(", ")}</li>` : "",
    v.kernel ? `<li class="warn">${esc(v.kernel)}</li>` : "",
  ].join("");
  body.innerHTML = intro +
    `<div class="sug ${v.ok ? "" : "st-bad"}"><div class="a-head"><b>${esc(e.descricao || e.resumo)}</b>` +
    `<span class="a-st">ensaio ${esc(e.id)} · verificações ${v.ok ? "OK" : "com problemas"}</span></div>` +
    `<ul class="rules">${checks}</ul>` +
    `<div class="muted small">Mudanças: ${(s.mudancas || []).map((m) => esc(m.descricao || m.resumo)).join(" · ")}</div>` +
    `<h4>O que muda no seu sistema (PD)</h4><pre class="diff">${esc(s.diff_texto || "")}</pre>` +
    `<div class="dlg-btns left"><button class="ok" data-st="promote">Promover para o PD</button>` +
    `<button class="danger" data-st="discard">Descartar</button></div>` +
    `<p class="muted small">Promover sempre pede a sua aprovação (com este diff), cria um ponto de retorno ` +
    `(geração anterior e snapshot Btrfs, se houver) e oferece Desfazer. Descartar desliga o ST; o PD não muda.</p></div>` + lastHtml;
  body.onclick = async (ev) => {
    const u = ev.target.closest("[data-undo]");
    if (u) { undoAction(u.dataset.undo, u); return; }
    const b = ev.target.closest("[data-st]");
    if (!b) return;
    if (b.dataset.st === "discard") {
      if (!confirm("Descartar a mudança em ensaio? O seu sistema (PD) não muda.")) return;
      const r = await api("/api/st/discard", { method: "POST", body: {} }).catch((err) => ({ ok: false, error: err.message }));
      toast(r.ok === false ? (r.error || "não foi possível descartar") : "Ensaio descartado.", r.ok !== false);
    } else {
      const r = await api("/api/st/promote", { method: "POST", body: {} }).catch((err) => ({ ok: false, error: err.message }));
      toast(r.ok === false ? r.error : "Confirme no cartão de aprovação (mostra o diff).", r.ok !== false);
      $("modal").classList.add("hidden");
    }
    pollAgent();
    if (!$("modal").classList.contains("hidden")) setTimeout(renderTab, 800);
  };
}
$("stBar").addEventListener("click", (e) => { if (e.target.closest("[data-stbar]")) openSettings("st"); });

const OBS_LABELS = { app: "Programas usados", comando: "Comandos no terminal", repeticao: "Comandos repetidos",
  arquivo_tipo: "Tipos de arquivo", pasta: "Pastas usadas", erro: "Erros nos logs", site: "Sites (domínios)" };
const obsLabel = (k) => OBS_LABELS[k] || (k.startsWith("app_hora_") ? `Programas às ${k.slice(9)}h` : k);

async function tabMemory(body) {
  const r = await api("/api/memory");
  const facts = r.facts || [], obs = r.observations || [];
  body.innerHTML = `<h3>O que a IA sabe sobre mim</h3>` +
    `<p class="muted">Fica só neste computador (banco local do Agent Orchestrator). Para a IA vão apenas estes resumos curtos — nunca o seu histórico bruto. Você pode corrigir ou apagar qualquer item.</p>` +
    (r.error ? `<div class="reason">${esc(r.error)}</div>` : "") +
    `<div class="facts">${facts.length ? facts.map((f) => `<div class="fact" data-id="${f.id}" data-key="${esc(f.key)}">` +
      `<input value="${esc(f.value)}"><span class="src">${esc(f.source || "")}</span>` +
      `<button data-save title="Salvar">${icon("check")}</button><button data-del title="Apagar">${icon("trash")}</button></div>`).join("")
      : `<div class="muted">Nada ainda.</div>`}</div>` +
    `<div class="fact add"><input id="newFact" placeholder="Ensine algo à IA (ex.: Prefiro respostas curtas)"><button data-add>Adicionar</button></div>` +
    `<div class="dlg-btns left"><button class="danger" data-clear>Apagar tudo o que a IA sabe</button></div>` +
    `<h3>Hábitos observados (só contagens)</h3>` +
    (obs.length ? `<table class="cat-table"><thead><tr><th>Tipo</th><th>Itens</th><th>Vezes</th><th></th></tr></thead><tbody>` +
      obs.map((o) => `<tr><td>${esc(obsLabel(o.kind))}</td><td>${o.keys}</td><td>${o.total}</td><td><button data-forget="${esc(o.kind)}">Esquecer</button></td></tr>`).join("") +
      `</tbody></table><div class="dlg-btns left"><button data-forget="">Esquecer todas as observações</button></div>`
      : `<div class="muted">Nenhuma observação ainda (o modo proativo coleta quando está ligado).</div>`);
  body.onclick = async (e) => {
    const row = e.target.closest(".fact");
    let r2 = null;
    if (e.target.closest("[data-save]")) r2 = await api("/api/memory/set", { method: "POST", body: { key: row.dataset.key, value: row.querySelector("input").value } });
    else if (e.target.closest("[data-del]")) r2 = await api("/api/memory/delete", { method: "POST", body: { id: +row.dataset.id } });
    else if (e.target.closest("[data-add]")) {
      const v = $("newFact").value.trim();
      if (!v) return;
      r2 = await api("/api/memory/set", { method: "POST", body: { value: v } });
    } else if (e.target.closest("[data-clear]")) {
      if (!confirm("Apagar tudo o que a IA aprendeu sobre você?")) return;
      r2 = await api("/api/memory/clear", { method: "POST", body: {} });
    } else if (e.target.closest("[data-forget]")) {
      r2 = await api("/api/memory/forget", { method: "POST", body: { kind: e.target.closest("[data-forget]").dataset.forget } });
    } else return;
    if (r2 && r2.error) toast(r2.error); else toast("Feito.", true);
    renderTab();
  };
}

async function tabLoop(body) {
  const r = await api("/api/loop");
  const s = r.settings, st = r.status || {};
  const src = [["apps", "Programas que você usa"], ["terminal", "Comandos do terminal (só o nome do programa)"],
    ["files", "Arquivos recentes (só tipo e pasta)"], ["browser", "Navegador (só domínios) — desligado por padrão"]];
  const when = st.updated ? new Date(st.updated * 1000).toLocaleTimeString("pt-BR") : "-";
  body.innerHTML = `<h3>IA proativa</h3>` +
    `<p class="muted">Observa seus hábitos localmente, pensa de tempos em tempos e <b>propõe</b> ajudas. Nada é executado sem passar pelas permissões. Pausa quando você está ausente ou na bateria.</p>` +
    `<div class="status-line">Agora: <b>${esc(st.state || "desconhecido")}</b> — ${esc(st.detail || "")} <span class="muted">(${when})</span>` +
    ` · tokens hoje: ${esc(st.tokens_today || 0)} de ${esc(s.daily_tokens)}</div>` +
    `<label class="row"><input type="checkbox" id="lpOn"${s.enabled ? " checked" : ""}> Modo proativo ligado</label>` +
    `<label class="row"><input type="checkbox" id="lpSilent"${s.silent ? " checked" : ""}> Modo silencioso (aprende, mas não interrompe)</label>` +
    `<label class="row">Intervalo entre ciclos: <input type="number" id="lpInt" min="5" max="240" value="${s.interval_min}"> min <span class="muted">(metade quando você está ativo)</span></label>` +
    `<label class="row">Limite diário de tokens: <input type="number" id="lpTok" min="0" max="500000" step="1000" value="${s.daily_tokens}"></label>` +
    `<h4>O que observar</h4>` + src.map(([k, l]) => `<label class="row"><input type="checkbox" data-src="${k}"${s.sources[k] ? " checked" : ""}> ${esc(l)}</label>`).join("") +
    `<div class="dlg-btns left"><button class="ok" data-save>Salvar</button><button class="danger" data-stop>Parar o modo proativo agora</button></div>`;
  const save = async (extra) => {
    const sources = {};
    body.querySelectorAll("[data-src]").forEach((c) => { sources[c.dataset.src] = c.checked; });
    await api("/api/loop", { method: "POST", body: Object.assign({ enabled: $("lpOn").checked, silent: $("lpSilent").checked,
      interval_min: +$("lpInt").value, daily_tokens: +$("lpTok").value, sources }, extra || {}) });
    toast("Configuração salva.", true);
    renderTab();
  };
  body.querySelector("[data-save]").onclick = () => save();
  body.querySelector("[data-stop]").onclick = () => save({ enabled: false });
}

// diferença linha a linha (LCS) para mostrar o que uma sugestão muda num arquivo
function diffLines(a, b) {
  const x = (a || "").split("\n"), y = (b || "").split("\n");
  if (x.length * y.length > 250000) return y.map((l) => ["+", l]);
  const n = x.length, m = y.length, t = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) t[i][j] = x[i] === y[j] ? t[i + 1][j + 1] + 1 : Math.max(t[i + 1][j], t[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { out.push([" ", x[i]]); i++; j++; } else if (t[i + 1][j] >= t[i][j + 1]) out.push(["-", x[i++]]); else out.push(["+", y[j++]]);
  }
  while (i < n) out.push(["-", x[i++]]);
  while (j < m) out.push(["+", y[j++]]);
  return out;
}

async function tabSugs(body) {
  const r = await api("/api/suggestions");
  const list = r.suggestions || [];
  body.innerHTML = `<h3>Sugestões da IA</h3><p class="muted">Propostas do modo proativo. Você vê exatamente o que será feito; ao aplicar, a ação passa pelas permissões (e pode pedir sua aprovação). Mudanças no sistema são testadas antes (build) e podem ser desfeitas.</p>` +
    (list.length ? list.map((s) => {
      let what = "";
      if (s.arquivo && s.arquivo.path) {
        const d = diffLines(s.atual === null || s.atual === undefined ? "" : s.atual, s.arquivo.content || "");
        what += `<div class="muted small">${s.atual === null || s.atual === undefined ? "Criar" : "Alterar"} o arquivo <code>${esc(s.arquivo.path)}</code>:</div>` +
          `<pre class="diff">${d.map(([k, l]) => `<span class="d${k === "+" ? "add" : k === "-" ? "del" : ""}">${esc(k + " " + l)}</span>`).join("\n")}</pre>`;
      }
      if (s.acao && s.acao.tool) what += `<div class="muted small">Ação: <b>${esc(TOOL_LABELS[s.acao.tool] || s.acao.tool)}</b></div><pre>${esc(JSON.stringify(s.acao.args || {}, null, 1))}</pre>`;
      const res = (s.resultado || []).map((x) => `<div class="small">${esc(x.status)}: ${esc(x.summary || "")} ${esc(x.reason || "")}</div>`).join("");
      return `<div class="sug st-${esc(s.status)}"><div class="a-head"><b>${esc(s.titulo)}</b><span class="a-st">${esc(s.status)}</span></div>` +
        `<div>${esc(s.motivo || "")}</div>${what}${res}` +
        (s.status === "nova" ? `<div class="dlg-btns left">${(s.arquivo || s.acao) ? `<button class="ok" data-apply="${esc(s.id)}">Aplicar</button>` : ""}<button data-dismiss="${esc(s.id)}">Dispensar</button></div>` : "") + `</div>`;
    }).join("") : `<div class="muted">Nenhuma sugestão por enquanto.</div>`);
  body.onclick = async (e) => {
    const ap = e.target.closest("[data-apply]"), di = e.target.closest("[data-dismiss]");
    if (!ap && !di) return;
    const id = (ap || di).dataset.apply || (ap || di).dataset.dismiss;
    await api(`/api/suggestions/${id}`, { method: "POST", body: { action: ap ? "apply" : "dismiss" } }).catch((err) => toast(err.message));
    toast(ap ? "Aplicando… (se precisar, a IA pede sua aprovação)" : "Dispensada.", true);
    setTimeout(renderTab, 600);
  };
}

async function tabHistory(body) {
  const r = await api("/api/agent/history");
  const acts = (r.actions || []).slice().reverse();
  body.innerHTML = `<h3>Ações recentes da IA</h3><p class="muted">Desde que o executor iniciou. O registro completo e imutável fica na auditoria (<code>/var/log/tupi-audit</code>).</p>` +
    (acts.length ? `<table class="cat-table"><thead><tr><th>Quando</th><th>Ação</th><th>Resultado</th><th></th></tr></thead><tbody>` +
      acts.map((a) => `<tr><td>${new Date(a.time * 1000).toLocaleTimeString("pt-BR")}</td><td><b>${esc(TOOL_LABELS[a.tool] || a.tool)}</b><div class="src">${esc(a.summary || "")}</div></td>` +
        `<td>${esc(a.status)}${a.undone ? " · desfeito" : ""}</td><td>${a.can_undo ? `<button data-undo="${esc(a.action_id)}">${icon("undo")} Desfazer</button>` : ""}</td></tr>`).join("") +
      `</tbody></table>` : `<div class="muted">Nenhuma ação ainda.</div>`);
  body.onclick = (e) => { const u = e.target.closest("[data-undo]"); if (u) undoAction(u.dataset.undo, u); };
}

// usados pelo app GTK (bolinha)
window.tupiSettings = (tab) => openSettings(tab || "perms");
window.tupiFocus = () => {
  focusInput();
  const id = localStorage.getItem("tupi.current");
  if (id && (!state.conv || state.conv.id !== id)) openConv(id);
};
window.tupiScreenshot = (mode) => screenshot(mode);

// ------------------------------------------------------------------- início
(async function init() {
  if (MODE === "mini") document.body.classList.add("mini");
  renderMessages();
  await Promise.all([loadStatus(), loadConvs().catch(() => {})]);
  const last = localStorage.getItem("tupi.current") || (state.convs[0] && state.convs[0].id);
  if (last) await openConv(last);
  loadModels();
  focusInput();
  // Enquanto a IA não estiver configurada, verifica de novo a cada 8 s
  setInterval(() => { if (!state.status || !state.status.configured) loadStatus(); }, 8000);
  // Agente: modo, PARAR IA, pedidos de aprovação (também aparecem na bolinha)
  await pollAgent();
  setInterval(pollAgent, 1500);
  if (state.agent && state.agent.available) {
    api("/api/policy").then((p) => { if (!p.error) { state.policy = p; renderMessages(false); } }).catch(() => {});
  }
  const openTab = params.get("open");
  if (["perms", "agentes", "trabalhos", "harness", "st", "memory", "loop", "sugs", "history"].includes(openTab)) {
    openSettings(openTab);
  }
})();
