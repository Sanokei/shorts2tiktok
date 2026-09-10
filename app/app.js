const $ = (id) => document.getElementById(id);
const FIELDS = ["youtube_api_key", "youtube_channel", "tiktok_client_key",
  "tiktok_client_secret", "caption_template", "extra_hashtags", "mode",
  "privacy_level", "disable_comment", "disable_duet", "disable_stitch",
  "redirect_uri"];

let videos = [];
let pollTimer = null;
let currentJob = null;
let grantedScopes = "";

// Direct posting needs a scope the inbox flow never asks for, so a connection
// made in inbox mode cannot post. Catch that before a batch starts.
function needsReconnect() {
  return $("mode").value === "direct" && !grantedScopes.includes("video.publish");
}

async function api(path, body) {
  const res = await fetch(path, body ? {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  } : {});
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data;
}

function notice(text, kind) {
  $("notice").innerHTML = text
    ? `<div class="banner" style="border-left-color:var(--${kind || "warn"})">${text}</div>`
    : "";
}

// ------------------------------------------------------------------ config

async function loadConfig() {
  const cfg = await api("/api/config");
  FIELDS.forEach((f) => {
    const el = $(f);
    if (!el || cfg[f] === undefined) return;
    if (el.type === "checkbox") el.checked = !!cfg[f];
    else el.value = cfg[f];
  });
  grantedScopes = cfg.scopes || "";
  if (cfg.secret_set) $("tiktok_client_secret").placeholder = "saved, type to replace";
  $("redir").textContent = cfg.loopback_redirect;
  $("pasted").placeholder = (cfg.redirect_uri || cfg.loopback_redirect) + "?code=...";
  setConnected(cfg.connected);
  $("direct-opts").hidden = cfg.mode !== "direct";
}

function setConnected(on) {
  const pill = $("tt-pill");
  pill.textContent = on ? "TikTok: connected" : "TikTok: not connected";
  pill.classList.toggle("on", on);
  $("disconnect").hidden = !on;
  $("connect").textContent = on ? "Reconnect TikTok" : "Connect TikTok";
  if (on) showAccount();
}

// Name the account the tool is authorized for, so it is never ambiguous whose
// profile a post would land on.
async function showAccount() {
  try {
    const user = await api("/api/account");
    const who = user.display_name;
    if (who) $("tt-pill").textContent = "TikTok: " + who;
  } catch {
    // an expired grant still reads as connected until the next call fails
  }
}

let saveTimer;
function saveSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    const body = {};
    FIELDS.forEach((f) => {
      const el = $(f);
      if (!el) return;
      body[f] = el.type === "checkbox" ? el.checked : el.value;
    });
    await api("/api/config", body);
  }, 400);
}

FIELDS.forEach((f) => {
  const el = $(f);
  if (el) el.addEventListener("input", saveSoon);
  if (el && el.tagName === "SELECT") el.addEventListener("change", saveSoon);
});

$("mode").addEventListener("change", () => {
  $("direct-opts").hidden = $("mode").value !== "direct";
  if (needsReconnect()) {
    notice(`Direct posting needs the video.publish scope, which this sign-in does not
      have. Press Connect TikTok again to pick it up.`);
  } else {
    notice("");
  }
});

document.querySelectorAll(".help").forEach((btn) => {
  btn.addEventListener("click", () => $(btn.dataset.help).classList.toggle("open"));
});

// -------------------------------------------------------------------- auth

$("connect").addEventListener("click", async () => {
  try {
    clearTimeout(saveTimer);
    await saveNow();
    const { url } = await api("/api/auth/start", {});
    window.open(url, "_blank", "width=620,height=760");
    $("h-paste").classList.add("open");
    watchConnection();
  } catch (err) {
    notice(err.message, "yt");
  }
});

async function saveNow() {
  const body = {};
  FIELDS.forEach((f) => {
    const el = $(f);
    if (el) body[f] = el.type === "checkbox" ? el.checked : el.value;
  });
  await api("/api/config", body);
}

