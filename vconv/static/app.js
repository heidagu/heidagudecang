/* VConv 前端逻辑：500ms 轮询 + 变更检测渲染 */
"use strict";

/* ---- 与后端 models.py 一致的兼容矩阵 ---- */
const CRF_SPECS = {
  h264: { def: 23, min: 18, max: 28 },
  h265: { def: 28, min: 20, max: 32 },
  av1:  { def: 30, min: 20, max: 40 },
  vp9:  { def: 31, min: 15, max: 35 },
  vp8:  { def: 10, min: 4, max: 63 },
};
const QSCALE_SPECS = {
  mpeg4:      { def: 5, min: 2, max: 31 },
  mpeg2video: { def: 5, min: 2, max: 31 },
  mjpeg:      { def: 5, min: 2, max: 31 },
};
const CONTAINER_CODECS = {
  mp4:  ["h264", "h265", "av1", "mpeg4"],
  mkv:  ["h264", "h265", "av1", "vp9", "vp8", "mpeg4", "mpeg2video", "prores", "mjpeg"],
  mov:  ["h264", "h265", "mpeg4", "prores", "mjpeg"],
  webm: ["vp9", "vp8", "av1"],
  avi:  ["mpeg4", "mpeg2video", "mjpeg"],
  flv:  ["h264", "mpeg4"],
  m4v:  ["h264", "h265", "mpeg4"],
  ts:   ["h264", "h265", "mpeg2video"],
};
const CONTAINER_AUDIO = {
  mp4:  ["copy", "aac", "flac", "none"],
  mkv:  ["copy", "aac", "opus", "mp3", "flac", "ac3", "none"],
  mov:  ["copy", "aac", "flac", "none"],
  webm: ["copy", "opus", "none"],
  avi:  ["copy", "mp3", "ac3", "none"],
  flv:  ["copy", "aac", "mp3", "none"],
  m4v:  ["copy", "aac", "none"],
  ts:   ["copy", "aac", "mp3", "ac3", "none"],
};
const STATUS_LABELS = {
  queued: "排队中", running: "转换中", done: "完成",
  failed: "失败", cancelled: "已取消",
};

/* ---- 状态 ---- */
const files = [];           // {path, info}
let hwAvail = {};           // {h264: "h264_videotoolbox", ...}
let lastSig = "";
let dlPollTimer = null;

/* ---- 工具 ---- */
const $ = (id) => document.getElementById(id);

async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { /* 204 等无 body */ }
  if (!res.ok) {
    throw new Error((body && body.error) || ("HTTP " + res.status));
  }
  return body;
}

function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg;
  t.className = isErr ? "err" : "";
  t.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { t.hidden = true; }, 4000);
}

function basename(p) {
  const parts = p.split(/[\\/]/);
  return parts[parts.length - 1];
}

