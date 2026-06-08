function $(id) {
  return document.getElementById(id);
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

async function runHealthCheck() {
  const button = $("runHealth");
  const grid = $("healthGrid");
  if (!button || !grid) return;
  button.disabled = true;
  button.textContent = "自检中...";
  grid.innerHTML = `<article class="health-empty surface-card">正在检查设备，请稍候...</article>`;
  try {
    const response = await fetch("/api/health?mode=full");
    const data = await response.json();
    renderHealth(data);
  } catch (error) {
    grid.innerHTML = `<article class="health-empty surface-card fail">自检请求失败：${escapeHtml(error?.message || "unknown")}</article>`;
  } finally {
    button.disabled = false;
    button.textContent = "一键自检";
  }
}

function renderHealth(data) {
  const items = data.items || [];
  const counts = {
    pass: items.filter((item) => item.status === "pass").length,
    warning: items.filter((item) => item.status === "warning").length,
    fail: items.filter((item) => item.status === "fail").length,
  };
  if ($("passCount")) $("passCount").textContent = counts.pass;
  if ($("warningCount")) $("warningCount").textContent = counts.warning;
  if ($("failCount")) $("failCount").textContent = counts.fail;
  if ($("healthTime")) $("healthTime").textContent = data.time ? `检测时间：${data.time}` : "检测完成";
  if ($("overallText")) $("overallText").textContent = overallText(data.overall);
  if ($("overallCard")) $("overallCard").className = `summary-card overall ${data.overall || "warning"}`;

  const grid = $("healthGrid");
  grid.innerHTML = items.map((item) => `
    <article class="health-card surface-card ${escapeHtml(item.status)}">
      <div class="health-card-head">
        <div>
          <span class="health-module">${escapeHtml(item.label)}</span>
          <h2>${escapeHtml(item.name)}</h2>
        </div>
        <strong>${escapeHtml(statusText(item.status))}</strong>
      </div>
      <p>${escapeHtml(item.message)}</p>
      ${renderDetails(item.details)}
    </article>
  `).join("");
}

function renderDetails(details) {
  const entries = Object.entries(details || {}).filter(([, value]) => typeof value !== "object");
  if (!entries.length) return "";
  return `<dl class="health-details">
    ${entries.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}
  </dl>`;
}

function statusText(status) {
  return {
    pass: "正常",
    warning: "警告",
    fail: "异常",
  }[status] || "--";
}

function overallText(status) {
  return {
    pass: "全部正常",
    warning: "可降级使用",
    fail: "存在异常",
  }[status] || "未检测";
}

if ($("runHealth")) {
  $("runHealth").addEventListener("click", runHealthCheck);
}