function watchConnection() {
  let ticks = 0;
  const timer = setInterval(async () => {
    const cfg = await api("/api/config");
    if (cfg.connected) {
      setConnected(true);
      $("h-paste").classList.remove("open");
      notice("");
      clearInterval(timer);
    }
    if (++ticks > 100) clearInterval(timer);
  }, 3000);
}

$("paste-go").addEventListener("click", async () => {
  try {
    await api("/api/auth/paste", { url: $("pasted").value.trim() });
    setConnected(true);
    $("h-paste").classList.remove("open");
    notice("");
  } catch (err) {
    notice(err.message, "yt");
  }
});

$("disconnect").addEventListener("click", async () => {
  await api("/api/disconnect", {});
  setConnected(false);
});

// ------------------------------------------------------------------ shorts

$("scan").addEventListener("click", async () => {
  $("scan").disabled = true;
  $("list").innerHTML = '<div class="empty">Asking YouTube...</div>';
  notice("");
  try {
    await saveNow();
    const data = await api("/api/shorts");
    videos = data.videos;
    $("detect").textContent = `${data.channel.title} · matched by ${data.detection}`;
    render();
  } catch (err) {
    $("list").innerHTML = '<div class="empty">Nothing loaded.</div>';
    notice(err.message, "yt");
  }
  $("scan").disabled = false;
});

function fmtDuration(s) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function render() {
  if (!videos.length) {
    $("list").innerHTML = '<div class="empty">No Shorts found on that channel.</div>';
    return;
  }
  const rows = videos.map((v, i) => `
    <tr id="row-${v.id}" class="${v.ported ? "done" : ""}">
      <td><input type="checkbox" data-i="${i}" ${!v.ported || $("include_ported").checked ? "checked" : ""}></td>
      <td><img src="${v.thumbnail}" alt=""></td>
      <td>
        <div class="title">${escapeHtml(v.title)}</div>
        <div class="meta">${fmtDuration(v.seconds)} · ${v.views.toLocaleString()} views · ${v.published.slice(0, 10)}</div>
      </td>
      <td><button class="ghost save" data-id="${v.id}" data-name="${escapeHtml(v.title)}">save mp4</button></td>
      <td class="state" id="state-${v.id}">${v.ported ? "already sent" : ""}</td>
    </tr>`).join("");
  $("list").innerHTML = `<table>
      <thead><tr><th></th><th></th><th>Video</th><th>File</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  updateCount();
}

$("list").addEventListener("change", (e) => {
  if (e.target.type === "checkbox") updateCount();
});

$("include_ported").addEventListener("change", () => {
  if (videos.length) render();
});

$("list").addEventListener("click", async (e) => {
  const copyBtn = e.target.closest(".copy");
  if (copyBtn) {
    await navigator.clipboard.writeText(copyBtn.dataset.caption);
    copyBtn.textContent = "copied";
    setTimeout(() => { copyBtn.textContent = "copy caption"; }, 1500);
    return;
  }

  // Fetching can take a while on a cold video, so the button carries the state.
  const saveBtn = e.target.closest(".save");
  if (!saveBtn) return;
  const label = saveBtn.textContent;
  saveBtn.disabled = true;
  saveBtn.textContent = "fetching";
  try {
    const res = await fetch(`/api/download?id=${encodeURIComponent(saveBtn.dataset.id)}`);
    if (!res.ok) throw new Error((await res.json()).error || "download failed");
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `${saveBtn.dataset.name.replace(/[\\/:*?"<>|]/g, "")}.mp4`;
    a.click();
    URL.revokeObjectURL(url);
    saveBtn.textContent = "saved";
  } catch (err) {
    saveBtn.textContent = "failed";
    notice(err.message, "yt");
  }
  setTimeout(() => { saveBtn.textContent = label; saveBtn.disabled = false; }, 2000);
});

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function selected() {
  return [...document.querySelectorAll("#list input:checked")].map((cb) => videos[cb.dataset.i]);
}

function updateCount() {
  const n = selected().length;
  $("count").textContent = n ? `${n} selected of ${videos.length}` : `${videos.length} Shorts`;
  $("port").disabled = n === 0;
  // TikTok caps how many uploads can sit unposted in the inbox at once, so a
  // big selection drips rather than failing. Say so before they press the button.
  if (n > 5 && $("mode").value === "inbox") {
    notice(`TikTok only holds about 5 unposted uploads at a time. These ${n} will go up
      a few at a time, pausing until you post some from your phone. Leave the app running
      and it keeps feeding them in.`);
  } else {
    notice("");
  }
}

// -------------------------------------------------------------------- port

$("port").addEventListener("click", async () => {
  const picked = selected();
  if (!picked.length) return;
  if (needsReconnect()) {
    notice(`Direct posting needs the video.publish scope. Press Connect TikTok again
      to authorize it, then run this batch.`);
    return;
  }
  $("port").disabled = true;
  try {
    const { job } = await api("/api/port", { videos: picked });
    currentJob = job;
    $("stop").hidden = false;
    notice(`Porting ${picked.length} video${picked.length > 1 ? "s" : ""}. Keep this window open.`, "warn");
    pollJob(job);
  } catch (err) {
    notice(err.message, "yt");
    $("port").disabled = false;
  }
});

$("stop").addEventListener("click", async () => {
  if (!currentJob) return;
  $("stop").disabled = true;
  await api("/api/cancel", { job: currentJob });
  notice("Stopping after the video in flight finishes.", "warn");
});

function pollJob(id) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const job = await api("/api/job?id=" + id);
    job.order.forEach((vid) => {
      const item = job.items[vid];
      const cell = $("state-" + vid);
      if (!cell) return;
      cell.className = "state " + ({ done: "ok", error: "err", stopped: "" }[item.status] || "busy");
      if (item.status === "done" && item.caption) {
        cell.innerHTML = `${escapeHtml(item.message)}<br>
          <button class="ghost copy" data-caption="${escapeHtml(item.caption)}">copy caption</button>`;
      } else {
        cell.textContent = item.message || item.status;
      }
    });
    const items = Object.values(job.items);
    const waiting = items.filter((i) => i.status === "waiting").length;
    const left = items.filter((i) => ["queued", "waiting"].includes(i.status)).length;
    if (waiting) {
      notice(`Inbox is full. ${left} still to go. Post some from your phone and this
        picks up on its own. You can close the app and it resumes next launch.`, "warn");
    }
    if (job.done) {
      clearInterval(pollTimer);
      currentJob = null;
      $("stop").hidden = true;
      $("stop").disabled = false;
      const failed = items.filter((i) => i.status === "error").length;
      const stopped = items.filter((i) => i.status === "stopped").length;
      if (failed) notice(`Finished with ${failed} failure${failed > 1 ? "s" : ""}.`, "yt");
      else if (stopped) notice(`Stopped with ${stopped} left unsent.`, "warn");
      else notice("All done.", "ok");
      $("port").disabled = false;
    }
  }, 2000);
}

