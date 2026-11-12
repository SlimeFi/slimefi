const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
if (tg) { tg.ready(); tg.expand(); }
let USER = "owner";
try {
  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) USER = "tg" + tg.initDataUnsafe.user.id;
  else USER = localStorage.getItem("slimefi_user") || "owner";
} catch (e) {}

// Owner's personal panel link: t.me/SlimeFi_bot/game?startapp=<key>
// The key can arrive four ways depending on how the game was opened:
// Telegram start param, ?startapp=, ?k= (direct owner link), or the
// tgWebAppStartParam in the URL hash that Telegram appends itself.
let OKEY = "";
try {
  const hashParams = new URLSearchParams((location.hash || "").replace(/^#/, ""));
  const sp = (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param)
    || new URLSearchParams(location.search).get("startapp")
    || new URLSearchParams(location.search).get("k")
    || hashParams.get("tgWebAppStartParam") || "";
  if (sp) localStorage.setItem("slimefi_k", sp);
  OKEY = localStorage.getItem("slimefi_k") || "";
} catch (e) {}

const $ = id => document.getElementById(id);
const fmt = n => Number(n || 0).toLocaleString("en-IN");
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let S = null;

async function api(path, body) {
  const opt = { headers: { "X-User-Id": USER, "Content-Type": "application/json", "X-Init-Data": tg ? (tg.initData || "") : "", ...(OKEY ? { "X-Owner-Key": OKEY } : {}) } };
  if (body) { opt.method = "POST"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  return r.json();
}
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2600);
}
function fmtCost(c) {
  const p = [];
  if (c.coins) p.push(c.coins + " coins");
  if (c.wood) p.push(c.wood + " wood");
  if (c.crystals) p.push(c.crystals + " crystals");
  return p.length ? p.join(" + ") : "Free";
}

// ---------- view navigation ----------
function go(name) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  $("view-" + name).classList.add("active");
  window.scrollTo(0, 0);
  if (name === "tasks") loadTasks();
}
window.go = go;

