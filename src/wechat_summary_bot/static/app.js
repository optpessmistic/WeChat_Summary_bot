const state = {
  settings: null,
  conversations: [],
  reports: [],
  jobs: [],
  upstreamTargets: [],
  selectedUpload: null,
  selectedReport: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const titles = { data: "数据资料库", analyze: "创建分析", reports: "历史报告", settings: "连接设置" };
let analysisPreviewTimer = null;
let analysisPreviewSequence = 0;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(message, error = false) {
  const item = document.createElement("div");
  item.className = `toast${error ? " is-error" : ""}`;
  item.textContent = message;
  $("#toast-region").append(item);
  setTimeout(() => item.remove(), 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let payload = {};
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) throw new Error(payload.detail || `请求失败（${response.status}）`);
  return payload;
}

function showView(view) {
  $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.toggle("is-active", item.id === `view-${view}`));
  $("#view-title").textContent = titles[view];
  if (view === "reports") renderReportList();
  if (view === "analyze") syncAnalysisSelection();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function formatDate(timestamp, withTime = false) {
  if (!timestamp) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(new Date(timestamp * 1000));
}

function toDateInput(timestamp) {
  if (!timestamp) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date(timestamp * 1000));
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${map.year}-${map.month}-${map.day}`;
}

async function loadAll() {
  const [settings, conversations, reports, jobs] = await Promise.all([
    api("/api/settings"), api("/api/conversations"), api("/api/reports"), api("/api/jobs"),
  ]);
  state.settings = settings;
  state.conversations = conversations.conversations || [];
  state.reports = reports.reports || [];
  state.jobs = jobs.jobs || [];
  renderSettings();
  renderConversations();
  renderAnalysisOptions();
  renderReportList();
  renderJobs();
}

function renderConversations() {
  const query = $("#library-search").value.trim().toLowerCase();
  const filtered = state.conversations.filter((item) =>
    `${item.display_name} ${item.username}`.toLowerCase().includes(query)
  );
  $("#hero-conversation-count").textContent = state.conversations.length.toLocaleString();
  $("#hero-message-count").textContent = state.conversations
    .reduce((total, item) => total + Number(item.message_count || 0), 0).toLocaleString();
  if (!filtered.length) {
    $("#conversation-grid").innerHTML = '<div class="empty-state">还没有匹配的本地会话。连接上游或上传导出文件后，会话会出现在这里。</div>';
    return;
  }
  $("#conversation-grid").innerHTML = filtered.map((item) => `
    <article class="conversation-card" data-conversation="${escapeHtml(item.id)}" tabindex="0">
      <div class="conversation-card-head">
        <h3 title="${escapeHtml(item.display_name)}">${escapeHtml(item.display_name)}</h3>
        <span class="type-badge">${item.is_group ? "群聊" : "单聊"}</span>
      </div>
      <dl><div><dt>消息</dt><dd>${Number(item.message_count).toLocaleString()}</dd></div>
      <div><dt>成员</dt><dd>${Number(item.participant_count).toLocaleString()}</dd></div></dl>
      <footer><span>${escapeHtml(formatDate(item.first_message_at))} — ${escapeHtml(formatDate(item.last_message_at))}</span>
      <button class="card-delete" data-delete="${escapeHtml(item.id)}" aria-label="删除会话">删除</button></footer>
    </article>`).join("");
  $$("[data-conversation]").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.closest("[data-delete]")) return;
      $("#analysis-conversation").value = card.dataset.conversation;
      showView("analyze");
    });
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter") card.click();
    });
  });
  $$("[data-delete]").forEach((button) => button.addEventListener("click", deleteConversation));
}

async function deleteConversation(event) {
  const id = event.currentTarget.dataset.delete;
  const item = state.conversations.find((value) => value.id === id);
  if (!confirm(`确定删除“${item?.display_name || "该会话"}”及其所有消息和报告吗？此操作无法撤销。`)) return;
  try {
    await api(`/api/conversations/${id}`, { method: "DELETE" });
    toast("本地会话已删除");
    await loadAll();
  } catch (error) { toast(error.message, true); }
}

function renderAnalysisOptions() {
  const select = $("#analysis-conversation");
  const previous = select.value;
  select.innerHTML = state.conversations.length
    ? state.conversations.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.display_name)} · ${item.is_group ? "群聊" : "单聊"}</option>`).join("")
    : '<option value="">请先导入会话</option>';
  if (state.conversations.some((item) => item.id === previous)) select.value = previous;
  const providers = state.settings?.providers || [];
  $("#analysis-provider").innerHTML = providers.length
    ? providers.map((item) => `<option value="${escapeHtml(item.id)}" ${item.is_default ? "selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml(item.model)}</option>`).join("")
    : '<option value="">请先配置 AI 服务</option>';
  syncAnalysisSelection();
}

