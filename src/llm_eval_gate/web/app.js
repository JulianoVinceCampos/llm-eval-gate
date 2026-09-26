// llm-eval-gate dashboard. No framework, no CDN, no innerHTML: every value from the API
// is written with textContent, so a case text or a model answer cannot become markup.
"use strict";

const OUTCOME = {
  pass: "PASSA", fail: "REPROVA", inconclusive: "INCONCLUSIVO",
  pending: "PENDENTE", error: "ERRO", evaluated: "AVALIADO",
};
const STATUS = { met: "atende", not_met: "não atende", no_evidence: "sem evidência" };
const SVG = "http://www.w3.org/2000/svg";
const state = { overview: null, labels: [], view: "overview" };

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    if (key === "text") node.textContent = String(value);
    else if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = String(text);
  return node;
}

const pct = (v, d = 1) => (v === null || v === undefined ? "n/d" : `${(v * 100).toFixed(d)}%`);
const pp = (v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)} p.p.`;
const badge = (key, label) => el("span", { class: `badge ${key}`, text: label || OUTCOME[key] || key });

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (response.status === 401) { showLogin(); throw new Error("unauthenticated"); }
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function table(headers, rows) {
  const head = el("thead", {}, el("tr", {}, headers.map((h) => el("th", { text: h }))));
  const body = el("tbody", {}, rows);
  return el("div", { class: "table-wrap" }, el("table", {}, [head, body]));
}

function candidateSelect(id, filter, onChange) {
  const names = state.overview.candidates.filter(filter).map((c) => c.name);
  const select = el("select", { id, onchange: () => onChange(select.value) },
    names.map((n) => el("option", { value: n, text: n })));
  return { select, names };
}

// --- charts --------------------------------------------------------------------------

function intervalChart(items, margin) {
  // Paired difference and its interval, one row per split, against the -margin line.
  const width = 720, rowH = 46, pad = 70, height = 40 + rowH * items.length;
  const lo = Math.min(-0.2, ...items.map((i) => i.lo)) - 0.02;
  const hi = Math.max(0.1, ...items.map((i) => i.hi)) + 0.02;
  const x = (v) => pad + ((v - lo) / (hi - lo)) * (width - pad - 20);
  const chart = svg("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": "Intervalo da diferença pareada contra a margem" });
  chart.append(svg("line", { class: "zero", x1: x(0), x2: x(0), y1: 10, y2: height - 20 }));
  chart.append(svg("line", { class: "margin", x1: x(-margin), x2: x(-margin), y1: 10, y2: height - 20 }));
  chart.append(svg("text", { x: x(-margin) + 4, y: height - 6 }, `-margem ${(margin * 100).toFixed(1)} p.p.`));
  chart.append(svg("text", { x: x(0) + 4, y: 20 }, "0"));
  items.forEach((item, index) => {
    const y = 36 + index * rowH;
    chart.append(svg("text", { x: 4, y: y + 4 }, item.split));
    chart.append(svg("line", { class: `ci ${item.verdict}`, x1: x(item.lo), x2: x(item.hi), y1: y, y2: y }));
    chart.append(svg("circle", { class: "point", cx: x(item.delta), cy: y, r: 4 }));
    chart.append(svg("text", { x: x(item.hi) + 8, y: y + 4 },
      `${pp(item.delta)} [${(item.lo * 100).toFixed(1)}, ${(item.hi * 100).toFixed(1)}]`));
  });
  return chart;
}

function operatorChart(operators, aName, bName) {
  const width = 720, rowH = 30, pad = 130, height = 30 + rowH * operators.length;
  const x = (v) => pad + v * (width - pad - 60);
  const chart = svg("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": "Acurácia por operador de perturbação" });
  operators.forEach((op, index) => {
    const y = 12 + index * rowH;
    chart.append(svg("text", { x: 4, y: y + 12 }, `${op.operator} (${op.cases})`));
    chart.append(svg("rect", { class: "bar-a", x: pad, y, width: x(op.a_accuracy) - pad, height: 10 }));
    chart.append(svg("rect", { class: "bar-b", x: pad, y: y + 11, width: x(op.b_accuracy) - pad, height: 10 }));
    chart.append(svg("text", { x: x(Math.max(op.a_accuracy, op.b_accuracy)) + 6, y: y + 16 },
      `${pct(op.a_accuracy, 0)} / ${pct(op.b_accuracy, 0)}`));
  });
  chart.append(svg("text", { x: pad, y: height - 4 }, `claro: ${aName}  escuro: ${bName}`));
  return chart;
}

// --- views ---------------------------------------------------------------------------

function renderOverview() {
  const data = state.overview;
  const view = document.getElementById("view-overview");
  const cards = el("div", { class: "cards" }, [
    card("Casos de avaliação", `${data.splits["in-dist"] + data.splits.hard}`,
      `in-dist ${data.splits["in-dist"]}, hard ${data.splits.hard}`),
    card("Margem de não inferioridade", `${(data.policy.margin * 100).toFixed(1)} p.p.`,
      `alfa ${data.policy.alpha}, score de ${data.policy.method === "tango" ? "Tango" : data.policy.method}, inconclusivo ${data.policy.on_inconclusive === "fail" ? "bloqueia" : "avisa"}`),
    card("Spec", `${data.spec.requirements} requisitos`, `${data.spec.id} v${data.spec.version}`),
    card("Dataset", data.dataset_sha256.slice(0, 12), "sha256 dos splits de avaliação"),
  ]);
  const rows = data.candidates.map((c) => el("tr", {}, [
    el("td", {}, [el("strong", { text: c.name }), el("div", { class: "muted", text: c.description })]),
    el("td", { text: c.stage }),
    el("td", {}, badge(c.outcome)),
    el("td", { text: c.splits["in-dist"] ? pct(c.splits["in-dist"].accuracy) : "n/d" }),
    el("td", { text: c.splits.hard ? pct(c.splits.hard.accuracy) : "n/d" }),
    el("td", { text: c.splits.hard ? pct(c.splits.hard.format_valid_rate) : "n/d" }),
    el("td", { text: c.splits.hard ? pct(c.splits.hard.instability_rate) : "n/d" }),
    el("td", { text: c.splits.hard ? `${c.splits.hard.latency_p95_ms.toFixed(1)} ms` : "n/d" }),
  ]));
  const scenarios = data.scenarios.map((s) => el("tr", {}, [
    el("td", {}, [el("strong", { text: s.title }), el("div", { class: "muted", text: s.story })]),
    el("td", {}, badge(s.expected)),
    el("td", {}, badge(s.verdict)),
  ]));
  view.replaceChildren(
    el("h1", { text: "Estado do gate" }),
    cards,
    el("h2", { text: "Candidatos" }),
    table(["Candidato", "Estágio", "Resultado", "in-dist", "hard", "Formato", "Instabilidade", "p95"], rows),
    el("h2", { text: "Cenários de validação do gate" }),
    el("p", { class: "muted", text: "Modelo sintético com acurácia-alvo conhecida. A suíte de testes exige que cada cenário produza o veredito esperado." }),
    table(["Cenário", "Esperado", "Obtido"], scenarios),
  );
}

function card(key, value, sub) {
  return el("div", { class: "card" }, [el("div", { class: "k", text: key }),
    el("div", { class: "v", text: value }), el("div", { class: "s", text: sub })]);
}

async function renderGate(name) {
  const view = document.getElementById("view-gate");
  const { select, names } = candidateSelect("gate-candidate", () => true, renderGate);
  const chosen = name || names[0];
  select.value = chosen;
  const data = await api(`/api/candidate?name=${encodeURIComponent(chosen)}`);
  const content = [el("h1", { text: "Gate por candidato" }),
    el("div", { class: "controls" }, el("label", {}, ["Candidato", select]))];
  content.push(el("p", { class: "verdict-line" }, ["Resultado: ", badge(data.outcome),
    data.blocking ? " bloqueia o PR" : " não bloqueia"]));
  if (data.message) content.push(el("p", { class: "explain", text: data.message }));
  if (data.report) {
    const ni = data.report.checks.filter((c) => c.kind === "non-inferiority" && c.details.delta !== undefined);
    if (ni.length) {
      content.push(el("h2", { text: "Não inferioridade contra a referência" }));
      content.push(intervalChart(ni.map((c) => c.details), ni[0].details.margin));
    }
    const rows = data.report.checks.map((c) => el("tr", {}, [
      el("td", { text: c.title }), el("td", { text: c.enforced ? "sim" : "não" }),
      el("td", {}, badge(c.verdict)), el("td", { text: c.summary }),
    ]));
    content.push(el("h2", { text: "Verificações" }), table(["Verificação", "Obrigatória", "Resultado", "Detalhe"], rows));
  }
  view.replaceChildren(...content);
}

async function renderWhatIf() {
  const view = document.getElementById("view-whatif");
  const withRef = (c) => c.has_reference && c.status === "evaluated";
  const { select, names } = candidateSelect("wi-candidate", withRef, () => update());
  if (!names.length) {
    view.replaceChildren(el("h1", { text: "E se?" }), el("p", { text: "Nenhum candidato com referência aceita." }));
    return;
  }
  const split = el("select", { id: "wi-split", onchange: () => update() },
    ["hard", "in-dist"].map((s) => el("option", { value: s, text: s })));
  const margin = el("input", { id: "wi-margin", type: "range", min: "0", max: "0.1", step: "0.005", value: String(state.overview.policy.margin) });
  const marginOut = el("output", { for: "wi-margin" });
  const alpha = el("select", { id: "wi-alpha", onchange: () => update() },
    ["0.01", "0.025", "0.05", "0.1"].map((a) => el("option", { value: a, text: a })));
  alpha.value = String(state.overview.policy.alpha);
  const size = el("select", { id: "wi-n", onchange: () => update() },
    [["0", "todos"], ["200", "200"], ["100", "100"], ["50", "50"], ["20", "20"]].map(([v, t]) => el("option", { value: v, text: t })));
  const result = el("div");
  margin.addEventListener("input", () => { marginOut.textContent = `${(Number(margin.value) * 100).toFixed(1)} p.p.`; });
  margin.addEventListener("change", () => update());
  marginOut.textContent = `${(Number(margin.value) * 100).toFixed(1)} p.p.`;

  async function update() {
    const query = new URLSearchParams({ name: select.value, split: split.value, margin: margin.value, alpha: alpha.value, n: size.value });
    try {
      const data = await api(`/api/whatif?${query}`);
      result.replaceChildren(
        el("p", { class: "verdict-line" }, ["Veredito: ", badge(data.verdict), ` com ${data.used} de ${data.available} casos pareados`]),
        intervalChart([data], data.margin),
        el("p", { class: "explain", text: explainVerdict(data) }),
        el("p", { class: "muted", text: `McNemar exato: ${data.mcnemar.worse} casos piores, ${data.mcnemar.better} melhores, p unilateral de piora ${data.mcnemar.p_worse.toPrecision(2)}.` }),
      );
    } catch (error) {
      result.replaceChildren(el("p", { class: "error", text: error.message }));
    }
  }

  view.replaceChildren(
    el("h1", { text: "E se a margem, o alfa ou a amostra fossem outros?" }),
    el("p", { class: "muted", text: "Mesmo dado, decisão refeita no servidor com o mesmo código do CI. Reduza a amostra para ver o veredito virar inconclusivo." }),
    el("div", { class: "controls" }, [
      el("label", {}, ["Candidato", select]), el("label", {}, ["Split", split]),
      el("label", {}, ["Margem", margin, marginOut]), el("label", {}, ["Alfa", alpha]),
      el("label", {}, ["Casos", size]),
    ]),
    result,
  );
  update();
}

function explainVerdict(d) {
  const m = (d.margin * 100).toFixed(1);
  if (d.verdict === "pass") return `O limite inferior (${(d.lo * 100).toFixed(1)} p.p.) está acima de -${m} p.p.: os dados descartam uma perda maior que a margem.`;
  if (d.verdict === "fail") return `O limite superior (${(d.hi * 100).toFixed(1)} p.p.) está abaixo de -${m} p.p.: os dados descartam que a perda caiba na margem.`;
  return `O intervalo cruza -${m} p.p.: os dados não sustentam nem aprovar nem reprovar. Mais casos resolvem; fingir certeza não.`;
}

function renderCompare() {
  const view = document.getElementById("view-compare");
  const comparisons = state.overview.comparisons;
  const content = [el("h1", { text: "O LLM classifica melhor que as regras, e a que custo?" })];
  if (!comparisons.length) {
    content.push(el("p", { class: "explain", text: "A comparação aparece quando existir uma gravação de LLM (cassette) feita pelo workflow live-eval. Até lá, o painel mostra o baseline e a régua funcionando." }));
  }
  for (const c of comparisons) {
    content.push(el("h2", { text: `${c.b} contra ${c.a} [${c.split}]` }));
    content.push(el("p", { text: `${c.n_cases} casos pareados. Acurácia ${pct(c.a_accuracy)} contra ${pct(c.b_accuracy)}, diferença ${pp(c.delta)} (IC95 [${(c.lo * 100).toFixed(1)}, ${(c.hi * 100).toFixed(1)}]). McNemar p = ${c.mcnemar.p_two_sided.toPrecision(2)}.` }));
    if (c.operators.length) content.push(operatorChart(c.operators, c.a, c.b));
  }
  view.replaceChildren(...content);
}

async function renderTrace(name) {
  const view = document.getElementById("view-trace");
  const { select, names } = candidateSelect("trace-candidate", (c) => c.status === "evaluated", renderTrace);
  const chosen = name || names[0];
  select.value = chosen;
  const data = await api(`/api/candidate?name=${encodeURIComponent(chosen)}`);
  const rows = data.trace.map((r) => el("tr", {}, [
    el("td", { class: "mono", text: r.id }),
    el("td", {}, [el("strong", { text: r.title }), el("div", { class: "muted mono", text: r.criterion })]),
    el("td", { text: r.enforced ? "bloqueia" : r.enforcement === "block" ? "bloqueia em produção" : "meta" }),
    el("td", { text: r.compared === null || r.compared === undefined ? "n/d" : Number(r.compared).toPrecision(4) }),
    el("td", { text: String(r.n) }),
    el("td", {}, badge(r.status, STATUS[r.status])),
    el("td", {}, el("div", { class: "ops" }, r.failing_cases.map((id) =>
      el("a", { href: "#", class: "op", text: id, onclick: (e) => { e.preventDefault(); openCase(id); } })))),
  ]));
  view.replaceChildren(
    el("h1", { text: "Requisito, evidência, resultado" }),
    el("div", { class: "controls" }, el("label", {}, ["Candidato", select])),
    table(["Requisito", "Critério", "Aplicação", "Medido", "n", "Status", "Casos que puxam para baixo"], rows),
  );
}

async function renderCases(filters = {}) {
  const view = document.getElementById("view-cases");
  const split = el("select", {}, [["", "todos"], ["in-dist", "in-dist"], ["hard", "hard"]].map(([v, t]) => el("option", { value: v, text: t })));
  const label = el("select", {}, [el("option", { value: "", text: "todos" })].concat(state.labels.map((l) => el("option", { value: l.value, text: l.value }))));
  const { select: cand } = candidateSelect("cases-candidate", (c) => c.status === "evaluated", () => load(0));
  const errors = el("input", { type: "checkbox" });
  split.value = filters.split || "hard";
  const list = el("div");
  const detail = el("div");
  for (const input of [split, label, errors]) input.addEventListener("change", () => load(0));

  async function load(offset) {
    const query = new URLSearchParams({ split: split.value, label: label.value, candidate: cand.value, errors: errors.checked ? "1" : "0", offset: String(offset), limit: "50" });
    const data = await api(`/api/cases?${query}`);
    const rows = data.rows.map((r) => el("tr", { class: "clickable", onclick: () => openCase(r.id, detail) }, [
      el("td", { class: "mono", text: r.id }), el("td", { text: r.split }), el("td", { text: r.label }),
      el("td", {}, r.predicted === null ? "n/d" : badge(r.predicted === r.label ? "pass" : "fail", r.predicted)),
      el("td", {}, el("div", { class: "ops" }, r.operators.map((o) => el("span", { class: "op", text: o })))),
    ]));
    const pager = el("p", { class: "muted" }, [`${data.total} casos. `,
      offset > 0 ? el("a", { href: "#", text: "anteriores", onclick: (e) => { e.preventDefault(); load(offset - 50); } }) : "",
      " ",
      offset + 50 < data.total ? el("a", { href: "#", text: "próximos", onclick: (e) => { e.preventDefault(); load(offset + 50); } }) : ""]);
    list.replaceChildren(table(["Caso", "Split", "Rótulo", "Previsto (maioria)", "Operadores"], rows), pager);
  }

  view.replaceChildren(
    el("h1", { text: "Casos" }),
    el("div", { class: "controls" }, [el("label", {}, ["Split", split]), el("label", {}, ["Rótulo", label]),
      el("label", {}, ["Candidato", cand]), el("label", {}, ["Só erros", errors])]),
    el("div", { class: "split" }, [list, detail]),
  );
  state.caseDetail = detail;
  await load(0);
}

async function openCase(id, target) {
  if (!target) { await switchView("cases"); target = state.caseDetail; }
  const data = await api(`/api/case?id=${encodeURIComponent(id)}`);
  const preds = Object.entries(data.predictions).map(([name, p]) => el("tr", {}, [
    el("td", { text: name }), el("td", {}, badge(p.majority === data.label ? "pass" : "fail", p.majority)),
    el("td", { class: "mono", text: p.predictions.join(", ") }),
  ]));
  target.replaceChildren(
    el("h2", { text: `${data.id} (${data.split}, ${data.lang})` }),
    el("p", {}, ["Rótulo verdadeiro: ", el("strong", { text: data.label })]),
    el("div", { class: "ops" }, data.operators.map((o) => el("span", { class: "op", text: o }))),
    el("pre", { class: "case", text: data.text }),
    table(["Candidato", "Maioria", "Respostas"], preds),
  );
}

const METHOD = {
  tango: "Score de Tango (1998): decide o gate",
  "agresti-min": "Wald+2 (Agresti e Min, 2005): comparação",
  bootstrap: "Bootstrap percentil: comparação",
};

async function renderCalibration() {
  const view = document.getElementById("view-calibration");
  const data = await api("/api/calibration");
  if (!data.available) { view.replaceChildren(el("p", { text: "Calibração ainda não gravada." })); return; }
  const spec = data.spec;
  const atMargin = (c) => Math.abs(c.effect + spec.margin) < 1e-9;
  const sections = spec.methods.flatMap((method) => {
    const rows = data.cells.filter((c) => c.method === method).map((c) => el("tr", { class: atMargin(c) ? "at-margin" : null }, [
      el("td", { text: String(c.n) }), el("td", { text: pp(c.effect) }),
      heat(c.pass / c.trials, "pass"), heat(c.inconclusive / c.trials, "inconclusive"), heat(c.fail / c.trials, "fail"),
    ]));
    return [el("h2", { text: METHOD[method] || method }), table(["Casos", "Efeito real", "Passa", "Inconclusivo", "Reprova"], rows)];
  });
  view.replaceChildren(
    el("h1", { text: "A régua também é medida" }),
    el("p", { text: `${spec.trials} simulações por célula com modelo sintético de efeito conhecido, decididas pelo mesmo código do CI. Margem ${(spec.margin * 100).toFixed(1)} p.p., alfa unilateral ${spec.alpha}, correlação entre modelos ${spec.correlation}. Os métodos decidem sobre os mesmos pares simulados.` }),
    el("p", { class: "explain", text: `Nas linhas em destaque o efeito real é igual à margem: ali "Passa" é aprovação indevida e deveria ficar perto de ${(spec.alpha * 100).toFixed(0)}%. Com efeito zero, "Reprova" é alarme falso e tudo o que não é "Passa" bloqueia o PR.` }),
    ...sections,
  );
}

function heat(value, kind) {
  const cell = el("td", { class: "heat", text: pct(value, 0) });
  const colors = { pass: [29, 122, 70], inconclusive: [138, 90, 0], fail: [179, 38, 30] };
  const [r, g, b] = colors[kind];
  cell.style.backgroundColor = `rgba(${r}, ${g}, ${b}, ${Math.min(0.85, value * 0.8).toFixed(2)})`;
  cell.style.color = value > 0.55 ? "#fff" : "inherit";
  return cell;
}

// --- shell ---------------------------------------------------------------------------

const RENDER = { overview: renderOverview, gate: () => renderGate(), whatif: renderWhatIf, compare: renderCompare,
  trace: () => renderTrace(), cases: () => renderCases(), calibration: renderCalibration, about: () => {} };

async function switchView(name) {
  state.view = name;
  for (const button of document.querySelectorAll("#tabs button")) button.setAttribute("aria-selected", String(button.dataset.view === name));
  for (const section of document.querySelectorAll(".view")) section.hidden = section.id !== `view-${name}`;
  await RENDER[name]();
}

function showLogin() {
  document.getElementById("login").hidden = false;
  document.getElementById("tabs").hidden = true;
  document.getElementById("logout").hidden = true;
  for (const section of document.querySelectorAll(".view")) section.hidden = true;
}

async function start() {
  document.getElementById("login").hidden = true;
  document.getElementById("tabs").hidden = false;
  document.getElementById("logout").hidden = false;
  state.overview = await api("/api/overview");
  state.labels = (await api("/api/labels")).labels;
  document.getElementById("version").textContent = `llm-eval-gate ${state.overview.version}`;
  await switchView("overview");
}

document.addEventListener("DOMContentLoaded", async () => {
  document.getElementById("tabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-view]");
    if (button) switchView(button.dataset.view);
  });
  document.getElementById("logout").addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", credentials: "same-origin" });
    showLogin();
  });
  document.getElementById("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("login-error");
    error.textContent = "";
    const response = await fetch("/api/login", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: document.getElementById("login-user").value, password: document.getElementById("login-password").value }),
    });
    if (response.ok) { await start(); } else { error.textContent = (await response.json()).error || "falha no login"; }
  });
  const session = await (await fetch("/api/session", { credentials: "same-origin" })).json();
  if (session.authenticated) await start(); else showLogin();
});