function render() {
  if (!S) return;
  // home
  $("home-cash").textContent = "₹" + Number(S.cash_rupees).toFixed(2);
  $("home-users").textContent = fmt(S.players);
  $("home-coins").textContent = fmt(S.coins);
  $("home-pending").textContent = fmt(S.pending.coins + S.pending.wood + S.pending.crystals);
  $("home-level").textContent = "Lv " + S.slime.level + " " + S.slime.name;
  // wallet
  $("cash-amt").textContent = "₹" + Number(S.cash_rupees).toFixed(2);
  $("wallet-coins").textContent = fmt(S.coins);
  $("pending-rew-line").textContent = "Pending rewards: " + fmt(S.pending_rewards_coins) + " coins (≈ ₹" +
    (S.pending_rewards_coins / S.earn.coins_per_rupee).toFixed(2) + ") — admin approval ke baad milenge";
  $("rate-line").textContent = S.earn.coins_per_rupee + " coins = ₹1";
  $("min-wd").textContent = "₹" + S.earn.min_withdraw_rupees;
  if (!S.earn.payouts_enabled) { $("btn-withdraw").disabled = true; $("btn-withdraw").textContent = "Withdraw abhi band hai"; }
  else { $("btn-withdraw").disabled = false; $("btn-withdraw").textContent = "Withdraw Request bhejo"; }
  $("my-withdrawals").innerHTML = (S.my_withdrawals || []).map(w =>
    `<div class="wd-row"><span>#${w.id} · ₹${w.amount_rupees}</span><span class="st-${w.status}">${w.status.toUpperCase()}</span></div>`).join("");
  // referral
  $("ref-code").textContent = S.referral_code;
  $("ref-code2").textContent = S.referral_code;
  $("squad-line").textContent = S.invites + " friends joined - Squad boost +" + S.squad_boost_pct + "%";
  $("squad-line2").textContent = S.invites + " friends joined · Har dost ki pehli activity par +500 coins";
  $("ref-link-line").textContent = "Dost ko ye link bhejo: t.me/SlimeFi_bot?start=" + S.referral_code;
  // game header + guardian
  $("r-coins").textContent = S.coins;
  $("r-wood").textContent = S.wood;
  $("r-crystals").textContent = S.crystals;
  $("r-level").textContent = "Lv " + S.slime.level;
  $("slime-name").textContent = S.slime.name;
  $("slime-sub").textContent = "Town Guardian - Level " + S.slime.level;
  if (S.slime.next_at) {
    const prevNeed = [0,1000,6000,21000,61000,161000,411000,1011000,2511000,6511000][S.slime.level - 1];
    const pct = Math.min(100, Math.round((S.lifetime_coins - prevNeed) / (S.slime.next_at - prevNeed) * 100));
    $("level-bar").style.width = pct + "%";
    $("level-text").textContent = S.lifetime_coins + " / " + S.slime.next_at + " coins to Lv " + (S.slime.level + 1);
  } else {
    $("level-bar").style.width = "100%";
    $("level-text").textContent = "MAX LEVEL";
  }
  $("pending-line").textContent = "Pending: " + S.pending.coins + " coins, " + S.pending.wood + " wood, " + S.pending.crystals + " crystals";
  $("btn-daily").disabled = !S.daily_available;
  $("btn-daily").textContent = S.daily_available ? "Claim Daily Reward" : "Daily Reward Claimed - Kal Phir Aana";

  $("buildings").innerHTML = S.buildings.map(b => `
    <div class="card">
      <div class="card-top"><div class="card-title">${b.name}</div><div class="lvl">Lv ${b.level}</div></div>
      <div class="card-sub">${b.resource ? ("Production: " + b.rate_per_hour + " " + b.resource + " / hour") : "Building upgrades ki limit badhata hai"}</div>
      <div class="card-sub">Upgrade cost: <span class="cost">${fmtCost(b.next_cost)}</span>${b.capped_by_forge ? " - Dwarf Forge pehle upgrade karo" : ""}</div>
      <button data-upgrade="${b.id}">${b.level === 0 ? "Build" : "Upgrade"}</button>
    </div>`).join("");

  $("recipes").innerHTML = S.recipes.map(r => `
    <div class="card">
      <div class="card-title">${r.title}</div>
      <div class="card-sub">Give: <span class="cost">${fmtCost(r.give)}</span> &nbsp; Get: <span class="cost">${fmtCost(r.get)}</span></div>
      <button data-refine="${r.id}">Refine</button>
    </div>`).join("");

  $("quests").innerHTML = S.quests.map(q => `
    <div class="card">
      <div class="card-top"><div class="card-title">${q.title}</div><div class="lvl">${q.progress}/${q.target}</div></div>
      <div class="card-sub">Reward: <span class="cost">${fmtCost(q.reward)}</span></div>
      <button data-quest="${q.id}" ${q.claimed || q.progress < q.target ? "disabled" : ""}>${q.claimed ? "Claimed" : "Claim Reward"}</button>
    </div>`).join("");

  // ad card
  const e = S.earn;
  $("ad-count").textContent = e.ads_today + "/" + e.ad_daily_limit;
  const adBtn = $("btn-ad");
  if (e.ads_today >= e.ad_daily_limit) { adBtn.disabled = true; adBtn.textContent = "Aaj ke ads khatam — kal aana"; }
  else if (e.ad_cooldown_left > 0) { adBtn.disabled = true; adBtn.textContent = "Agla ad " + e.ad_cooldown_left + " sec me"; }
  else { adBtn.disabled = false; adBtn.textContent = "Watch Ad (+" + e.ad_reward_coins + " coins)"; }
  $("ad-note").textContent = e.ads_live ? "" : "Note: asal AdsGram ads judna baki hai — abhi reward system test mode me hai.";

  // admin panel
  if (S.is_owner) { $("owner-panel").style.display = "block"; loadOwner(); }
  else { $("owner-panel").style.display = "none"; }

  document.querySelectorAll("[data-upgrade]").forEach(b => b.onclick = () => act("/api/upgrade", { building_id: b.dataset.upgrade }));
  document.querySelectorAll("[data-refine]").forEach(b => b.onclick = () => act("/api/refine", { recipe_id: b.dataset.refine }));
  document.querySelectorAll("[data-quest]").forEach(b => b.onclick = () => act("/api/quest/claim", { quest_id: b.dataset.quest }));
}
async function act(path, body) {
  const r = await api(path, body || {});
  if (r.state) { S = r.state; render(); }
  if (r.msg) toast(r.msg);
  else if (r.ok && r.got) toast("Collected: " + fmtCost(r.got));
  else if (r.ok && r.reward) toast("Reward: +" + r.reward + " coins");
  else if (r.ok) toast("Ho gaya");
}
async function load() {
  S = await api("/api/state");
  if (!S || S.ok === false) { if (S && S.msg) toast(S.msg); S = null; return; }
  render();
  try {
    const ref = new URLSearchParams(location.search).get("ref");
    if (ref && !localStorage.getItem("slimefi_ref_done")) {
      localStorage.setItem("slimefi_ref_done", "1");
      act("/api/referral/apply", { code: ref });
    }
  } catch (e) {}
}