async function syncAnalysisSelection() {
  const id = $("#analysis-conversation").value;
  const summary = state.conversations.find((item) => item.id === id);
  if (!summary) {
    $("#analysis-context-name").textContent = "尚未选择会话";
    $("#context-messages").textContent = "—";
    $("#context-members").textContent = "—";
    $("#context-tokens").textContent = "—";
    $("#context-chunks").textContent = "—";
    $("#context-range").textContent = "—";
    return;
  }
  $("#analysis-context-name").textContent = summary.display_name;
  $("#context-messages").textContent = Number(summary.message_count).toLocaleString();
  $("#context-members").textContent = Number(summary.participant_count).toLocaleString();
  $("#context-range").textContent = `${formatDate(summary.first_message_at)} 至 ${formatDate(summary.last_message_at)}`;
  if (!$("#analysis-start-date").value) $("#analysis-start-date").value = toDateInput(summary.first_message_at);
  if (!$("#analysis-end-date").value) $("#analysis-end-date").value = toDateInput(summary.last_message_at);
  try {
    const detail = await api(`/api/conversations/${id}`);
    $("#analysis-member").innerHTML = (detail.participants || [])
      .map((item) => `<option value="${escapeHtml(item.username)}">${escapeHtml(item.display_name)} · ${Number(item.message_count).toLocaleString()} 条</option>`).join("");
    const memberRadio = $('input[name="analysis-mode"][value="member"]');
    memberRadio.disabled = !detail.is_group;
    if (!detail.is_group && memberRadio.checked) {
      $('input[name="analysis-mode"][value="conversation"]').checked = true;
    }
    syncMode();
    scheduleAnalysisPreview();
  } catch (error) { toast(error.message, true); }
}

function syncMode() {
  const mode = $('input[name="analysis-mode"]:checked').value;
  $("#member-field").classList.toggle("is-hidden", mode !== "member");
  scheduleAnalysisPreview();
}

function analysisRequestBody() {
  const mode = $('input[name="analysis-mode"]:checked').value;
  return {
    conversation_id: $("#analysis-conversation").value,
    mode,
    member_username: mode === "member" ? $("#analysis-member").value : null,
    start_date: $("#analysis-start-date").value || null,
    end_date: $("#analysis-end-date").value || null,
    focus: $("#analysis-focus").value,
    provider_id: $("#analysis-provider").value,
    pseudonymize: $("#analysis-pseudonymize").checked,
  };
}

function scheduleAnalysisPreview() {
  clearTimeout(analysisPreviewTimer);
  analysisPreviewTimer = setTimeout(previewAnalysis, 260);
}