function fmtDur(ms) {
  if (!ms) return "";
  const s = Math.round(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return h ? h + ":" + mm + ":" + String(sec).padStart(2, "0")
           : mm + ":" + String(sec).padStart(2, "0");
}

function fmtEta(sec) {
  if (sec === null || sec === undefined || sec <= 0) return "";
  sec = Math.round(sec);
  if (sec < 60) return "约 " + sec + " 秒";
  if (sec < 3600) return "约 " + Math.round(sec / 60) + " 分钟";
  return "约 " + Math.floor(sec / 3600) + " 小时 " + Math.round((sec % 3600) / 60) + " 分";
}

function fmtSpeed(s) {
  if (!s) return "";
  const str = String(s);
  if (str.indexOf("x") >= 0) return str;
  return str + "x";
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ---- ffmpeg 状态 ---- */
let lastDlFinished = false;
let dlTimer = null;

async function loadFFmpeg() {
  try {
    const st = await api("/api/ffmpeg");
    const chip = $("ffmpeg-chip");
    const dl = st.downloading || {};
    if (st.status === "ok") {
      chip.textContent = "ffmpeg " + (st.version || "") + "（" + (st.source || "已安装") + "）";
      chip.className = "chip ok";
      $("btn-download").hidden = true;
    } else {
      chip.textContent = "未检测到 ffmpeg";
      chip.className = "chip err";
      $("btn-download").hidden = false;
    }
    $("btn-clear-manual").hidden = st.source !== "config";

    if (dl.active) {
      $("dl-progress").textContent = "下载中 " + (dl.percent || 0) + "%（" + (dl.stage || "") + "）";
      $("btn-download").disabled = true;
      scheduleDlPoll();
    } else if (dl.finished) {
      $("btn-download").disabled = false;
      if (dl.error) {
        $("dl-progress").textContent = "下载失败: " + dl.error + "（可检查代理设置，或使用「手动指定路径」）";
      } else if (!lastDlFinished) {
        $("dl-progress").textContent = "";
        toast("ffmpeg 下载完成");
      }
    }
    lastDlFinished = !!dl.finished;
  } catch (e) {
    $("ffmpeg-chip").textContent = "状态获取失败";
    $("ffmpeg-chip").className = "chip err";
  }
}

function scheduleDlPoll() {
  clearTimeout(dlTimer);
  dlTimer = setTimeout(loadFFmpeg, 1200);
}

async function loadHw() {
  try {
    const data = await api("/api/hwaccel");
    hwAvail = data.available || {};
  } catch (e) {
    hwAvail = {};
  }
  refreshUI();
}

async function loadSettings() {
  try {
    const cfg = await api("/api/settings");
    $("workers").value = cfg.workers;
    $("outdir").value = cfg.default_output_dir || "";
    const note = $("workers").parentElement.querySelector(".muted");
    if (note && cfg.cpu_count) note.textContent = "0 = 自动（本机 " + cfg.cpu_count + " 核）";
  } catch (e) {
    toast("读取设置失败: " + e.message, true);
  }
}

async function saveSettings() {
  const workers = parseInt($("workers").value, 10);
  if (!(workers >= 0 && workers <= 32)) {
    toast("并发数必须是 0-32 的整数", true);
    return;
  }
  try {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workers: workers,
        default_output_dir: $("outdir").value.trim(),
      }),
    });
    toast("设置已保存");
  } catch (e) {
    toast("保存失败: " + e.message, true);
  }
}

async function startDownload() {
  try {
    lastDlFinished = false;
    $("dl-progress").textContent = "开始下载…";
    $("btn-download").disabled = true;
    await api("/api/ffmpeg/download", { method: "POST" });
    scheduleDlPoll();
  } catch (e) {
    toast("下载失败: " + e.message, true);
    $("btn-download").disabled = false;
  }
}

async function pickFFmpeg() {
  try {
    const data = await api("/api/pick-files", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: false }),
    });
    if (data.cancelled || !data.paths || !data.paths.length) return;
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ffmpeg_path: data.paths[0] }),
    });
    toast("已指定 ffmpeg 路径");
    await loadFFmpeg();
  } catch (e) {
    toast("指定失败: " + e.message, true);
  }
}

async function clearManual() {
  try {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ffmpeg_path: "" }),
    });
    toast("已清除手动指定路径");
    await loadFFmpeg();
  } catch (e) {
    toast("清除失败: " + e.message, true);
  }
}