// ---------- sponsored tasks ----------
async function loadTasks() {
  const box = $("tasks-list");
  box.innerHTML = `<div class="card"><div class="card-sub">Loading…</div></div>`;
  const r = await api("/api/tasks");
  if (!r.ok) { box.innerHTML = ""; return; }
  if (!r.tasks.length) {
    box.innerHTML = `<div class="card"><div class="card-title">Abhi koi sponsored task nahi hai</div>
      <div class="card-sub">Naye paid tasks yahan aayenge — businesses ke campaigns shuru hote hi.</div></div>`;
    return;
  }
  box.innerHTML = r.tasks.map(t => `
    <div class="card">
      <div class="card-top"><div class="card-title">${esc(t.title)}</div><div class="lvl">+${fmt(t.reward_coins)} coins</div></div>
      <div class="card-sub">${esc(t.description || "")}</div>
      ${t.link ? `<div class="card-sub">Link: ${esc(t.link)}</div>` : ""}
      ${t.my_status === "approved" ? `<div class="card-sub st-paid">✅ Reward mil gaya</div>`
        : t.my_status === "pending" ? `<div class="card-sub">⏳ Proof admin check me hai</div>`
        : t.my_status === "rejected" ? `<div class="card-sub st-rejected">❌ Proof reject hua — sahi proof dobara bhejo</div>
             <div class="ref-row"><input id="proof-${t.id}" placeholder="Proof — screenshot link / username" style="text-transform:none">
             <button class="small-btn" onclick="submitProof(${t.id})">Submit</button></div>`
        : `<div class="ref-row"><input id="proof-${t.id}" placeholder="Proof — screenshot link / username" style="text-transform:none">
           <button class="small-btn" onclick="submitProof(${t.id})">Submit Proof</button></div>`}
    </div>`).join("");
}
window.submitProof = async (taskId) => {
  const v = $("proof-" + taskId).value.trim();
  if (!v) { toast("Pehle proof dalo"); return; }
  const r = await api("/api/tasks/submit", { task_id: taskId, proof: v });
  toast(r.msg || (r.ok ? "Ho gaya" : "Nahi hua"));
  if (r.ok) { loadTasks(); S = await api("/api/state"); render(); }
};