async function previewAnalysis() {
  const body = analysisRequestBody();
  if (!body.conversation_id || !body.provider_id || (body.mode === "member" && !body.member_username)) {
    $("#context-tokens").textContent = "—";
    $("#context-chunks").textContent = "—";
    return;
  }
  const sequence = ++analysisPreviewSequence;
  $("#context-tokens").textContent = "估算中";
  try {
    const preview = await api("/api/analyses/preview", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (sequence !== analysisPreviewSequence) return;
    $("#context-messages").textContent = Number(preview.selected_message_count || 0).toLocaleString();
    $("#context-tokens").textContent = `≈ ${Number(preview.estimated_input_tokens || 0).toLocaleString()}`;
    $("#context-chunks").textContent = Number(preview.chunk_count || 0).toLocaleString();
  } catch (error) {
    if (sequence !== analysisPreviewSequence) return;
    $("#context-tokens").textContent = "不可估算";
    $("#context-chunks").textContent = "—";
  }
}

async function submitAnalysis(event) {
  event.preventDefault();
  const body = analysisRequestBody();
  try {
    const result = await api("/api/analyses", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    toast("分析任务已开始");
    watchJob(result.job_id);
    openJobDrawer();
  } catch (error) { toast(error.message, true); }
}

async function connectUpstream() {
  const pill = $("#upstream-pill");
  pill.textContent = "正在连接";
  pill.className = "connection-pill";
  try {
    const accounts = await api("/api/upstream/accounts");
    const items = accounts.items || accounts.accountInfos || (accounts.accounts || []).map((name) => ({ account: name, name }));
    if (!items.length) throw new Error(accounts.message || "上游没有可用账号");
    $("#upstream-account").innerHTML = items.map((item) =>
      `<option value="${escapeHtml(item.account || item.name)}">${escapeHtml(item.name || item.account)}${item.realtimeAvailable ? " · 实时" : ""}</option>`
    ).join("");
    pill.textContent = "连接正常";
    pill.className = "connection-pill is-ok";
    $("#upstream-detail").textContent = `检测到 ${items.length} 个账号`;
    $("#upstream-import-form").classList.remove("is-hidden");
    await loadTargets();
  } catch (error) {
    pill.textContent = "连接失败";
    pill.className = "connection-pill is-error";
    $("#upstream-detail").textContent = error.message;
    toast(error.message, true);
  }
}

async function loadTargets() {
  try {
    const payload = await api(`/api/upstream/targets?account=${encodeURIComponent($("#upstream-account").value)}`);
    state.upstreamTargets = payload.targets || [];
    renderTargets();
  } catch (error) { toast(error.message, true); }
}

function renderTargets() {
  const query = $("#target-search").value.trim().toLowerCase();
  const targets = state.upstreamTargets.filter((item) =>
    `${item.displayName || item.name} ${item.username}`.toLowerCase().includes(query)
  );
  $("#upstream-target").innerHTML = targets.map((item) =>
    `<option value="${escapeHtml(item.username)}">${item.isGroup ? "群聊" : "单聊"} · ${escapeHtml(item.displayName || item.name || item.username)}</option>`
  ).join("");
}

async function submitUpstreamImport(event) {
  event.preventDefault();
  try {
    const result = await api("/api/imports/upstream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account: $("#upstream-account").value,
        username: $("#upstream-target").value,
        start_date: $("#upstream-start-date").value || null,
        end_date: $("#upstream-end-date").value || null,
      }),
    });
    toast("已请求 WeChatDataAnalysis 导出");
    watchJob(result.job_id);
    openJobDrawer();
  } catch (error) { toast(error.message, true); }
}

function chooseUpload(file) {
  if (!file) return;
  if (!/\.(zip|json)$/i.test(file.name)) { toast("请选择 ZIP 或 JSON 文件", true); return; }
  state.selectedUpload = file;
  $("#upload-filename").textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
  $("#upload-file-row").classList.remove("is-hidden");
}

async function uploadFile() {
  if (!state.selectedUpload) return;
  const form = new FormData();
  form.append("file", state.selectedUpload);
  $("#upload-button").disabled = true;
  try {
    const result = await api("/api/imports/upload", { method: "POST", body: form });
    toast("文件已上传，正在本机导入");
    watchJob(result.job_id);
    openJobDrawer();
  } catch (error) { toast(error.message, true); }
  finally { $("#upload-button").disabled = false; }
}

function renderJobs() {
  $("#job-count").textContent = state.jobs.filter((item) => !["succeeded", "failed", "cancelled"].includes(item.status)).length;
  $("#job-list").innerHTML = state.jobs.length ? state.jobs.map((job) => `
    <article class="job-card">
      <div class="job-card-head"><strong>${job.job_type === "analysis" ? "AI 分析" : "数据导入"}</strong><span>${escapeHtml(job.status)}</span></div>
      <p>${escapeHtml(job.stage || "等待处理")}</p>
      <div class="progress-track"><i style="width:${Number(job.progress || 0)}%"></i></div>
      ${job.error ? `<p class="job-error">${escapeHtml(job.error)}</p>` : ""}
      ${!["succeeded", "failed", "cancelled"].includes(job.status) ? `<button class="quiet-button job-cancel" data-job="${escapeHtml(job.id)}">取消</button>` : ""}
    </article>`).join("") : '<div class="empty-state small">暂无后台任务。</div>';
  $$(".job-cancel").forEach((button) => button.addEventListener("click", async () => {
    try { await api(`/api/jobs/${button.dataset.job}`, { method: "DELETE" }); toast("正在取消任务"); }
    catch (error) { toast(error.message, true); }
  }));
}