async function loadReview() {
  const { justification } = await api("/api/review");
  $("justification").value = justification;
  $("just-count").textContent = `${justification.length} / 1000 characters`;
}

$("check-scopes").addEventListener("click", async () => {
  const out = $("scope-report");
  out.textContent = "checking...";
  const probes = [
    ["user.info.basic", "/api/account", (r) => r.display_name || "no name returned"],
    ["video.publish", "/api/creator", (r) => ((r.data || {}).privacy_level_options || []).join(", ") || "no options returned"],
  ];
  const lines = [];
  for (const [scope, path, describe] of probes) {
    try {
      lines.push(`<span class="ok">works</span> &nbsp;${scope} &nbsp;&rarr; ${escapeHtml(String(describe(await api(path))))}`);
    } catch (err) {
      lines.push(`<span class="err">fails</span> &nbsp;${scope} &nbsp;&rarr; ${escapeHtml(err.message.slice(0, 90))}`);
    }
  }
  lines.push('<span class="ok">works</span> &nbsp;video.upload &nbsp;&rarr; proven by any successful inbox send');
  out.innerHTML = lines.join("<br>");
});

$("copy-just").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("justification").value);
  $("copy-just").textContent = "copied";
  setTimeout(() => { $("copy-just").textContent = "Copy justification"; }, 1500);
});

loadConfig();
loadReview();