/* ---- 动态 UI 规则 ---- */
function refreshUI() {
  const codec = $("codec").value;
  const container = $("container").value;
  const audio = $("audio").value;
  let qm = document.querySelector('input[name="qm"]:checked').value;
  const hw = $("hw").checked;

  // 容器选项：禁用与当前编码不兼容的
  const allowed = CONTAINER_CODECS[container] || null;
  const containerSel = $("container");
  for (const opt of containerSel.options) {
    if (!opt.value) continue;
    opt.disabled = codec !== "copy" && !CONTAINER_CODECS[opt.value].includes(codec);
  }
  if (container && codec !== "copy" && allowed && !allowed.includes(codec)) {
    containerSel.value = "";
  }

  // 画质滑块范围随编码变化
  const isCopy = codec === "copy";
  const crfSpec = CRF_SPECS[codec];
  if (crfSpec) {
    const s = $("crf");
    s.min = crfSpec.min; s.max = crfSpec.max;
    const cur = parseInt(s.value, 10);
    if (!(cur >= crfSpec.min && cur <= crfSpec.max)) s.value = crfSpec.def;
    $("crf-val").textContent = s.value;
  }
  const qSpec = QSCALE_SPECS[codec];
  if (qSpec) {
    const s = $("qscale");
    s.min = qSpec.min; s.max = qSpec.max;
    const cur = parseInt(s.value, 10);
    if (!(cur >= qSpec.min && cur <= qSpec.max)) s.value = qSpec.def;
    $("qscale-val").textContent = s.value;
  }

  // copy 模式：禁用视频滤镜与画质
  $("video-fs").disabled = isCopy || audio === "extract";
  $("quality-fs").disabled = isCopy || audio === "extract";
  $("container").disabled = audio === "extract";

  // 硬件加速：仅 h264/h265 且有可用编码器
  const hwBox = $("hw");
  hwBox.disabled = isCopy || !hwAvail[codec];
  $("hw-quality").hidden = !(hw && !hwBox.disabled);
  $("hw-info").textContent = hwAvail[codec]
    ? "可用: " + hwAvail[codec] : (codec === "h264" || codec === "h265") ? "未检测到" : "";

  // 两遍编码：仅 x264/x265/vp9/vp8/mpeg4/mpeg2video + 固定码率 + 非硬件
  $("two-pass").disabled = !(qm === "bitrate" && !hw &&
    ["h264", "h265", "vp9", "vp8", "mpeg4", "mpeg2video"].includes(codec));

  // 画质模式：随编码自动切换（prores 用档位选择器，后端忽略 quality_mode）
  const isProres = codec === "prores";
  if (!isCopy) {
    if (isProres) {
      qm = "crf";
    } else if (qm === "crf" && !CRF_SPECS[codec]) {
      qm = QSCALE_SPECS[codec] ? "qscale" : "bitrate";
    } else if (qm === "qscale" && !QSCALE_SPECS[codec]) {
      qm = CRF_SPECS[codec] ? "crf" : "bitrate";
    }
    const radio = document.querySelector('input[name="qm"][value="' + qm + '"]');
    if (radio && !radio.checked) radio.checked = true;
  }
  $("qm-crf-label").hidden = isProres || (!isCopy && !CRF_SPECS[codec]);
  $("qm-qscale-label").hidden = isProres || (!isCopy && !QSCALE_SPECS[codec]);
  $("qm-bitrate-label").hidden = isProres;

  $("crf-box").hidden = qm !== "crf" || hw;
  $("qscale-box").hidden = qm !== "qscale" || hw;
  $("bitrate-box").hidden = qm !== "bitrate" || hw;
  $("prores-box").hidden = !isProres || hw;

  // 音频（flac 与无损提取格式无码率概念）
  $("extract-ext-label").hidden = audio !== "extract";
  const losslessExtract = audio === "extract" &&
    ($("extract-ext").value === "wav" || $("extract-ext").value === "flac");
  $("audio-bitrate-label").hidden = audio === "copy" || audio === "none" ||
    audio === "flac" || losslessExtract;
  let warn = "";
  if (container && audio !== "extract" && !CONTAINER_AUDIO[container].includes(audio)) {
    warn = "提示: " + container.toUpperCase() + " 容器不支持所选音频处理方式（提交时会报错）";
  }
  $("audio-warning").hidden = !warn;
  $("audio-warning").textContent = warn;

  // 自定义帧率/分辨率输入框
  $("custom-fps-label").hidden = $("fps").value !== "custom";
  $("custom-res-label").hidden = $("resolution").value !== "custom";
}

/* ---- 文件列表 ---- */
function addPaths(paths) {
  const fresh = [];
  for (const p of paths) {
    const norm = p.trim();
    if (!norm) continue;
    if (!files.some((f) => f.path === norm)) {
      files.push({ path: norm, info: null });
      fresh.push(norm);
    }
  }
  renderFiles();
  for (const p of fresh) probeFile(p);
}

async function probeFile(path) {
  try {
    const info = await api("/api/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path }),
    });
    const f = files.find((x) => x.path === path);
    if (f) f.info = info;
    renderFiles();
  } catch (e) {
    const f = files.find((x) => x.path === path);
    if (f) f.info = { error: e.message };
    renderFiles();
  }
}

function renderFiles() {
  const ul = $("files");
  ul.textContent = "";
  for (const f of files) {
    const li = el("li");
    const name = el("span", "fname", basename(f.path));
    const info = el("span", "finfo");
    if (f.info === null) {
      info.textContent = "探测中…";
    } else if (f.info.error) {
      info.textContent = "无法探测: " + f.info.error;
    } else {
      const parts = [];
      if (f.info.width) parts.push(f.info.width + "×" + f.info.height);
      if (f.info.fps) parts.push(f.info.fps.toFixed(2) + " fps");
      if (f.info.duration_ms) parts.push(fmtDur(f.info.duration_ms));
      info.textContent = parts.join(" · ");
    }
    const btn = el("button", "", "✕");
    btn.title = "移除";
    btn.addEventListener("click", () => {
      const i = files.indexOf(f);
      if (i >= 0) files.splice(i, 1);
      renderFiles();
    });
    li.appendChild(name);
    li.appendChild(info);
    li.appendChild(btn);
    ul.appendChild(li);
  }
}