function watchJob(jobId) {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  source.onmessage = async (event) => {
    const job = JSON.parse(event.data);
    const index = state.jobs.findIndex((item) => item.id === job.id);
    if (index >= 0) state.jobs[index] = job; else state.jobs.unshift(job);
    renderJobs();
    if (["succeeded", "failed", "cancelled"].includes(job.status)) {
      source.close();
      if (job.status === "succeeded") {
        toast(job.job_type === "analysis" ? "报告已生成" : "会话导入完成");
        await loadAll();
        if (job.result?.report_id) {
          showView("reports");
          await openReport(job.result.report_id);
        }
      } else if (job.status === "failed") toast(job.error || "任务失败", true);
    }
  };
  source.onerror = () => source.close();
}

function openJobDrawer() {
  $("#job-drawer").classList.add("is-open");
  $("#job-drawer").setAttribute("aria-hidden", "false");
  $("#drawer-backdrop").classList.add("is-open");
}
function closeJobDrawer() {
  $("#job-drawer").classList.remove("is-open");
  $("#job-drawer").setAttribute("aria-hidden", "true");
  $("#drawer-backdrop").classList.remove("is-open");
}

function renderReportList() {
  $("#report-list").innerHTML = state.reports.length ? state.reports.map((report) => `
    <button class="report-list-item ${state.selectedReport?.id === report.id ? "is-active" : ""}" data-report="${escapeHtml(report.id)}">
      <strong>${escapeHtml(report.conversation_name)}</strong>
      <small>${escapeHtml(formatDate(report.created_at, true))} · ${(Number(report.prompt_tokens) + Number(report.completion_tokens)).toLocaleString()} tokens</small>
    </button>`).join("") : '<div class="empty-state small">还没有报告。</div>';
  $$("[data-report]").forEach((button) => button.addEventListener("click", () => openReport(button.dataset.report)));
}

function evidenceButtons(ids, evidenceMap) {
  if (!ids?.length) return "";
  return `<span class="evidence-buttons">${ids.map((id) =>
    `<button class="evidence-button" data-evidence="${escapeHtml(id)}">证据 ${escapeHtml(id.slice(0, 8))}</button>`
  ).join("")}</span>`;
}

function reportItems(title, items, field, evidenceMap) {
  if (!items?.length) return "";
  return `<section class="report-section"><h3>${escapeHtml(title)}</h3>${items.map((item) => {
    const details = [
      item.time ? `时间：${item.time}` : "", item.owner ? `负责人：${item.owner}` : "",
      item.due ? `期限：${item.due}` : "", item.status ? `状态：${item.status}` : "",
    ].filter(Boolean).join(" · ");
    return `<div class="report-item">${item.title ? `<strong>${escapeHtml(item.title)}</strong>` : ""}
      <span>${escapeHtml(item[field] || item.text || "")}</span>${details ? `<small> · ${escapeHtml(details)}</small>` : ""}
      ${evidenceButtons(item.evidence_ids, evidenceMap)}</div>`;
  }).join("")}</section>`;
}

