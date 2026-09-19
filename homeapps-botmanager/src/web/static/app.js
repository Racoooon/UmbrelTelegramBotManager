/* Dashboard glue. All API calls are same-origin through Umbrel's proxy. */

const $ = (sel) => document.querySelector(sel);

function setTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === name);
  });
  document.querySelectorAll(".panel").forEach((p) => {
    p.classList.toggle("active", p.id === `tab-${name}`);
  });
}

document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => setTab(btn.dataset.tab));
});

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

function renderStatus(data) {
  const running = data.bot.running;
  const pill = $("#bot-pill");
  pill.textContent = running ? `Bot running · pid ${data.bot.pid}` : "Bot stopped";
  pill.className = "pill " + (running ? "on" : "off");
  const token = data.settings.bot_token_set
    ? data.settings.bot_token_preview
    : "not set";
  $("#status-meta").innerHTML = `
    <dt>Token</dt><dd>${token}</dd>
    <dt>Language</dt><dd>${data.settings.bot_language}</dd>
    <dt>Currency</dt><dd>${data.settings.vs_currency}</dd>
    <dt>Instagram host</dt><dd>${data.settings.instagram_fix_host || "kirkstagram.com"}</dd>
    <dt>Bot script</dt><dd>${data.bot.script || "thepiratebot.py"}</dd>
    <dt>Exit code</dt><dd>${data.bot.exit_code ?? "—"}</dd>
  `;
}

async function refreshStatus() {
  const data = await api("/api/status");
  renderStatus(data);
}

async function refreshLog() {
  const data = await api("/api/logs?lines=250");
  $("#log").textContent = data.text || "(log is empty)";
  $("#log").scrollTop = $("#log").scrollHeight;
}

$("#btn-start").onclick = async () => {
  await api("/api/bot/start", { method: "POST" });
  await refreshStatus();
  await refreshLog();
};
$("#btn-stop").onclick = async () => {
  await api("/api/bot/stop", { method: "POST" });
  await refreshStatus();
};
$("#btn-restart").onclick = async () => {
  await api("/api/bot/restart", { method: "POST" });
  await refreshStatus();
  await refreshLog();
};
$("#btn-refresh-log").onclick = refreshLog;

async function loadSettings() {
  const s = await api("/api/settings");
  $("#bot_token").value = s.bot_token || "";
  $("#bot_language").value = s.bot_language || "en";
  $("#vs_currency").value = s.vs_currency || "usd";
  $("#instagram_fix_host").value = s.instagram_fix_host || "kirkstagram.com";
  $("#delete_link_only_originals").checked = !!s.delete_link_only_originals;
  $("#auto_start_bot").checked = !!s.auto_start_bot;
}

$("#settings-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const hint = $("#settings-hint");
  hint.textContent = "Saving…";
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        bot_token: $("#bot_token").value.trim(),
        bot_language: $("#bot_language").value,
        vs_currency: $("#vs_currency").value.trim().toLowerCase(),
        instagram_fix_host: $("#instagram_fix_host").value,
        delete_link_only_originals: $("#delete_link_only_originals").checked,
        auto_start_bot: $("#auto_start_bot").checked,
      }),
    });
    hint.textContent = "Saved. Bot restarted if it was running.";
    await refreshStatus();
  } catch (err) {
    hint.textContent = String(err);
  }
});

/* ---------- source editor ---------- */

let openPath = null;

function renderTree(nodes, into, depth) {
  into.innerHTML = "";
  for (const node of nodes) {
    if (node.type === "dir") {
      const wrap = document.createElement("div");
      wrap.className = "dir";
      const title = document.createElement("div");
      title.className = "dir-name";
      title.textContent = "▸ " + node.name;
      wrap.appendChild(title);
      const kids = document.createElement("div");
      renderTree(node.children || [], kids, depth + 1);
      wrap.appendChild(kids);
      into.appendChild(wrap);
    } else {
      const btn = document.createElement("button");
      btn.className = "file";
      btn.textContent = node.name;
      btn.dataset.path = node.path;
      btn.onclick = () => openFile(node.path, btn);
      into.appendChild(btn);
    }
  }
}

async function loadTree() {
  const data = await api("/api/files");
  renderTree(data.tree, $("#file-tree"), 0);
}

async function openFile(path, btn) {
  const data = await api("/api/files/read?path=" + encodeURIComponent(path));
  openPath = path;
  $("#open-path").textContent = path;
  const area = $("#code");
  area.disabled = false;
  area.value = data.content;
  $("#btn-save").disabled = false;
  document.querySelectorAll(".tree button.file").forEach((b) => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
}

$("#btn-save").onclick = async () => {
  if (!openPath) return;
  await api("/api/files/save", {
    method: "POST",
    body: JSON.stringify({ path: openPath, content: $("#code").value }),
  });
  $("#open-path").textContent = openPath + " · saved — restart the bot to apply";
};

$("#btn-reset-src").onclick = async () => {
  if (!confirm("Replace the editable source with the packaged copy? Your file edits will be lost. Settings stay.")) {
    return;
  }
  await api("/api/files/reset", { method: "POST" });
  openPath = null;
  $("#code").value = "";
  $("#code").disabled = true;
  $("#btn-save").disabled = true;
  $("#open-path").textContent = "Pick a file";
  await loadTree();
};

refreshStatus().catch((err) => {
  $("#bot-pill").textContent = "dashboard error";
  $("#log").textContent = String(err);
});
refreshLog().catch(() => {});
loadSettings().catch(() => {});
loadTree().catch((err) => {
  $("#file-tree").textContent = String(err);
});
setInterval(() => {
  refreshStatus().catch(() => {});
}, 5000);