async function pickFiles() {
  try {
    const data = await api("/api/pick-files", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: false }),
    });
    if (!data.cancelled && data.paths && data.paths.length) addPaths(data.paths);
  } catch (e) {
    toast("文件选择失败: " + e.message, true);
  }
}

async function pickOutdir() {
  try {
    const data = await api("/api/pick-files", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: true }),
    });
    if (!data.cancelled && data.paths && data.paths.length) {
      $("outdir").value = data.paths[0];
    }
  } catch (e) {
    toast("目录选择失败: " + e.message, true);
  }
}

/* ---- 提交任务 ---- */
function buildSettings() {
  const codec = $("codec").value;
  const audio = $("audio").value;
  const qm = document.querySelector('input[name="qm"]:checked').value;
  const hw = $("hw").checked && !$("hw").disabled;

  const s = {
    container: $("container").disabled ? "" : $("container").value,
    video_codec: codec,
    quality_mode: qm,
    hw_accel: hw,
    hw_quality: $("hw-quality").value,
    audio_mode: audio,
    audio_bitrate: $("audio-bitrate").value.trim() || "192k",
    audio_extract_ext: $("extract-ext").value,
  };

  const videoDisabled = $("video-fs").disabled;
  if (videoDisabled) {
    s.frame_rate = "source";
    s.resolution = "source";
  } else {
    s.frame_rate = $("fps").value;
    if (s.frame_rate === "custom") s.custom_fps = parseFloat($("custom-fps").value) || 30;
    s.resolution = $("resolution").value;
    if (s.resolution === "custom") {
      s.custom_width = parseInt($("custom-w").value, 10) || 0;
      s.custom_height = parseInt($("custom-h").value, 10) || 0;
    }
  }

  if (qm === "crf") {
    s.crf = parseInt($("crf").value, 10);
  } else if (qm === "qscale") {
    s.qscale = parseInt($("qscale").value, 10);
  } else {
    s.bitrate = $("bitrate").value.trim() || "6M";
    s.two_pass = $("two-pass").checked && !$("two-pass").disabled;
  }
  if (codec === "prores") s.prores_profile = $("prores-profile").value;
  return s;
}

async function startJobs() {
  if (!files.length) {
    toast("请先添加视频文件", true);
    return;
  }
  const btn = $("btn-start");
  btn.disabled = true;
  try {
    const data = await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        inputs: files.map((f) => f.path),
        settings: buildSettings(),
      }),
    });
    const n = data.jobs.length;
    files.length = 0;
    renderFiles();
    toast("已添加 " + n + " 个转换任务");
    await poll();
  } catch (e) {
    toast("提交失败: " + e.message, true);
  }
  btn.disabled = false;
}

/* ---- 任务列表渲染 ---- */
function renderJobs(jobs, history) {
  const box = $("jobs");
  const sig = signature(jobs, history);
  if (sig !== lastSig) {
    box.textContent = "";
    for (const j of jobs) box.appendChild(jobCard(j));
    lastSig = sig;
  }
  $("jobs-empty").hidden = jobs.length > 0;
  $("job-count").textContent = jobs.length ? "（" + jobs.length + "）" : "";
  renderHistory(history);
}

function signature(jobs, history) {
  return JSON.stringify([
    jobs.map((j) => [j.id, j.status, j.progress, j.speed, j.eta_seconds, j.pass_index, j.error]),
    history.map((h) => [h.id, h.status]),
  ]);
}