async function openReport(id) {
  try {
    const payload = await api(`/api/reports/${id}`);
    state.selectedReport = payload;
    renderReportList();
    const report = payload.report;
    const metadata = report.metadata || {};
    const evidenceMap = Object.fromEntries((report.evidence || []).map((item) => [item.id, item]));
    const member = report.member_analysis || {};
    $("#report-viewer").innerHTML = `
      <header class="report-header">
        <div><p class="section-kicker">STRUCTURED REPORT</p><h2>${escapeHtml(metadata.conversation_name || "聊天总结")}</h2>
        <p>${metadata.mode === "member" ? `成员分析 · ${escapeHtml(metadata.member)}` : "会话整体分析"} · ${escapeHtml(metadata.provider)} / ${escapeHtml(metadata.model)}</p></div>
        <div class="report-actions"><a href="/api/reports/${encodeURIComponent(id)}/download?format=markdown">Markdown</a><a href="/api/reports/${encodeURIComponent(id)}/download?format=json">JSON</a><button id="delete-report-button">删除</button></div>
      </header>
      <div class="report-body">
        <section class="report-section"><h3>总体概览</h3><p>${escapeHtml(report.overview?.text || "")}${evidenceButtons(report.overview?.evidence_ids, evidenceMap)}</p></section>
        ${reportItems("主要主题", report.topics, "summary", evidenceMap)}
        ${reportItems("关键结论", report.key_conclusions, "text", evidenceMap)}
        ${reportItems("决定", report.decisions, "text", evidenceMap)}
        ${reportItems("待办", report.action_items, "text", evidenceMap)}
        ${reportItems("未决问题", report.open_questions, "text", evidenceMap)}
        ${reportItems("时间线", report.timeline, "event", evidenceMap)}
        ${reportItems("成员主要观点", member.main_points, "text", evidenceMap)}
        ${reportItems("成员贡献", member.contributions, "text", evidenceMap)}
        ${reportItems("成员承诺", member.commitments, "text", evidenceMap)}
        ${reportItems("互动结果", member.interactions, "text", evidenceMap)}
        ${renderStatistics(report.participant_statistics)}
        ${(report.limitations || []).length ? `<section class="report-section"><h3>分析限制</h3><ul>${report.limitations.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>` : ""}
      </div>`;
    $$("[data-evidence]").forEach((button) => button.addEventListener("click", () => showEvidence(evidenceMap[button.dataset.evidence])));
    $("#delete-report-button").addEventListener("click", () => deleteReport(id));
  } catch (error) { toast(error.message, true); }
}

async function deleteReport(id) {
  if (!confirm("确定删除这份本地报告吗？聊天资料不会被删除。")) return;
  try {
    await api(`/api/reports/${id}`, { method: "DELETE" });
    state.selectedReport = null;
    $("#report-viewer").innerHTML = '<div class="report-placeholder"><span>⌁</span><h3>选择一份报告</h3><p>结构化结论与原文证据将在这里并排呈现。</p></div>';
    toast("报告已删除");
    await loadAll();
  } catch (error) { toast(error.message, true); }
}

function renderStatistics(stats = {}) {
  return `<section class="report-section"><h3>参与统计</h3>
    <p>共 ${Number(stats.message_count || 0).toLocaleString()} 条消息，${Number(stats.active_days || 0).toLocaleString()} 个活跃日；进入模型上下文 ${Number(stats.selected_message_count || 0).toLocaleString()} 条。</p>
    <table class="stat-table">${(stats.participants || []).map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${Number(item.message_count).toLocaleString()} 条</td></tr>`).join("")}</table>
  </section>`;
}

function showEvidence(item) {
  if (!item) return;
  $("#evidence-title").textContent = `证据 ${item.id.slice(0, 8)}`;
  $("#evidence-meta").textContent = `${item.time} · ${item.sender} · ${item.type}`;
  $("#evidence-content").textContent = item.excerpt;
  $("#evidence-dialog").showModal();
}

function renderSettings() {
  $("#setting-upstream-url").value = state.settings?.upstream_url || "http://127.0.0.1:10392";
  const providers = state.settings?.providers || [];
  $("#provider-list").innerHTML = providers.map((provider) => `
    <div class="provider-row">
      <div><strong>${escapeHtml(provider.name)}${provider.is_default ? " · 默认" : ""}</strong>
      <small>${escapeHtml(provider.model)} · ${provider.has_api_key || provider.kind === "ollama" ? "可用凭据" : "未设置密钥"}</small></div>
      <div class="provider-actions"><button data-provider-edit="${escapeHtml(provider.id)}">编辑</button><button data-provider-test="${escapeHtml(provider.id)}">测试</button><button data-provider-delete="${escapeHtml(provider.id)}">删除</button></div>
    </div>`).join("");
  $$("[data-provider-edit]").forEach((button) => button.addEventListener("click", () => editProvider(button.dataset.providerEdit)));
  $$("[data-provider-test]").forEach((button) => button.addEventListener("click", () => testProvider(button.dataset.providerTest)));
  $$("[data-provider-delete]").forEach((button) => button.addEventListener("click", () => deleteProvider(button.dataset.providerDelete)));
}

function resetProvider() {
  $("#provider-form").reset();
  $("#provider-id").value = "";
  $("#provider-kind").value = "openai_compatible";
  $("#provider-context").value = "32000";
}
function editProvider(id) {
  const provider = state.settings.providers.find((item) => item.id === id);
  if (!provider) return;
  $("#provider-id").value = provider.id;
  $("#provider-name").value = provider.name;
  $("#provider-kind").value = provider.kind;
  $("#provider-base-url").value = provider.base_url;
  $("#provider-model").value = provider.model;
  $("#provider-context").value = provider.max_context_tokens;
  $("#provider-default").checked = provider.is_default;
  $("#provider-api-key").value = "";
}
async function saveProvider(event) {
  event.preventDefault();
  const body = {
    id: $("#provider-id").value || null, name: $("#provider-name").value,
    kind: $("#provider-kind").value, base_url: $("#provider-base-url").value,
    model: $("#provider-model").value, max_context_tokens: Number($("#provider-context").value),
    is_default: $("#provider-default").checked,
  };
  if ($("#provider-api-key").value) body.api_key = $("#provider-api-key").value;
  try {
    const result = await api("/api/providers", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    toast(result.secret_persisted === false ? "已保存；当前系统凭据库不可用，密钥只在本次运行有效" : "AI 服务已保存");
    resetProvider(); await loadAll();
  } catch (error) { toast(error.message, true); }
}
async function testProvider(id) {
  toast("正在测试模型连接");
  try { await api(`/api/providers/${id}/test`, { method: "POST" }); toast("模型连接正常"); }
  catch (error) { toast(error.message, true); }
}
async function deleteProvider(id) {
  if (!confirm("确定删除该 AI 服务配置和系统凭据吗？")) return;
  try { await api(`/api/providers/${id}`, { method: "DELETE" }); toast("AI 服务已删除"); await loadAll(); }
  catch (error) { toast(error.message, true); }
}

function bindEvents() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => showView(item.dataset.view)));
  $("#refresh-button").addEventListener("click", () => loadAll().then(() => toast("已刷新")));
  $("#library-search").addEventListener("input", renderConversations);
  $("#analysis-conversation").addEventListener("change", syncAnalysisSelection);
  $$('input[name="analysis-mode"]').forEach((input) => input.addEventListener("change", syncMode));
  ["analysis-member", "analysis-start-date", "analysis-end-date", "analysis-provider", "analysis-pseudonymize"]
    .forEach((id) => $(`#${id}`).addEventListener("change", scheduleAnalysisPreview));
  $("#analysis-form").addEventListener("submit", submitAnalysis);
  $("#connect-upstream").addEventListener("click", connectUpstream);
  $("#upstream-account").addEventListener("change", loadTargets);
  $("#target-search").addEventListener("input", renderTargets);
  $("#upstream-import-form").addEventListener("submit", submitUpstreamImport);
  $("#upload-input").addEventListener("change", (event) => chooseUpload(event.target.files[0]));
  $("#upload-button").addEventListener("click", uploadFile);
  const dropzone = $("#dropzone");
  ["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.add("is-dragging"); }));
  ["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.remove("is-dragging"); }));
  dropzone.addEventListener("drop", (event) => chooseUpload(event.dataTransfer.files[0]));
  $("#job-indicator").addEventListener("click", openJobDrawer);
  $("#close-job-drawer").addEventListener("click", closeJobDrawer);
  $("#drawer-backdrop").addEventListener("click", closeJobDrawer);
  $("#evidence-close").addEventListener("click", () => $("#evidence-dialog").close());
  $("#upstream-setting-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/settings/upstream", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: $("#setting-upstream-url").value }),
      });
      toast("上游地址已保存"); await loadAll();
    } catch (error) { toast(error.message, true); }
  });
  $("#provider-form").addEventListener("submit", saveProvider);
  $("#provider-reset").addEventListener("click", resetProvider);
}

bindEvents();
loadAll().catch((error) => toast(error.message, true));
