const els = {
  statusText: document.querySelector("#statusText"),
  runtimePlatform: document.querySelector("#runtimePlatform"),
  runtimeMode: document.querySelector("#runtimeMode"),
  runtimePid: document.querySelector("#runtimePid"),
  loginPid: document.querySelector("#loginPid"),
  lastError: document.querySelector("#lastError"),
  jdEnabled: document.querySelector("#jdEnabled"),
  djiEnabled: document.querySelector("#djiEnabled"),
  dryRun: document.querySelector("#dryRun"),
  autoSubmit: document.querySelector("#autoSubmit"),
  jdUrl: document.querySelector("#jdUrl"),
  djiUrl: document.querySelector("#djiUrl"),
  saleTime: document.querySelector("#saleTime"),
  maxTotal: document.querySelector("#maxTotal"),
  keywords: document.querySelector("#keywords"),
  slowMo: document.querySelector("#slowMo"),
  headless: document.querySelector("#headless"),
  saveConfig: document.querySelector("#saveConfig"),
  loginJd: document.querySelector("#loginJd"),
  loginDji: document.querySelector("#loginDji"),
  confirmLogin: document.querySelector("#confirmLogin"),
  startMonitor: document.querySelector("#startMonitor"),
  stopMonitor: document.querySelector("#stopMonitor"),
  logs: document.querySelector("#logs"),
  autoScroll: document.querySelector("#autoScroll"),
  latestScreenshot: document.querySelector("#latestScreenshot"),
  screenshotLink: document.querySelector("#screenshotLink"),
  screenshotTime: document.querySelector("#screenshotTime"),
  confirmDialog: document.querySelector("#confirmDialog"),
  confirmSummary: document.querySelector("#confirmSummary"),
  confirmText: document.querySelector("#confirmText"),
  confirmLiveStart: document.querySelector("#confirmLiveStart"),
  toast: document.querySelector("#toast"),
};

let currentConfig = null;

function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  setTimeout(() => els.toast.classList.remove("show"), 2600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch (_err) {
      // Keep response.statusText.
    }
    throw new Error(detail);
  }
  return response.json();
}

function keywordText(value) {
  return Array.isArray(value) ? value.join(",") : String(value || "");
}