function jobCard(j) {
  const card = el("div", "job");
  const head = el("div", "head");
  const name = el("span", "name", basename(j.input_path));

  let pct = "";
  if (j.status === "running") pct = " " + Math.floor(j.progress || 0) + "%";
  const badge = el("span", "badge " + j.status, (STATUS_LABELS[j.status] || j.status) + pct);
  head.appendChild(name);
  head.appendChild(badge);

  const out = el("div", "out", "→ " + j.output_path);

  const bar = el("div", "bar");
  const fill = el("i");
  fill.style.width = Math.max(0, Math.min(100, j.progress || 0)) + "%";
  if (j.status === "running" && !j.duration_ms) bar.className = "bar indet";
  bar.appendChild(fill);

  const meta = el("div", "meta");
  if (j.status === "running") {
    if (fmtSpeed(j.speed)) meta.appendChild(el("span", "", "速度 " + fmtSpeed(j.speed)));
    if (fmtEta(j.eta_seconds)) meta.appendChild(el("span", "", "剩余 " + fmtEta(j.eta_seconds)));
    if (j.pass_count > 1) meta.appendChild(el("span", "", "第 " + j.pass_index + "/" + j.pass_count + " 遍"));
  } else if (j.error && j.status !== "failed") {
    meta.appendChild(el("span", "", "出错: " + j.error));
  }

  const actions = el("div", "actions");
  if (j.status === "queued" || j.status === "running") {
    const btn = el("button", "btn small", "取消");
    btn.addEventListener("click", () => cancelJob(j.id));
    actions.appendChild(btn);
  } else {
    const btn = el("button", "btn small", "删除");
    btn.addEventListener("click", () => deleteJob(j.id));
    actions.appendChild(btn);
  }

  card.appendChild(head);
  card.appendChild(out);
  card.appendChild(bar);
  card.appendChild(meta);
  if (j.status === "failed" && j.error) {
    const errbox = el("div", "errbox");
    const d = el("details");
    d.open = true;
    const sum = el("summary", "", "错误详情");
    const pre = el("pre", "", j.error);
    d.appendChild(sum);
    d.appendChild(pre);
    errbox.appendChild(d);
    card.appendChild(errbox);
  }
  if (actions.childNodes.length) card.appendChild(actions);
  return card;
}

function renderHistory(history) {
  const ul = $("history");
  const items = history.slice(-30).reverse();
  if (!items.length) {
    $("history-box").hidden = true;
    return;
  }
  $("history-box").hidden = false;
  $("history-box").querySelector("summary").textContent = "历史记录（" + history.length + "）";
  const oldSig = ul._sig;
  const sig = JSON.stringify(items.map((h) => [h.id, h.status]));
  if (sig === oldSig) return;
  ul._sig = sig;
  ul.textContent = "";
  for (const h of items) {
    const li = el("li");
    li.appendChild(el("span", "badge " + h.status, STATUS_LABELS[h.status] || h.status));
    li.appendChild(el("span", "hname", basename(h.input) + " → " + basename(h.output)));
    const t = h.finished_at ? new Date(h.finished_at * 1000) : null;
    if (t) li.appendChild(el("span", "muted", t.toLocaleTimeString()));
    ul.appendChild(li);
  }
}

async function cancelJob(id) {
  try {
    await api("/api/jobs/" + encodeURIComponent(id) + "/cancel", { method: "POST" });
    toast("已请求取消");
  } catch (e) {
    toast("取消失败: " + e.message, true);
  }
}

async function deleteJob(id) {
  try {
    await api("/api/jobs/" + encodeURIComponent(id), { method: "DELETE" });
  } catch (e) {
    toast("删除失败: " + e.message, true);
  }
  await poll();
}

/* ---- 轮询 ---- */
let pollBusy = false;
async function poll() {
  if (pollBusy || document.hidden) return;
  pollBusy = true;
  try {
    const data = await api("/api/jobs");
    renderJobs(data.jobs || [], data.history || []);
  } catch (e) {
    /* 服务器暂不可达时静默，下个周期重试 */
  }
  pollBusy = false;
}

/* ---- 事件绑定 ---- */
function bind() {
  $("btn-recheck").addEventListener("click", () => { loadFFmpeg(); loadHw(); });
  $("btn-download").addEventListener("click", startDownload);
  $("btn-manual").addEventListener("click", pickFFmpeg);
  $("btn-clear-manual").addEventListener("click", clearManual);
  $("btn-save-settings").addEventListener("click", saveSettings);
  $("btn-pick-outdir").addEventListener("click", pickOutdir);
  $("btn-add-files").addEventListener("click", pickFiles);
  $("btn-add-pasted").addEventListener("click", () => {
    const paths = $("paste-paths").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    if (paths.length) { addPaths(paths); $("paste-paths").value = ""; }
  });
  $("btn-start").addEventListener("click", startJobs);
  $("crf").addEventListener("input", () => { $("crf-val").textContent = $("crf").value; });
  $("qscale").addEventListener("input", () => { $("qscale-val").textContent = $("qscale").value; });

  for (const id of ["codec", "container", "audio", "fps", "resolution", "hw-quality", "extract-ext"]) {
    $(id).addEventListener("change", refreshUI);
  }
  $("hw").addEventListener("change", refreshUI);
  for (const r of document.querySelectorAll('input[name="qm"]')) {
    r.addEventListener("change", refreshUI);
  }
}

/* ---- 启动 ---- */
bind();
refreshUI();
loadFFmpeg();
loadHw();
loadSettings();
poll();
setInterval(poll, 500);
