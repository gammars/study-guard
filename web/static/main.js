const role = document.body.dataset.role || "student";
let trendRange = "30m";
let speechUnlocked = false;
let speechInitialized = false;
let ttsPlaying = false;
let currentTtsAudio = null;
let voiceRecorder = null;
let voiceEventsInitialized = false;
let lastVoiceEventId = 0;
let handlingVoiceEvents = false;
const spokenMessages = new Set();
const ttsQueue = [];

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

async function postJson(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

async function runAction(action) {
  if (action === "settings") {
    await openSettingsPanel();
    return;
  }
  if (action === "report") {
    await openReportPanel();
    return;
  }
  const payload = { action };
  if (action === "start") {
    const minutes = prompt("目标学习分钟数", "1");
    payload.target_minutes = Number(minutes || 1);
  }
  if (action === "remind") {
    const message = prompt("发送给学生的提醒", "家长提醒：请保持专注，注意坐姿。");
    if (message === null) return;
    payload.message = message.trim() || "家长提醒：请保持专注，注意坐姿。";
  }
  const data = await postJson("/api/action", payload);
  if (role === "student" && data.result?.report_text) {
    renderStudentChat("", data.result.report_text);
  } else if (data.result?.report_text && $("replyText")) {
    $("replyText").textContent = data.result.report_text;
  }
  await refreshState();
}

async function openReportPanel() {
  const modal = $("reportModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  if ($("reportLoading")) {
    $("reportLoading").classList.remove("hidden");
    $("reportLoading").textContent = "正在整理今日学习数据...";
  }
  if ($("reportContent")) $("reportContent").classList.add("hidden");
  if ($("reportSubtitle")) $("reportSubtitle").textContent = "正在生成...";
  try {
    const data = await fetch(`/api/report/ui?role=${encodeURIComponent(role)}`).then((res) => res.json());
    if (!data.success) throw new Error(data.error || "生成日报失败");
    renderReport(data);
    loadReportAdvice(data);
  } catch (error) {
    if ($("reportLoading")) $("reportLoading").textContent = `日报生成失败：${error?.message || "unknown"}`;
  }
}

function closeReportPanel() {
  if ($("reportModal")) $("reportModal").classList.add("hidden");
}

function renderReport(data) {
  const summary = data.summary || {};
  const env = data.environment || {};
  if ($("reportLoading")) $("reportLoading").classList.add("hidden");
  if ($("reportContent")) $("reportContent").classList.remove("hidden");
  if ($("reportSubtitle")) $("reportSubtitle").textContent = `${data.date || "--"} 生成于 ${timeOnly(data.generated_at)}`;
  if ($("reportFocusScore")) $("reportFocusScore").textContent = `${summary.focus_score ?? 0}`;
  if ($("reportTotalStudy")) $("reportTotalStudy").textContent = summary.total_study_text || "--";
  if ($("reportEffectiveStudy")) $("reportEffectiveStudy").textContent = summary.effective_study_text || "--";
  if ($("reportAwayCount")) $("reportAwayCount").textContent = `${summary.away_count ?? 0} 次`;
  if ($("reportLongestAway")) $("reportLongestAway").textContent = summary.longest_away_text || "--";
  if ($("reportAlertCount")) $("reportAlertCount").textContent = `${summary.alert_count ?? 0} 次`;
  if ($("reportEnvironment")) {
    $("reportEnvironment").innerHTML = `${escapeHtml(env.average_text || "暂无环境均值")}<small>最近 ${escapeHtml(env.recent_text || "暂无最近环境")}</small>`;
  }
  if ($("reportAdvice")) $("reportAdvice").textContent = data.advice || "正在调用大模型生成个性化建议...";
  renderReportEvents(data.abnormal_events?.items || []);
}

async function loadReportAdvice(report) {
  try {
    const data = await postJson("/api/report/advice", { role, report });
    if ($("reportAdvice")) $("reportAdvice").textContent = data.advice || "暂无建议";
  } catch (error) {
    if ($("reportAdvice")) {
      $("reportAdvice").textContent = `AI 建议生成失败：${error?.message || "unknown"}。可先查看上方结构化学习数据。`;
    }
  }
}

function renderReportEvents(items) {
  const target = $("reportEvents");
  if (!target) return;
  target.innerHTML = (items || []).slice(0, 8).map((item) => `
    <article class="report-event ${escapeHtml(item.severity || "medium")}">
      <div>
        <strong>${escapeHtml(item.title || "--")}</strong>
        <p>${escapeHtml(item.summary || "暂无详情")}</p>
      </div>
      <span>${escapeHtml(item.status || "--")}</span>
      <time>${escapeHtml(item.time_label || timeOnly(item.time))}</time>
    </article>
  `).join("") || `<div class="report-empty">今日暂无异常事件</div>`;
}

async function openSettingsPanel() {
  const modal = $("settingsModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  await loadSettings();
}

function closeSettingsPanel() {
  if ($("settingsModal")) $("settingsModal").classList.add("hidden");
}

async function loadSettings() {
  const data = await fetch("/api/settings").then((res) => res.json());
  if ($("settingStudyMinutes")) $("settingStudyMinutes").value = data.default_target_minutes ?? "";
  if ($("settingBreakSeconds")) $("settingBreakSeconds").value = data.default_break_seconds ?? "";
  if ($("settingDistanceThreshold")) $("settingDistanceThreshold").value = data.distance_threshold_cm ?? "";
  if ($("settingYoloThreshold")) $("settingYoloThreshold").value = data.yolo_confidence_threshold ?? "";
  if ($("settingsPath")) $("settingsPath").textContent = `配置文件：${data.settings_path || "logs/settings.json"}`;
}

async function saveSettings() {
  const button = $("saveSettings");
  if (button) {
    button.disabled = true;
    button.textContent = "保存中...";
  }
  try {
    const payload = {
      default_target_minutes: Number($("settingStudyMinutes")?.value || 1),
      default_break_seconds: Number($("settingBreakSeconds")?.value || 30),
      distance_threshold_cm: Number($("settingDistanceThreshold")?.value || 40),
      yolo_confidence_threshold: Number($("settingYoloThreshold")?.value || 0.35),
    };
    const data = await postJson("/api/settings", payload);
    if (!data.success) throw new Error(data.error || "保存失败");
    await loadSettings();
    await refreshState();
    closeSettingsPanel();
  } catch (error) {
    alert(`参数保存失败：${error?.message || "unknown"}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "保存参数";
    }
  }
}

async function sendChat() {
  const input = $("chatInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  await sendChatText(text);
}

async function sendChatText(text) {
  const assistant = appendStreamingChat(text);
  await streamChat(text, assistant);
  await refreshState();
}

async function toggleVoiceInput() {
  const button = $("voiceInput");
  if (!button) return;
  if (voiceRecorder?.recording) {
    await stopVoiceRecording();
    return;
  }
  await startVoiceRecording();
}

async function startVoiceRecording() {
  const button = $("voiceInput");
  if (!button || voiceRecorder?.recording) return;
  try {
    voiceRecorder = await createWavRecorder();
    button.classList.add("recording");
    button.title = "停止录音";
  } catch (error) {
    alert(`无法开启麦克风：${error?.message || "请检查浏览器权限"}`);
  }
}

async function stopVoiceRecording() {
  const button = $("voiceInput");
  if (!button || !voiceRecorder?.recording) return;
  button.classList.add("transcribing");
  button.title = "正在识别";
  try {
    const audioBlob = await voiceRecorder.stop();
    voiceRecorder = null;
    const text = await transcribeAudio(audioBlob);
    if (text && $("chatInput")) {
      $("chatInput").value = text;
      $("chatInput").focus();
      button.title = "正在发送";
      await sendChat();
    }
  } catch (error) {
    alert(`语音识别失败：${error?.message || "unknown"}`);
  } finally {
    button.classList.remove("recording", "transcribing");
    button.title = "语音输入";
  }
}

async function transcribeAudio(audioBlob) {
  const formData = new FormData();
  formData.append("audio", audioBlob, "voice-input.wav");
  const res = await fetch("/api/asr", { method: "POST", body: formData });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.success) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return String(data.text || "").trim();
}

async function createWavRecorder() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("当前浏览器不支持麦克风录音");
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  const audioContext = new AudioContextClass();
  const source = audioContext.createMediaStreamSource(stream);
  const processor = audioContext.createScriptProcessor(4096, 1, 1);
  const chunks = [];

  processor.onaudioprocess = (event) => {
    chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  };
  source.connect(processor);
  processor.connect(audioContext.destination);

  return {
    recording: true,
    async stop() {
      this.recording = false;
      processor.disconnect();
      source.disconnect();
      stream.getTracks().forEach((track) => track.stop());
      const sampleRate = audioContext.sampleRate;
      await audioContext.close();
      return encodeWav(mergeFloat32Chunks(chunks), sampleRate);
    },
  };
}

function mergeFloat32Chunks(chunks) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const result = new Float32Array(length);
  let offset = 0;
  chunks.forEach((chunk) => {
    result.set(chunk, offset);
    offset += chunk.length;
  });
  return result;
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return new Blob([view], { type: "audio/wav" });
}

function writeAscii(view, offset, text) {
  for (let index = 0; index < text.length; index += 1) {
    view.setUint8(offset + index, text.charCodeAt(index));
  }
}

function appendStreamingChat(question) {
  const target = $("chatHistory");
  if (!target) return null;
  if (target.querySelector(".chat-empty")) target.innerHTML = "";
  if (role === "parent" && $("replyText")) $("replyText").remove();
  const now = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const userHtml = role === "student"
    ? `<div class="chat-line user">
        <div class="chat-bubble">问：${escapeHtml(question)}</div>
        <div class="avatar user"></div>
        <div class="chat-time">${escapeHtml(now)}</div>
      </div>`
    : `<div class="parent-chat user"><div>${escapeHtml(question)}</div><time>${escapeHtml(now)}</time></div>`;
  const assistant = document.createElement("div");
  assistant.className = role === "student" ? "chat-line agent streaming" : "parent-chat agent streaming";
  assistant.innerHTML = role === "student"
    ? `<div class="avatar agent"></div><div class="chat-flow"></div><div class="chat-time">${escapeHtml(now)}</div>`
    : `<div class="chat-flow"></div>`;
  target.insertAdjacentHTML("beforeend", userHtml);
  target.appendChild(assistant);
  target.scrollTop = target.scrollHeight;
  return { element: assistant, flowItems: [], content: "" };
}

async function streamChat(text, assistant) {
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role, text }),
    });
    if (!res.ok || !res.body) {
      handleStreamEvent({ type: "error", error: `HTTP ${res.status}` }, assistant);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      chunks.forEach((chunk) => {
        const line = chunk.split("\n").find((item) => item.startsWith("data:"));
        if (!line) return;
        try {
          handleStreamEvent(JSON.parse(line.replace(/^data:\s*/, "")), assistant);
        } catch {
          handleStreamEvent({ type: "error", error: "流式事件解析失败" }, assistant);
        }
      });
    }
  } catch (error) {
    handleStreamEvent({ type: "error", error: error?.message || "流式连接中断" }, assistant);
  }
}

function handleStreamEvent(event, assistant) {
  if (!assistant) return;
  if (event.type === "tool") {
    assistant.flowItems.push({ kind: "tool", id: event.id, tool: event.tool, args: event.args || {}, result: {} });
  } else if (event.type === "tool_result") {
    const item = [...assistant.flowItems].reverse().find((flow) => flow.kind === "tool" && (flow.id === event.id || flow.tool === event.tool));
    if (item) item.result = event.result || { status: "success" };
  } else if (event.type === "delta") {
    const last = assistant.flowItems[assistant.flowItems.length - 1];
    if (last && last.kind === "text") {
      last.text += event.delta || "";
    } else {
      assistant.flowItems.push({ kind: "text", text: event.delta || "" });
    }
    assistant.content += event.delta || "";
  } else if (event.type === "error") {
    assistant.flowItems.push({ kind: "text", text: `出错了：${event.error || "unknown"}` });
  }
  renderFlowItems(assistant);
}

function renderFlowItems(assistant) {
  const target = assistant.element.querySelector(".chat-flow");
  if (!target) return;
  target.innerHTML = assistant.flowItems.map((flow) => {
    if (flow.kind === "text") {
      return `<div class="stream-text">${escapeHtml(flow.text)}</div>`;
    }
    const done = Boolean(flow.result?.status);
    return `<details class="tool-card" ${done ? "" : "open"}>
      <summary>
        <span>${escapeHtml(flow.tool)}</span>
        <em>${done ? (flow.result.status === "error" ? "调用失败" : "调用完成") : "正在调用工具..."}</em>
      </summary>
      <div class="tool-card__body">
        <label>Args</label>
        <pre>${escapeHtml(formatJson(flow.args))}</pre>
        <label>Result</label>
        <pre>${done ? escapeHtml(formatJson(flow.result)) : "获取数据中..."}</pre>
      </div>
    </details>`;
  }).join("");
  const container = $("chatHistory");
  if (container) container.scrollTop = container.scrollHeight;
}

function formatJson(value) {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value);
  }
}

function renderMessages(messages) {
  const target = $("messageList");
  if (!target) return;
  let filtered = (messages || []).filter((item) => item.target === "student" || item.target === role);
  if (role === "student") {
    filtered = filtered.filter((item) => {
      const message = String(item.message || "");
      return !message.startsWith("问：") && !message.startsWith("答：");
    });
    speakNewStudentMessages(filtered);
    target.innerHTML = filtered.slice(-3).map((item) =>
      `<div class="student-message-row">
        <div class="message-bell">铃</div>
        <div class="message-bubble">${escapeHtml(cleanMessage(item.message))}</div>
        <div class="message-time">${escapeHtml(timeOnly(item.time))}</div>
      </div>`
    ).join("") || `<div class="student-message-row">
      <div class="message-bell">铃</div>
      <div class="message-bubble">暂无系统消息</div>
      <div class="message-time">--:--:--</div>
    </div>`;
    return;
  }
  target.innerHTML = filtered.slice(-8).reverse().map((item) =>
    `<div class="message">[${escapeHtml(timeOnly(item.time))}] ${escapeHtml(item.message)}</div>`
  ).join("") || `<div class="message">暂无系统消息</div>`;
}

function unlockSpeech() {
  if (role !== "student" || speechUnlocked) return;
  speechUnlocked = true;
  playNextTts();
}

function speakNewStudentMessages(messages) {
  if (role !== "student") return;
  const candidates = (messages || []).filter((item) => {
    const message = String(item.message || "").trim();
    return (
      message
      && item.target === "student"
      && !item.silent_tts
      && !message.startsWith("问：")
      && !message.startsWith("答：")
    );
  });

  if (!speechInitialized) {
    candidates.forEach((item) => spokenMessages.add(messageKey(item)));
    speechInitialized = true;
    return;
  }

  candidates.forEach((item) => {
    const key = messageKey(item);
    if (spokenMessages.has(key)) return;
    spokenMessages.add(key);
    speakText(cleanMessage(item.message));
  });
}

function speakText(text) {
  const content = String(text || "").trim();
  if (!content) return;
  ttsQueue.push(content);
  playNextTts();
}

function messageKey(item) {
  return `${item.time || ""}-${item.message || ""}`;
}

async function playNextTts() {
  if (!speechUnlocked || ttsPlaying || !ttsQueue.length) return;
  ttsPlaying = true;
  const text = ttsQueue.shift();
  try {
    const response = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) throw new Error("tts failed");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    currentTtsAudio = audio;
    audio.onended = () => {
      URL.revokeObjectURL(url);
      currentTtsAudio = null;
      ttsPlaying = false;
      playNextTts();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      currentTtsAudio = null;
      ttsPlaying = false;
      playNextTts();
    };
    await audio.play();
  } catch {
    ttsPlaying = false;
    playNextTts();
  }
}

function renderLogs(logs) {
  const target = $("logList");
  if (!target) return;
  target.innerHTML = (logs || []).slice(-8).reverse().map((line) =>
    `<div class="log">${escapeHtml(line)}</div>`
  ).join("") || `<div class="log">暂无日志</div>`;
}

function renderAbnormalEvents(data) {
  const target = $("abnormalList");
  if (!target) return;
  const items = data?.items || [];
  if ($("abnormalCount")) $("abnormalCount").textContent = `${Number(data?.total || 0)} 条`;
  target.innerHTML = items.map((item) => {
    const meta = abnormalMeta(item);
    return `<article class="abnormal-item ${escapeHtml(item.kind || "alert")} ${escapeHtml(item.severity || "medium")}">
      <div class="abnormal-icon"></div>
      <div class="abnormal-body">
        <div class="abnormal-title">
          <strong>${escapeHtml(item.title || "--")}</strong>
          <span>${escapeHtml(item.status || "--")}</span>
        </div>
        <p>${escapeHtml(item.summary || "暂无详情")}</p>
        <div class="abnormal-meta">${meta}</div>
      </div>
      <time>${escapeHtml(item.time_label || timeOnly(item.time))}</time>
    </article>`;
  }).join("") || `<div class="abnormal-empty">今日暂无异常事件</div>`;
}

function abnormalMeta(item) {
  const parts = [];
  if (item.kind === "away") {
    parts.push(`开始 ${escapeHtml(item.start_label || "--")}`);
    if (item.alert_label) parts.push(`提醒 ${escapeHtml(item.alert_label)}`);
    if (item.return_label) parts.push(`回座 ${escapeHtml(item.return_label)}`);
    parts.push(`持续 ${escapeHtml(item.duration_text || "持续中")}`);
    if (item.distance_cm != null) parts.push(`距离 ${escapeHtml(item.distance_cm)}cm`);
  } else {
    parts.push(`时间 ${escapeHtml(item.time_label || timeOnly(item.time))}`);
  }
  return parts.map((part) => `<span>${part}</span>`).join("");
}

function renderTrace(trace) {
  const target = $("traceList");
  if (!target) return;
  target.innerHTML = (trace || []).slice(-10).reverse().map((item, index) =>
    `<div class="trace">${index + 1}. ${escapeHtml(item.tool)} → ${escapeHtml(JSON.stringify(item.result))}</div>`
  ).join("") || `<div class="trace">暂无工具调用</div>`;
}

async function refreshState() {
  const hasTrendRange = Boolean($("trendRange"));
  const stateUrl = hasTrendRange ? trendStateUrl() : "/api/state";
  const data = await fetch(stateUrl).then((res) => res.json());
  $("statusText").textContent = role === "student" ? studentStatusText(data.status) : parentStatusText(data);
  if ($("statusMirror")) $("statusMirror").textContent = studentStatusText(data.status);
  const countSource = role === "parent" ? (data.today_stats || data) : data;
  if ($("awayCount")) $("awayCount").textContent = countSource.away_count ?? 0;
  if ($("alertCount")) $("alertCount").textContent = countSource.alert_count ?? 0;

  const ledDot = $("ledDot");
  if (ledDot) {
    ledDot.className = `led-dot ${data.led_color}`;
  }
  if ($("ledText")) {
    $("ledText").textContent = ledText(data.led_color);
  }

  const env = data.environment || {};
  if ($("tempText")) $("tempText").textContent = env.temperature ? `${env.temperature}℃` : "--℃";
  if ($("humiText")) $("humiText").textContent = env.humidity ? `${env.humidity}%` : "--%";
  if ($("envSuggestion")) $("envSuggestion").textContent = env.suggestion || "等待读取环境数据";
  if ($("envText")) {
    $("envText").textContent = env.temperature
      ? `${env.temperature}℃ / ${env.humidity}%`
      : "--℃ / --%";
  }
  renderTimer(data);
  renderParentState(data);
  renderTrend(data.trend);

  renderMessages(data.messages);
  renderAbnormalEvents(data.abnormal_events);
  renderLogs(data.recent_logs);
  renderTrace(data.tool_trace);
  await processVoiceEvents(data.voice_events);
}

async function processVoiceEvents(events) {
  if (role !== "student" || !$("voiceInput")) return;
  const items = (events || []).slice().sort((left, right) => Number(left.id || 0) - Number(right.id || 0));
  if (!voiceEventsInitialized) {
    lastVoiceEventId = Math.max(0, ...items.map((item) => Number(item.id || 0)));
    voiceEventsInitialized = true;
    return;
  }
  const pending = items.filter((item) => Number(item.id || 0) > lastVoiceEventId);
  if (!pending.length || handlingVoiceEvents) return;
  handlingVoiceEvents = true;
  try {
    for (const event of pending) {
      lastVoiceEventId = Math.max(lastVoiceEventId, Number(event.id || 0));
      if (event.action === "start") {
        await startVoiceRecording();
      } else if (event.action === "stop") {
        await stopVoiceRecording();
      }
    }
  } finally {
    handlingVoiceEvents = false;
  }
}

function trendStateUrl() {
  const params = new URLSearchParams({ trend_range: trendRange });
  if (trendRange === "custom") {
    if ($("trendStart")?.value) params.set("start", $("trendStart").value);
    if ($("trendEnd")?.value) params.set("end", $("trendEnd").value);
  }
  return `/api/state?${params.toString()}`;
}

function localDateTimeValue(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function ensureCustomTrendDefaults() {
  const startInput = $("trendStart");
  const endInput = $("trendEnd");
  if (!startInput || !endInput) return;
  const now = new Date();
  const start = new Date(now.getTime() - 30 * 60 * 1000);
  if (!startInput.value) startInput.value = localDateTimeValue(start);
  if (!endInput.value) endInput.value = localDateTimeValue(now);
}

function updateTrendControls() {
  if (!$("customTrendControls")) return;
  const isCustom = trendRange === "custom";
  $("customTrendControls").classList.toggle("hidden", !isCustom);
  if (isCustom) ensureCustomTrendDefaults();
}

function renderTrend(trend) {
  if (!trend || !$("timelineTrack")) return;
  const timelineTrack = $("timelineTrack");
  if (timelineTrack) {
    const segments = trend.timeline_segments || [];
    timelineTrack.innerHTML = segments.map((segment) => {
      const displayKind = role === "student" && segment.kind === "uncertain" ? "studying" : (segment.kind || "no_data");
      const displayLabel = role === "student" && segment.kind === "uncertain" ? "在座学习" : (segment.label || "");
      return `
        <button
          class="timeline-segment ${escapeHtml(displayKind)}"
          style="left:${Number(segment.left || 0)}%;width:${Number(segment.width || 0)}%;"
          title="${escapeHtml(displayLabel)} ${escapeHtml(segment.start || "--")} - ${escapeHtml(segment.end || "--")}"
          aria-label="${escapeHtml(displayLabel)} ${escapeHtml(segment.start || "--")} 到 ${escapeHtml(segment.end || "--")}"
        ></button>
      `;
    }).join("");
  }
  if ($("timelineEmpty")) $("timelineEmpty").classList.toggle("hidden", Boolean(trend.has_data));
  if ($("trendStartLabel")) $("trendStartLabel").textContent = trend.labels?.start || "--";
  if ($("trendMiddleLabel")) $("trendMiddleLabel").textContent = trend.labels?.middle || "--";
  if ($("trendEndLabel")) $("trendEndLabel").textContent = trend.labels?.end || "--";
  renderStateDistribution(trend.distribution);
}

function renderStateDistribution(distribution) {
  const stats = distribution || {};
  const uncertainRaw = Number(stats.uncertain || 0);
  const studying = Number(stats.studying || 0) + (role === "student" ? uncertainRaw : 0);
  const uncertain = role === "parent" ? uncertainRaw : 0;
  const resting = Number(stats.break || 0);
  const away = Number(stats.away || 0);
  const noData = Math.max(0, Number(stats.no_data ?? (100 - studying - uncertain - resting - away)));
  const seconds = stats.seconds || {};
  const studyingSeconds = Number(seconds.studying || 0) + (role === "student" ? Number(seconds.uncertain || 0) : 0);

  if ($("studyingPct")) $("studyingPct").textContent = `${studying}% / ${formatDuration(studyingSeconds)}`;
  if ($("uncertainPct")) $("uncertainPct").textContent = `${uncertain}% / ${formatDuration(seconds.uncertain)}`;
  if ($("breakPct")) $("breakPct").textContent = `${resting}% / ${formatDuration(seconds.break)}`;
  if ($("awayPct")) $("awayPct").textContent = `${away}% / ${formatDuration(seconds.away)}`;
  if ($("noDataPct")) $("noDataPct").textContent = `${noData}% / ${formatDuration(seconds.no_data)}`;
  if ($("parentDonutTotal")) $("parentDonutTotal").textContent = stats.total_text || "--";

  const studyingEnd = studying;
  const uncertainEnd = studyingEnd + uncertain;
  const breakEnd = uncertainEnd + resting;
  const awayEnd = breakEnd + away;
  const donut = document.querySelector(".donut-chart");
  if (donut) {
    donut.style.background = `conic-gradient(
      var(--green) 0 ${studyingEnd}%,
      var(--amber) ${studyingEnd}% ${uncertainEnd}%,
      var(--blue) ${uncertainEnd}% ${breakEnd}%,
      var(--coral) ${breakEnd}% ${awayEnd}%,
      #cbd5e1 ${awayEnd}% 100%
    )`;
  }
}

function renderTimer(data) {
  const phaseTotal = Number(data.phase_total_seconds != null ? data.phase_total_seconds : data.target_minutes * 60 || 0);
  const phaseElapsed = Number(data.phase_elapsed_seconds != null ? data.phase_elapsed_seconds : data.elapsed_seconds || 0);
  const remaining = Math.max(0, phaseTotal - phaseElapsed);
  const display = phaseTotal ? `${formatDuration(phaseElapsed)}/${formatDuration(phaseTotal)}` : formatDuration(phaseElapsed);
  const progress = phaseTotal ? Math.max(0, Math.min(100, Math.round((phaseElapsed / phaseTotal) * 100))) : 0;
  const isBreak = data.status === "break";

  if ($("phaseTitle")) $("phaseTitle").textContent = isBreak ? "本轮休息" : "本轮学习";
  if ($("remainingLabel")) $("remainingLabel").textContent = isBreak ? "还可休息" : "剩余";
  if ($("elapsedText")) $("elapsedText").textContent = display;
  if ($("remainingText")) $("remainingText").textContent = formatDuration(remaining);
  if ($("parentProgress")) $("parentProgress").style.width = `${progress}%`;
  if ($("studentTopProgress")) $("studentTopProgress").style.width = `${progress}%`;
  if ($("parentTargetText")) $("parentTargetText").textContent = data.phase_total_text || formatDuration(phaseTotal);
  if ($("parentStatsTime")) $("parentStatsTime").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false }).slice(0, 5);
  if ($("studentTodayFocus")) $("studentTodayFocus").textContent = data.today_stats?.focus_text || data.elapsed_text || "--";
  if ($("studentDefaultStudy")) $("studentDefaultStudy").textContent = `${Number(data.default_target_minutes || 0)}分钟`;
  if ($("studentDefaultBreak")) $("studentDefaultBreak").textContent = formatDuration(data.default_break_seconds || 0);
}

function renderParentState(data) {
  if (role !== "parent") return;
  const env = data.environment || {};
  const todayStats = data.today_stats || {};
  const latestSeat = latestToolResult(data.tool_trace, "get_seat_status");
  const distance = latestSeat?.distance?.distance_cm;
  const visionDetected = latestSeat?.vision?.person_detected;
  const targetSeconds = Number(data.phase_total_seconds || data.target_minutes * 60 || 60);
  const elapsedSeconds = Number(data.elapsed_seconds || 0);
  const progress = Math.max(0, Math.min(100, Math.round((elapsedSeconds / targetSeconds) * 100)));

  if ($("parentStatusSub")) $("parentStatusSub").textContent = data.status_text || "--";
  if ($("parentFocusTime")) $("parentFocusTime").textContent = todayStats.focus_text || data.elapsed_text || "--";
  if ($("parentTargetText")) $("parentTargetText").textContent = data.phase_total_text || "--";
  if ($("parentProgress")) $("parentProgress").style.width = `${progress}%`;
  if ($("parentDistanceText")) $("parentDistanceText").textContent = distance != null ? `距离 ${distance}cm` : "距离 --cm";
  if ($("parentDistanceInline")) $("parentDistanceInline").textContent = distance != null ? `${distance} cm` : "-- cm";
  if ($("parentPresenceText")) $("parentPresenceText").textContent = visionDetected == null ? "--" : visionDetected ? "是" : "否";
  if ($("parentPresenceTag")) {
    $("parentPresenceTag").textContent = data.seat_text || "--";
    $("parentPresenceTag").className = `detection-status ${detectionStatusClass(data.seat_status)}`;
  }
  if ($("parentFusionExplain")) {
    $("parentFusionExplain").textContent = fusionExplainText(data.seat_status, latestSeat);
    $("parentFusionExplain").className = `fusion-explain ${detectionStatusClass(data.seat_status)}`;
  }
  if ($("parentLastUpdated")) $("parentLastUpdated").textContent = new Date().toLocaleString("zh-CN", { hour12: false });
  if ($("parentStatsTime")) $("parentStatsTime").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false }).slice(0, 5);
  if ($("parentDonutTotal")) $("parentDonutTotal").textContent = todayStats.focus_text || data.elapsed_text || "--";
  if ($("envSuggestion")) $("envSuggestion").textContent = env.temperature ? envLevelText(env.level) : "等待读取";
}

function detectionStatusClass(status) {
  if (status === "present") return "present";
  if (status === "away") return "away";
  return "uncertain";
}

function fusionExplainText(status, seat) {
  const confidence = seat?.confidence;
  if (status === "present") {
    return "融合判断：超声波距离与 YOLO 视觉均判断有人在座，因此当前为高置信在座。";
  }
  if (status === "away") {
    return "融合判断：超声波距离超过阈值且 YOLO 未检测到人，因此当前为高置信离座。";
  }
  if (confidence === "medium") {
    return "融合判断：超声波与视觉结果冲突，系统暂时标记为不确定，避免误判离座。";
  }
  if (confidence === "low") {
    return "融合判断：至少一个检测源不可用，系统暂时标记为不确定，请检查摄像头或传感器。";
  }
  return "融合判断：超声波与视觉结果一致时为高置信；结果冲突时标记为不确定。";
}

function formatDuration(seconds) {
  seconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours) return `${hours}小时${minutes}分钟`;
  if (minutes) return `${minutes}分${secs}秒`;
  return `${secs}秒`;
}

function renderStudentChat(question, answer) {
  const target = $("chatHistory");
  if (!target) return;
  const now = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const questionHtml = question ? `
    <div class="chat-line user">
      <div class="chat-bubble">问：${escapeHtml(question)}</div>
      <div class="avatar user">人</div>
      <div class="chat-time">${escapeHtml(now)}</div>
    </div>` : "";
  target.innerHTML = `${questionHtml}
    <div class="chat-line agent">
      <div class="avatar agent">机</div>
      <div class="chat-bubble">答：${escapeHtml(answer)}</div>
      <div class="chat-time">${escapeHtml(now)}</div>
    </div>`;
}

function studentStatusText(status) {
  return {
    idle: "未开始",
    studying: "学习中",
    break: "休息中",
    away_alert: "离座暂停",
    error: "异常",
  }[status] || status || "--";
}

function parentStatusText(data) {
  if (data.status === "studying" && data.seat_status === "present") return "在座专注";
  if (data.status === "studying") return "学习中";
  if (data.status === "away_alert") return "离座暂停";
  if (data.status === "break") return "休息中";
  if (data.status === "idle") return data.seat_status === "present" ? "在座" : "未开始";
  return data.status_text || "--";
}

function ledText(color) {
  return {
    green: "绿色",
    blue: "蓝色",
    red: "红色",
    off: "熄灭",
  }[color] || "--";
}

function envLevelText(level) {
  return {
    normal: "正常",
    hot: "偏热",
    humid: "偏湿",
  }[level] || "正常";
}

function timeOnly(value) {
  return String(value || "").slice(11) || "--:--:--";
}

function cleanMessage(message) {
  return String(message || "").replace(/^问：|^答：/, "");
}

function latestToolResult(trace, toolName) {
  const items = trace || [];
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].tool === toolName) return items[index].result;
  }
  return null;
}

function updateParentClock() {
  if ($("parentClock")) {
    $("parentClock").textContent = new Date().toLocaleString("zh-CN", { hour12: false });
  }
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => runAction(button.dataset.action));
});

if (role === "student") {
  document.addEventListener("click", unlockSpeech, { once: true });
}

if ($("chatSend")) {
  $("chatSend").addEventListener("click", sendChat);
}

if ($("voiceInput")) {
  $("voiceInput").addEventListener("click", toggleVoiceInput);
}

if ($("closeSettings")) {
  $("closeSettings").addEventListener("click", closeSettingsPanel);
}

if ($("closeReport")) {
  $("closeReport").addEventListener("click", closeReportPanel);
}

if ($("reloadSettings")) {
  $("reloadSettings").addEventListener("click", loadSettings);
}

if ($("saveSettings")) {
  $("saveSettings").addEventListener("click", saveSettings);
}

if ($("settingsModal")) {
  $("settingsModal").addEventListener("click", (event) => {
    if (event.target === $("settingsModal")) closeSettingsPanel();
  });
}

if ($("reportModal")) {
  $("reportModal").addEventListener("click", (event) => {
    if (event.target === $("reportModal")) closeReportPanel();
  });
}

if ($("chatInput")) {
  $("chatInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendChat();
  });
}

if ($("cameraToggle")) {
  $("cameraToggle").addEventListener("click", () => {
    const video = $("parentVideo");
    const placeholder = $("cameraPlaceholder");
    const running = !video.classList.contains("hidden");
    if (running) {
      video.src = "";
      video.classList.add("hidden");
      placeholder.classList.remove("hidden");
      $("cameraToggle").textContent = "开启摄像头";
    } else {
      video.src = `${video.dataset.src}?t=${Date.now()}`;
      video.classList.remove("hidden");
      placeholder.classList.add("hidden");
      $("cameraToggle").textContent = "关闭摄像头";
    }
  });
}

if ($("trendRange")) {
  trendRange = $("trendRange").value || trendRange;
  updateTrendControls();
  $("trendRange").addEventListener("change", () => {
    trendRange = $("trendRange").value || "30m";
    updateTrendControls();
    refreshState();
  });
}

["trendStart", "trendEnd"].forEach((id) => {
  if ($(id)) {
    $(id).addEventListener("change", () => {
      trendRange = $("trendRange")?.value || trendRange;
      refreshState();
    });
  }
});

refreshState();
setInterval(refreshState, 1000);
updateParentClock();
setInterval(updateParentClock, 1000);