function keywordList() {
  return els.keywords.value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

async function loadConfig() {
  currentConfig = await api("/api/config");
  const jd = currentConfig.platforms.jd;
  const dji = currentConfig.platforms.dji;
  els.jdUrl.value = jd.product_url || "";
  els.djiUrl.value = dji.product_url || "";
  els.saleTime.value = jd.sale_time || currentConfig.default_sale_time;
  els.maxTotal.value = jd.max_order_total_cny || dji.max_order_total_cny || "";
  els.keywords.value = keywordText(jd.require_order_keywords || jd.product_required_keywords);
  els.autoSubmit.checked = Boolean(jd.auto_submit_order && dji.auto_submit_order);
  els.dryRun.checked = Boolean(jd.dry_run && dji.dry_run);
  els.headless.checked = Boolean(jd.headless || dji.headless);
  els.slowMo.value = jd.slow_mo || dji.slow_mo || 50;
}

async function saveConfig() {
  const keywords = keywordList();
  const common = {
    sale_time: els.saleTime.value.trim(),
    dry_run: els.dryRun.checked,
    auto_submit_order: els.autoSubmit.checked,
    require_order_keywords: keywords,
    product_required_keywords: keywords,
    max_order_total_cny: els.maxTotal.value ? Number(els.maxTotal.value) : null,
    headless: els.headless.checked,
    slow_mo: Number(els.slowMo.value || 50),
  };
  await api("/api/config", {
    method: "POST",
    body: JSON.stringify({
      platforms: {
        jd: { ...common, product_url: els.jdUrl.value.trim() },
        dji: { ...common, product_url: els.djiUrl.value.trim() },
      },
    }),
  });
  toast("配置已保存");
  await loadConfig();
}

function appendLogs(lines) {
  const wasNearBottom = els.logs.scrollTop + els.logs.clientHeight >= els.logs.scrollHeight - 40;
  for (const line of lines) {
    els.logs.textContent += `${line}\n`;
  }
  if (els.autoScroll.checked && wasNearBottom) {
    els.logs.scrollTop = els.logs.scrollHeight;
  }
}

async function loadLogs() {
  const data = await api("/api/logs");
  els.logs.textContent = `${data.logs.join("\n")}\n`;
  if (els.autoScroll.checked) {
    els.logs.scrollTop = els.logs.scrollHeight;
  }
}

function connectLogs() {
  const stream = new EventSource("/api/logs/stream");
  stream.onmessage = (event) => {
    appendLogs([JSON.parse(event.data)]);
  };
  stream.onerror = () => {
    appendLogs(["[console] 日志流断开，浏览器会自动重连"]);
  };
}

async function refreshStatus() {
  const status = await api("/api/status");
  els.statusText.textContent = status.status || "等待";
  els.runtimePlatform.textContent = status.platform || "-";
  els.runtimeMode.textContent = status.pid ? (status.dry_run ? "Dry-Run" : "实战") : "-";
  els.runtimePid.textContent = status.pid || "-";
  els.loginPid.textContent = status.login_check_pid || "-";
  els.lastError.textContent = status.last_error || "";
}

async function refreshScreenshot() {
  try {
    const meta = await api("/api/screenshot/latest-meta");
    if (!meta.exists) {
      els.screenshotTime.textContent = "暂无截图";
      els.latestScreenshot.removeAttribute("src");
      return;
    }
    const url = `/api/screenshot/latest?t=${Date.now()}`;
    els.latestScreenshot.src = url;
    els.screenshotLink.href = url;
    els.screenshotTime.textContent = meta.mtime_text || meta.name;
  } catch (_err) {
    els.screenshotTime.textContent = "暂无截图";
  }
}

async function loginCheck(platform) {
  await api("/api/login-check", {
    method: "POST",
    body: JSON.stringify({ platform }),
  });
  toast("登录检查浏览器已打开");
  await refreshStatus();
}

async function confirmLoginCheck() {
  await api("/api/login-check/confirm", { method: "POST", body: "{}" });
  toast("已发送 Enter");
  await refreshStatus();
}

function startPayload(confirmText = "") {
  return {
    jd_enabled: els.jdEnabled.checked,
    dji_enabled: els.djiEnabled.checked,
    dry_run: els.dryRun.checked,
    confirm_text: confirmText,
  };
}

function liveSummary() {
  return [
    `京东启用：${els.jdEnabled.checked ? "是" : "否"}`,
    `DJI 启用：${els.djiEnabled.checked ? "是" : "否"}`,
    `京东链接：${els.jdUrl.value}`,
    `DJI 链接：${els.djiUrl.value}`,
    `开售时间：${els.saleTime.value}`,
    `金额上限：${els.maxTotal.value}`,
    `关键词：${els.keywords.value}`,
    `自动提交：${els.autoSubmit.checked ? "是" : "否"}`,
  ].join("\n");
}

async function startMonitor(confirmText = "") {
  await saveConfig();
  await api("/api/start", {
    method: "POST",
    body: JSON.stringify(startPayload(confirmText)),
  });
  toast("监控已启动");
  await refreshStatus();
}

async function stopMonitor() {
  await api("/api/stop", { method: "POST", body: "{}" });
  toast("监控已停止");
  await refreshStatus();
}

els.saveConfig.addEventListener("click", () => saveConfig().catch((err) => toast(err.message)));
els.loginJd.addEventListener("click", () => loginCheck("jd").catch((err) => toast(err.message)));
els.loginDji.addEventListener("click", () => loginCheck("dji").catch((err) => toast(err.message)));
els.confirmLogin.addEventListener("click", () => confirmLoginCheck().catch((err) => toast(err.message)));
els.stopMonitor.addEventListener("click", () => stopMonitor().catch((err) => toast(err.message)));

els.startMonitor.addEventListener("click", async () => {
  if (els.dryRun.checked) {
    startMonitor().catch((err) => toast(err.message));
    return;
  }
  els.confirmSummary.textContent = liveSummary();
  els.confirmText.value = "";
  els.confirmDialog.showModal();
});

els.confirmLiveStart.addEventListener("click", async () => {
  try {
    if (els.confirmText.value !== "我确认") {
      toast("请输入“我确认”");
      return;
    }
    await startMonitor(els.confirmText.value);
    els.confirmDialog.close();
  } catch (err) {
    toast(err.message);
  }
});

async function boot() {
  await loadConfig();
  await loadLogs();
  await refreshStatus();
  await refreshScreenshot();
  connectLogs();
  setInterval(() => refreshStatus().catch(() => {}), 2000);
  setInterval(() => refreshScreenshot().catch(() => {}), 2500);
}

boot().catch((err) => toast(err.message));

