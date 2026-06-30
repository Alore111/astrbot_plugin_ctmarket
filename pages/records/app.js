// 插件 Pages 通过 bridge 与后端交互：
// - bridge.apiGet("records") -> GET /api/plug/<plugin_name>/records
// - bridge.apiGet("groups")  -> GET /api/plug/<plugin_name>/groups
const bridge = window.AstrBotPluginPage;

const elGroupSelect = document.getElementById("groupSelect");
const elQInput = document.getElementById("qInput");
const elRefresh = document.getElementById("refreshBtn");
const elPrev = document.getElementById("prevBtn");
const elNext = document.getElementById("nextBtn");
const elPageInfo = document.getElementById("pageInfo");
const elSummary = document.getElementById("summary");
const elTbody = document.getElementById("tbody");

let state = {
  limit: 50,
  offset: 0,
  group_id: "",
  q: "",
  total: 0,
};

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatTs(ts) {
  const n = Number(ts || 0);
  if (!Number.isFinite(n) || n <= 0) return "-";
  const ms = n > 1e12 ? n : n * 1000;
  const d = new Date(ms);
  const pad = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}:${pad(d.getSeconds())}`;
}

function renderRows(items) {
  if (!items || items.length === 0) {
    elTbody.innerHTML = `<tr><td class="muted" colspan="5">暂无记录</td></tr>`;
    return;
  }
  elTbody.innerHTML = items
    .map((i) => {
      const time = formatTs(i.created_at);
      const groupId = escapeHtml(i.group_id);
      const sender = escapeHtml(i.sender_name || i.sender_id || "-");
      const msg = escapeHtml(i.message_str || "");
      const rule = escapeHtml(i.rule_name || "-");
      return `<tr>
        <td class="muted mono">${time}</td>
        <td class="mono">${groupId}</td>
        <td>${sender}</td>
        <td class="msg">${msg}</td>
        <td class="mono muted">${rule}</td>
      </tr>`;
    })
    .join("");
}

function syncMeta() {
  const page = Math.floor(state.offset / state.limit) + 1;
  const pageCount = Math.max(1, Math.ceil(state.total / state.limit));
  elPageInfo.textContent = `第 ${page} / ${pageCount} 页`;
  elSummary.textContent = `共 ${state.total} 条`;
  elPrev.disabled = state.offset <= 0;
  elNext.disabled = state.offset + state.limit >= state.total;
}

async function loadGroups() {
  const resp = await bridge.apiGet("groups");
  const items = Array.isArray(resp?.items) ? resp.items : [];
  const options = [
    { value: "", label: "全部群" },
    ...items.map((g) => ({ value: String(g.group_id || ""), label: String(g.group_id || "") })),
  ];
  elGroupSelect.innerHTML = options
    .map((o) => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`)
    .join("");
  elGroupSelect.value = state.group_id;
}

async function loadRecords() {
  elRefresh.disabled = true;
  try {
    const resp = await bridge.apiGet("records", {
      limit: state.limit,
      offset: state.offset,
      group_id: state.group_id || undefined,
      q: state.q || undefined,
    });
    state.total = Number(resp?.total || 0);
    renderRows(resp?.items || []);
    syncMeta();
  } catch (e) {
    state.total = 0;
    elTbody.innerHTML = `<tr><td class="muted" colspan="5">加载失败：${escapeHtml(
      e?.message || String(e)
    )}</td></tr>`;
    syncMeta();
  } finally {
    elRefresh.disabled = false;
  }
}

function resetAndLoad() {
  state.offset = 0;
  loadRecords();
}

elRefresh.addEventListener("click", () => resetAndLoad());
elGroupSelect.addEventListener("change", () => {
  state.group_id = elGroupSelect.value;
  resetAndLoad();
});
elQInput.addEventListener("change", () => {
  state.q = elQInput.value.trim();
  resetAndLoad();
});
elQInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    state.q = elQInput.value.trim();
    resetAndLoad();
  }
});
elPrev.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadRecords();
});
elNext.addEventListener("click", () => {
  state.offset = state.offset + state.limit;
  loadRecords();
});

await bridge.ready();
await loadGroups();
await loadRecords();