// ---------- owner / admin ----------
async function loadOwner() {
  try {
    const st = await api("/api/owner/stats");
    if (st.ok) {
      $("owner-stats").textContent =
        "Total Players: " + st.total_players + " · Aaj Join: " + st.joined_today +
        " · Bot Starts: " + st.bot_starts;
      $("owner-recent").innerHTML = (st.recent || []).map(x =>
        `<div class="wd-row"><span>${esc(x.user_id.replace("tg", "TG "))} · ${fmt(x.lifetime_coins)} coins</span>` +
        `<span>${x.created_at ? new Date(x.created_at * 1000).toLocaleDateString("en-IN") : ""}</span></div>`).join("");
    }
  } catch (e) {}
  try {
    const sb = await api("/api/owner/submissions");
    if (sb.ok) {
      $("owner-subs").innerHTML = (sb.submissions || []).map(s => `
        <div class="owner-row">
          <div class="wd-row"><span>#${s.id} · ${esc(s.task_title)} · ${esc(s.user_id)}</span><span class="st-${s.status}">${s.status.toUpperCase()}</span></div>
          <div class="card-sub">Proof: ${esc(s.proof)} · Reward: ${fmt(s.reward_coins)} coins</div>
          ${s.status === "pending" ? `<div class="ref-row"><button onclick="decideSub(${s.id},'approve')">Approve</button><button onclick="decideSub(${s.id},'reject')">Reject</button></div>` : ""}
        </div>`).join("") || `<div class="card-sub">Koi submission nahi</div>`;
      $("owner-tasks").innerHTML = (sb.tasks || []).map(t =>
        `<div class="wd-row"><span>Task #${t.id} · ${esc(t.title)}</span><span>${fmt(t.reward_coins)} coins${t.budget_coins ? ` · Budget ${fmt(t.spent_coins || 0)}/${fmt(t.budget_coins)}` : ""} · ${t.active ? "ON" : "OFF"}</span></div>`).join("");
    }
  } catch (e) {}
  const r = await api("/api/owner/withdrawals");
  if (!r.ok) return;
  $("owner-summary").textContent = "Pending: " + r.pending_count + " requests · ₹" + r.pending_rupees;
  $("owner-list").innerHTML = r.withdrawals.map(w => `
    <div class="owner-row">
      <div class="wd-row"><span>#${w.id} · ${esc(w.user_id)}</span><span class="st-${w.status}">${w.status.toUpperCase()}</span></div>
      <div class="card-sub">₹${w.amount_rupees} → ${esc(w.upi_id)}</div>
      ${w.status === "pending" ? `<div class="ref-row"><button onclick="decideWd(${w.id},'paid')">Paid kar diya</button><button onclick="decideWd(${w.id},'reject')">Reject</button></div>` : ""}
    </div>`).join("");
}
window.decideWd = async (id, action) => {
  const rr = await api("/api/owner/withdrawals/decide", { id, action });
  toast(rr.msg || "Ho gaya");
  S = await api("/api/state"); render();
};
window.decideSub = async (id, action) => {
  const rr = await api("/api/owner/submissions/decide", { id, action });
  toast(rr.msg || "Ho gaya");
  S = await api("/api/state"); render();
};

// ---------- wiring ----------
$("btn-collect").onclick = () => act("/api/collect");
$("btn-daily").onclick = () => act("/api/daily");
$("btn-convert").onclick = () => {
  const v = parseInt($("convert-input").value, 10);
  if (v > 0) act("/api/convert", { coins: v });
  else toast("Pehle coins ki sankhya dalo");
};
$("btn-withdraw").onclick = () => {
  const upi = $("upi-input").value.trim();
  const amt = parseInt($("wd-input").value, 10);
  if (upi && amt > 0) act("/api/withdraw", { upi_id: upi, amount_rupees: amt });
  else toast("UPI ID aur amount dono dalo");
};
$("btn-ad").onclick = () => {
  const claim = () => act("/api/ad/claim");
  const blockId = window.SLIMEFI_ADS_BLOCK_ID;
  if (blockId && window.Adsgram) {
    try {
      window.Adsgram.init({ blockId }).show()
        .then(res => { if (res && res.done) claim(); else toast("Ad pura dekho, tabhi coins milenge"); })
        .catch(() => toast("Ad abhi nahi chal paya, thodi der baad try karo"));
      return;
    } catch (err) {}
  }
  $("btn-ad").disabled = true;
  toast("Ad chal raha hai...");
  setTimeout(claim, 3000);
};
$("btn-ref").onclick = () => {
  const code = $("ref-input").value.trim();
  if (code) act("/api/referral/apply", { code });
};
$("btn-nt").onclick = async () => {
  const body = { title: $("nt-title").value.trim(), description: $("nt-desc").value.trim(),
                 link: $("nt-link").value.trim(), reward_coins: parseInt($("nt-reward").value, 10) || 0,
                 budget_coins: parseInt($("nt-budget").value, 10) || 0 };
  const r = await api("/api/owner/tasks", body);
  toast(r.msg || "Ho gaya");
  if (r.ok) { $("nt-title").value = ""; $("nt-desc").value = ""; $("nt-link").value = ""; $("nt-reward").value = ""; $("nt-budget").value = ""; loadOwner(); }
};
document.querySelectorAll(".tabbar button").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tabbar button").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".pane").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  $("tab-" + b.dataset.tab).classList.add("active");
});
load();
setInterval(load, 30000);
