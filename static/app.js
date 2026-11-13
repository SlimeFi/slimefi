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
  const target = $("view-" + name);
  if (!target) { toast("Ye section available nahi hai"); return; }
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  target.classList.add("active");
  document.body.classList.remove("in-game");
  document.querySelectorAll(".bottom-nav .nav-item").forEach(b =>
    b.classList.toggle("active", b.dataset.go === name));
  window.scrollTo(0, 0);
  if (name === "tasks") loadTasks();
  if (name === "history") loadHistory();
  if (name === "support") loadSupportTickets();
}
window.go = go;

function render() {
  if (!S) return;
  // home
  const coinValue = Number(S.coins || 0) / Number(S.earn.coins_per_rupee || 1000);
  $("home-cash").textContent = coinValue.toFixed(2);
  $("home-coin-value").textContent = "₹" + coinValue.toFixed(2);
  $("home-coins-small").textContent = fmt(S.coins);
  $("home-value-small").textContent = "₹" + coinValue.toFixed(2);
  $("home-users").textContent = fmt(S.players);
  $("home-coins").textContent = fmt(S.coins);
  $("home-lifetime").textContent = fmt(S.lifetime_coins);
  const level = (S.slime && S.slime.level) || 1;
  const nextAt = S.slime && S.slime.next_at;
  const prevAt = [0,1000,6000,21000,61000,161000,411000,1011000,2511000,6511000][Math.max(0, level - 1)] || 0;
  const xpPct = nextAt ? Math.max(0, Math.min(100, ((S.lifetime_coins - prevAt) / Math.max(1, nextAt - prevAt)) * 100)) : 100;
  $("home-level-label").textContent = "SLIME LEVEL " + level;
  $("home-level-number").textContent = String(level).padStart(2, "0");
  $("home-xp-label").textContent = nextAt ? fmt(Math.max(0, nextAt - S.lifetime_coins)) + " XP to next" : "MAX LEVEL";
  $("home-xp-bar").style.width = xpPct + "%";
  $("home-xp-caption").textContent = nextAt ? fmt(S.lifetime_coins) + " / " + fmt(nextAt) + " lifetime coins" : "Top Slime reached";
  $("home-referral-caption").textContent = (S.invites || 0) + " friends in your squad · Invite more";
  $("home-pending-rew").textContent = "₹" + (S.pending_rewards_coins / S.earn.coins_per_rupee).toFixed(2);
  $("payouts-total").textContent = S.payouts_paid_count ? (S.payouts_paid_count + " payouts · ₹" + fmt(S.payouts_paid_rupees)) : "";
  const medals = ["🥇", "🥈", "🥉"];
  $("leaderboard-list").innerHTML = (S.leaderboard || []).map((p, i) =>
    `<div class="wd-row"><span>${medals[i] || (i + 1) + "."} ${esc(p.label)} · Lv ${p.level}</span><span>${fmt(p.coins)} coins</span></div>`).join("")
    || `<div class="card-sub">Abhi koi player nahi hai — pehle tum bano!</div>`;
  renderDaily();
  renderSpin();
  $("payouts-list").innerHTML = (S.recent_payouts || []).map(p =>
    `<div class="wd-row"><span>✅ ₹${p.amount_rupees} → ${esc(p.upi)}${p.txn_id ? " · 🧾 " + esc(p.txn_id) : ""}${p.utr ? " · UTR " + esc(p.utr) : ""}</span><span>${p.ts ? new Date(p.ts * 1000).toLocaleDateString("en-IN") : ""}</span></div>`).join("")
    || `<div class="card-sub">Abhi tak koi payout nahi hua — pehla payout yahin dikhega.</div>`;
  // wallet
  $("cash-amt").textContent = "₹" + coinValue.toFixed(2);
  $("wallet-coins").textContent = fmt(S.coins);
  $("wallet-coin-value").textContent = "₹" + coinValue.toFixed(2);
  $("pending-rew-line").textContent = "Pending approval: " + fmt(S.pending_rewards_coins) + " Coins = ₹" +
    (S.pending_rewards_coins / S.earn.coins_per_rupee).toFixed(2);
  $("rate-line").textContent = fmt(S.earn.coins_per_rupee) + " Coins = ₹1";
  $("min-wd").textContent = "₹" + S.earn.min_withdraw_rupees;
  if (!S.earn.payouts_enabled) { $("btn-withdraw").disabled = true; $("btn-withdraw").textContent = "Withdraw abhi band hai"; }
  else { $("btn-withdraw").disabled = false; $("btn-withdraw").textContent = "Withdraw Request bhejo"; }
  $("my-withdrawals").innerHTML = (S.my_withdrawals || []).map(w =>
    `<div class="wd-row"><span>#${w.id} · ₹${w.amount_rupees}</span><span class="st-${w.status}">${w.status === "rejected" ? "FAILED" : w.status.toUpperCase()}</span></div>${w.holder_name ? `<div class="card-sub">👤 ${esc(w.holder_name)}</div>` : ""}${w.txn_id ? `<div class="card-sub">🧾 Txn: ${esc(w.txn_id)}</div>` : ""}${w.utr ? `<div class="card-sub">🏦 UTR: ${esc(w.utr)}</div>` : ""}${w.payment_date ? `<div class="card-sub">📅 Payment Date: ${esc(w.payment_date)}</div>` : ""}`).join("");
  // referral
  $("ref-code2").textContent = S.referral_code;
  $("squad-line2").textContent = S.invites + " friends joined · Har dost ki pehli activity par +500 coins";
  $("ref-apply-card").style.display = S.has_referrer ? "none" : "block";
  $("ref-applied-note").style.display = S.has_referrer ? "block" : "none";
  $("ref-total").textContent = "Total Refer: " + (S.invites || 0);
  const QLABEL = { 0: "Joined — pehli activity baki", 1: "Reward mila ✅", 2: "Review me hai ⏳", 3: "Reward nahi mila" };
  $("my-ref-list").innerHTML = (S.my_referrals || []).length
    ? (S.my_referrals || []).map(r => `<div class="wd-row"><span>${esc(r.label)}</span><span>${QLABEL[r.q] || ""}</span></div>`).join("")
    : `<div class="card-sub">Abhi koi referral nahi — apna code dosto ko bhejo.</div>`;
  $("ref-link-line").textContent = "Dost ko ye link bhejo: t.me/SlimeFi_bot?start=" + S.referral_code;
  // ad card
  const e = S.earn;
  $("ad-count").textContent = e.ads_today + "/" + e.ad_daily_limit;
  const adBtn = $("btn-ad");
  if (e.ads_today >= e.ad_daily_limit) { adBtn.disabled = true; adBtn.textContent = "Aaj ke ads khatam — kal aana"; }
  else if (e.ad_cooldown_left > 0) { adBtn.disabled = true; adBtn.textContent = "Agla ad " + e.ad_cooldown_left + " sec me"; }
  else { adBtn.disabled = false; adBtn.textContent = "▶ Watch Ad (+" + e.ad_reward_coins + " coins)"; }
  $("ad-note").textContent = e.ads_live ? "" : "Note: ads judna baki hai — abhi reward system test mode me hai.";

  // admin panel
  if (S.is_owner) { $("owner-panel").style.display = "block"; loadOwner(); }
  else { $("owner-panel").style.display = "none"; }

}
async function act(path, body) {
  const r = await api(path, body || {});
  if (r.state) { S = r.state; render(); }
  if (r.msg) toast(r.msg);
  else if (r.ok && r.got) toast("Collected: " + fmtCost(r.got));
  else if (r.ok && r.reward) toast("Reward: +" + r.reward + " coins");
  else if (r.ok) toast("Ho gaya");
}
function showBan(notice) {
  let el = document.getElementById("ban-screen");
  if (!el) { el = document.createElement("div"); el.id = "ban-screen"; document.body.appendChild(el); }
  el.innerHTML = `<div class="ban-card"><div class="ban-emoji">🚫</div><div class="ban-text"></div></div>`;
  el.querySelector(".ban-text").textContent = notice || "MULTIPLE ACCOUNTS DETECTED";
  document.querySelectorAll(".bottomnav, .tabbar").forEach(n => n.style.display = "none");
}
async function load() {
  S = await api("/api/state");
  if (!S || S.ok === false) { if (S && S.msg) toast(S.msg); S = null; return; }
  if (S.banned) { showBan(S.ban_notice); return; }
  render();
  try {
    const ref = new URLSearchParams(location.search).get("ref");
    if (ref && !localStorage.getItem("slimefi_ref_done")) {
      localStorage.setItem("slimefi_ref_done", "1");
      act("/api/referral/apply", { code: ref });
    }
  } catch (e) {}
}

// ---------- daily bonus + promo ----------
function renderDaily() {
  const table = S.daily_table || [50, 75, 100, 150, 200, 300, 500];
  const cd = S.daily_cycle_day || 1;
  const claimed = !S.daily_available;
  $("daily-streak").textContent = "Streak: " + fmt(S.streak || 0) + " din";
  $("daily-coins").textContent = fmt(S.coins);
  $("daily-cal").innerHTML = table.map((amt, i) => {
    const d = i + 1;
    const cls = d < cd || (claimed && d === cd) ? "done" : (!claimed && d === cd ? "current" : "");
    const mark = cls === "done" ? "✅" : (d === 7 ? "👑" : "🎁");
    return `<div class="day-cell ${cls}"><small>Day ${d}</small><b>${mark}</b><span>+${amt}</span></div>`;
  }).join("");
  const btn = $("daily-claim");
  if (claimed) { btn.textContent = "Claimed ✅"; btn.disabled = true; btn.classList.add("done-btn"); }
  else { btn.textContent = "🎁 Claim — Day " + cd + " · +" + table[cd - 1] + " coins"; btn.disabled = false; btn.classList.remove("done-btn"); }
}
window.claimDaily = async () => {
  const r = await api("/api/daily", {});
  toast(r.msg || (r.ok ? "Bonus mil gaya" : "Nahi hua"));
  if (r.ok) load();
};
window.redeemPromo = async () => {
  const code = $("promo-code").value.trim();
  if (!code) { toast("Pehle promo code dalo"); return; }
  const r = await api("/api/promo/redeem", { code });
  toast(r.msg || "Ho gaya");
  if (r.ok) { $("promo-code").value = ""; load(); }
};

// ---------- sponsored tasks ----------
let LAST_TASKS = [];
let TASK_CAT = "all";
const CAT_LABEL = { telegram: "➤ TELEGRAM", twitter: "𝕏 TWITTER", other: "▦ OTHER" };
const CAT_ICON = { telegram: "📢", twitter: "𝕏", other: "🎯" };
async function loadTasks() {
  const box = $("tasks-list");
  box.innerHTML = `<div class="card"><div class="card-sub">Loading…</div></div>`;
  const r = await api("/api/tasks");
  if (!r.ok) { box.innerHTML = ""; return; }
  LAST_TASKS = r.tasks;
  renderTasks();
}
function renderTasks() {
  const box = $("tasks-list");
  const list = LAST_TASKS.filter(t => TASK_CAT === "all" || (t.category || "telegram") === TASK_CAT);
  if (!LAST_TASKS.length) {
    box.innerHTML = `<div class="card"><div class="card-title">Abhi koi sponsored task nahi hai</div>
      <div class="card-sub">Naye paid tasks yahan aayenge — businesses ke campaigns shuru hote hi.</div></div>`;
    return;
  }
  if (!list.length) {
    box.innerHTML = `<div class="card"><div class="card-sub">Is category me abhi koi task nahi hai.</div></div>`;
    return;
  }
  box.innerHTML = list.map(t => `
    <div class="card task-card">
      <div class="task-head">
        <span class="task-ico">${CAT_ICON[t.category || "telegram"]}</span>
        <div class="task-main">
          <div class="task-title">${esc(t.title)}</div>
          <div class="card-sub">${esc(t.description || "")}</div>
        </div>
        <div class="task-reward">+${fmt(t.reward_coins)}</div>
      </div>
      <div class="task-actions">
        ${t.my_status === "approved" ? `<span class="done-pill">✅ COMPLETED</span>`
          : t.my_status === "pending" ? `<span class="pend-pill">⏳ Admin check me hai</span>`
          : `${t.link ? `<button class="btn-open" data-u="${esc(t.link)}" onclick="openTaskLink(this.dataset.u)">🔗 Open</button>` : ""}
             ${t.verify_mode === "channel"
                ? `<button class="btn-claim" onclick="channelCheck(${t.id})">✅ Check Karo</button>`
                : `<button class="btn-claim" onclick="claimTask(${t.id})">✔ Complete Karo</button>`}`}
      </div>
    </div>`).join("");
}
window.openTaskLink = (url) => {
  if (!url) return;
  try { if (url.includes("t.me/") && tg?.openTelegramLink) { tg.openTelegramLink(url); return; } } catch (e) {}
  try { if (tg?.openLink) { tg.openLink(url); return; } } catch (e) {}
  window.open(url, "_blank");
};
window.channelCheck = async (taskId) => {
  const r = await api("/api/tasks/" + taskId + "/channel-check", {});
  toast(r.msg || (r.ok ? "Ho gaya" : "Nahi hua"));
  if (r.ok) load();
};
window.grantCoins = async () => {
  const user_id = $("gr-user").value;
  const coins = parseInt($("gr-amount").value, 10) || 0;
  if (!user_id) { toast("Pehle player chuno"); return; }
  if (coins <= 0) { toast("Coins ki matra dalo"); return; }
  const r = await api("/api/owner/grant", { user_id, coins });
  toast(r.msg || "Ho gaya");
  if (r.ok) { $("gr-amount").value = ""; loadOwner(); }
};
window.createPromo = async () => {
  const code = $("np-code").value.trim();
  const coins = parseInt($("np-coins").value, 10) || 0;
  const max_uses = parseInt($("np-uses").value, 10) || 0;
  if (!code || coins <= 0) { toast("Code aur coins dalo"); return; }
  const r = await api("/api/owner/promos", { code, coins, max_uses });
  toast(r.msg || "Ho gaya");
  if (r.ok) { $("np-code").value = ""; $("np-coins").value = ""; $("np-uses").value = ""; loadOwner(); }
};
window.deactivatePromo = async (code) => {
  const r = await api("/api/owner/promos/deactivate", { code });
  toast(r.msg || "Ho gaya");
  loadOwner();
};
window.reduceCoins = async () => {
  const user_id = $("gr-user").value;
  const coins = parseInt($("gr-amount").value, 10) || 0;
  if (!user_id) { toast("Pehle player chuno"); return; }
  if (coins <= 0) { toast("Coins ki matra dalo"); return; }
  const r = await api("/api/owner/reduce", { user_id, coins });
  toast(r.msg || "Ho gaya");
  if (r.ok) { $("gr-amount").value = ""; loadOwner(); }
};
window.revDecide = async (id, action) => {
  const r = await api("/api/owner/referrals/decide", { id, action });
  toast(r.msg || "Ho gaya");
  loadOwner();
};
window.unbanUser = async (userId) => {
  const r = await api("/api/owner/unban", { user_id: userId });
  toast(r.msg || "Ho gaya");
  loadOwner();
};
window.claimTask = async (taskId) => {
  const r = await api("/api/tasks/" + taskId + "/claim", {});
  toast(r.msg || (r.ok ? "Ho gaya" : "Nahi hua"));
  if (r.ok) load();
};
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
        " · Bot Starts: " + st.bot_starts +
        " · Coin Liability: ₹" + Number(st.estimated_coin_value_rupees || 0).toFixed(2) +
        " · Open Tickets: " + (st.open_support_tickets || 0);
      const sel = $("gr-user");
      const keep = sel.value;
      sel.innerHTML = (st.players || []).map(p =>
        `<option value="${esc(p.user_id)}">${p.uname ? "@" + esc(p.uname) : esc(p.user_id)} — ${fmt(p.coins)} coins</option>`).join("");
      if (keep) sel.value = keep;
      $("owner-promos").innerHTML = (st.promos || []).map(p =>
        `<div class="wd-row"><span>🎟️ ${esc(p.code)} · ${fmt(p.coins)} coins</span><span>${p.active ? "ON" : "BAND"} · ${fmt(p.used_count)}/${p.max_uses ? fmt(p.max_uses) : "∞"} use${p.active ? ` · <a href="#" onclick="deactivatePromo('${p.code}');return false;">band karo</a>` : ""}</span></div>`).join("")
        || `<div class="card-sub">Abhi koi promo code nahi hai.</div>`;
      $("owner-recent").innerHTML = (st.recent || []).map(x =>
        `<div class="wd-row"><span>${x.uname ? "@" + esc(x.uname) + " · " : ""}${esc(x.user_id.replace("tg", "TG "))} · ${fmt(x.lifetime_coins)} coins</span>` +
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
      const rf = await api("/api/owner/referrals");
      if (rf.ok) {
        $("owner-referrals").innerHTML = (rf.reviews || []).map(r => `
          <div class="owner-row">
            <div class="wd-row"><span>${esc(r.referrer)} → ${esc(r.referred)}</span><span>${fmt(r.reward_coins)} coins HOLD</span></div>
            <div class="card-sub">Dono ek hi network se hain — reward tumhare faisle par ruka hai.</div>
            <div class="ref-row"><button onclick="revDecide(${r.id},'approve')">Approve — Reward Do</button><button onclick="revDecide(${r.id},'reject')">Reject</button></div>
          </div>`).join("") || `<div class="card-sub">Review me kuch nahi hai</div>`;
        $("owner-suspicious").innerHTML = (rf.suspicious || []).map(x => `
          <div class="owner-row">
            <div class="wd-row"><span>⚠️ ${esc(x.label)}</span><span>${fmt(x.lifetime_coins)} coins</span></div>
            <div class="card-sub">${esc(x.reason || "")} — sirf network match hai, doshi pakka nahi. Jaanch karke faisla lo — ye account band nahi hai.</div>
          </div>`).join("") || `<div class="card-sub">Koi suspicious account nahi hai</div>`;
        $("owner-banned").innerHTML = (rf.banned || []).map(b => `
          <div class="owner-row">
            <div class="wd-row"><span>${esc(b.label)}</span><span>${fmt(b.lifetime_coins)} coins</span></div>
            <div class="card-sub">${esc(b.reason || "")}</div>
            <div class="ref-row"><button onclick="unbanUser('${b.user_id}')">Unban Karo</button></div>
          </div>`).join("") || `<div class="card-sub">Koi banned account nahi hai</div>`;
      }
      $("owner-tasks").innerHTML = (sb.tasks || []).map(t =>
        `<div class="wd-row"><span>Task #${t.id} · ${esc(t.title)}</span><span>${CAT_LABEL[t.category || "telegram"]} · ${fmt(t.reward_coins)} coins${t.budget_coins ? ` · Budget ${fmt(t.spent_coins || 0)}/${fmt(t.budget_coins)}` : ""} · ${t.active ? "ON" : "OFF"}</span></div>`).join("");
    }
  } catch (e) {}
  const r = await api("/api/owner/withdrawals");
  if (!r.ok) return;
  $("owner-summary").textContent = "Pending: " + r.pending_count + " requests · ₹" + r.pending_rupees;
  const support = await api("/api/owner/support/tickets");
  if (support.ok) {
    $("owner-support-tickets").innerHTML = (support.tickets || []).map(t => `
      <div class="owner-row"><div class="wd-row"><span>#${t.id} · ${esc(t.user_id)} · ${esc(t.category)}</span><b>${esc(t.status)}</b></div>
      <div class="card-sub">${esc(t.message)}</div>
      ${t.status === "open" ? `<button onclick="closeSupportTicket(${t.id})">Close Ticket</button>` : ""}</div>`).join("") || '<div class="card-sub">No support tickets.</div>';
  }
  $("owner-list").innerHTML = r.withdrawals.map(w => `
    <div class="owner-row">
      <div class="wd-row"><span>#${w.id} · ${esc(w.user_id)}</span><span class="st-${w.status}">${w.status === "rejected" ? "FAILED" : w.status.toUpperCase()}</span></div>
      <div class="card-sub">₹${w.amount_rupees} → ${esc(w.upi_id)}</div>
      ${w.holder_name ? `<div class="card-sub">👤 ${esc(w.holder_name)}</div>` : `<div class="card-sub">👤 Naam nahi diya (purani request)</div>`}
      ${w.txn_id ? `<div class="card-sub">🧾 Txn: ${esc(w.txn_id)}</div>` : ""}
      ${w.utr ? `<div class="card-sub">🏦 UTR: ${esc(w.utr)}</div>` : ""}
      ${w.payment_date ? `<div class="card-sub">📅 Payment Date: ${esc(w.payment_date)}</div>` : ""}
      ${w.status === "pending" ? `<input id="txn-${w.id}" placeholder="Transaction ID — payment ke baad dalo" style="text-transform:none"><input id="utr-${w.id}" placeholder="UTR / UPI Reference Number" style="text-transform:none"><input id="pdate-${w.id}" type="date"><div class="ref-row"><button onclick="decideWd(${w.id},'paid')">Paid kar diya</button><button onclick="decideWd(${w.id},'reject')">Reject / Failed</button></div>` : ""}
    </div>`).join("");
}
window.closeSupportTicket = async (id) => {
  const r = await api("/api/owner/support/tickets/close", {id});
  toast(r.msg || "Ho gaya");
  loadOwner();
};
window.decideWd = async (id, action) => {
  let txn_id = "", utr = "", payment_date = "";
  if (action === "paid") {
    const gx = p => { const e = $(p + "-" + id); return e ? e.value.trim() : ""; };
    txn_id = gx("txn"); utr = gx("utr"); payment_date = gx("pdate");
    if (!txn_id && !utr) { toast("Transaction ID ya UTR dalo — wahi payment ka proof hai"); return; }
  }
  const rr = await api("/api/owner/withdrawals/decide", { id, action, txn_id, utr, payment_date });
  toast(rr.msg || "Ho gaya");
  S = await api("/api/state"); render();
};
window.decideSub = async (id, action) => {
  const rr = await api("/api/owner/submissions/decide", { id, action });
  toast(rr.msg || "Ho gaya");
  S = await api("/api/state"); render();
};

// ---------- Spin Wheel ----------
let SPIN_ROT = 0, SPINNING = false, WHEEL_KEY = "";
const WHEEL_COLORS = ["#7c4dff", "#ffb300", "#00897b", "#c2185b", "#3949ab", "#ef6c00", "#2e7d32", "#5e35b1"];
function renderSpin() {
  if (!S || !S.spin || !$("wheel")) return;
  $("spin-info").textContent = "🎟️ Free spin bacha: " + S.spin.free_left + " · Extra spin: " + S.spin.credits;
  const segs = S.spin.segments || [];
  const key = segs.join(",");
  if (!segs.length || key === WHEEL_KEY) return;
  WHEEL_KEY = key;
  const step = 360 / segs.length;
  $("wheel").style.background = "conic-gradient(from " + (-step / 2) + "deg, " +
    segs.map((v, i) => WHEEL_COLORS[i % WHEEL_COLORS.length] + " " + (i * step) + "deg " + ((i + 1) * step) + "deg").join(", ") + ")";
  document.querySelectorAll(".wheel-label").forEach(e => e.remove());
  segs.forEach((v, i) => {
    const s = document.createElement("span");
    s.className = "wheel-label";
    s.textContent = v;
    s.style.transform = "rotate(" + (i * step) + "deg) translateY(-104px)";
    $("wheel").appendChild(s);
  });
}
async function spinNow() {
  if (SPINNING) return;
  const r = await api("/api/spin", {});
  if (!r.ok) { toast(r.msg || "Spin nahi ho paya"); return; }
  SPINNING = true;
  $("spin-result").textContent = "";
  $("btn-spin").disabled = true;
  const segs = (S && S.spin && S.spin.segments) || [50, 500, 150, 300, 100, 250, 200, 100];
  const step = 360 / segs.length;
  const target = -r.index * step;
  const delta = (((target - (SPIN_ROT % 360)) % 360) + 360) % 360;
  SPIN_ROT += 360 * 5 + delta;
  $("wheel").style.transform = "rotate(" + SPIN_ROT + "deg)";
  setTimeout(() => {
    SPINNING = false;
    $("btn-spin").disabled = false;
    $("spin-result").textContent = "🎉 " + r.prize + " Coins jeete!";
    toast("🎡 Spin: +" + r.prize + " coins!");
    if (r.state) { S = r.state; render(); }
  }, 4900);
}
function spinAd() {
  // Same proven ad flow as Watch Ads (Monetag first, AdsGram fallback,
  // one-time token + age gates) — but the claim carries purpose "spin",
  // so the server pays +1 spin credit instead of the +100 coins.
  const monetagFn = window.show_12248440;
  const blockId = window.SLIMEFI_ADS_BLOCK_ID;
  const useMonetag = typeof monetagFn === "function";
  if (!useMonetag && (!blockId || !window.Adsgram)) { toast("Ad abhi available nahi hai — thodi der baad try karo"); return; }
  const startP = api("/api/ad/start", { v: 30 });
  const t0 = Date.now();
  let claimed = false;
  const tryClaim = async done => {
    if (claimed) return; claimed = true;
    const st = await startP;
    if (!st.ok) { toast(st.msg || "Spin nahi mila — fir try karo"); return; }
    const need = (done ? 10000 : 17000) - (Date.now() - t0);
    const send = async () => {
      const r = await api("/api/ad/claim", { token: st.token, done: done ? 1 : 0, purpose: "spin" });
      if (r.ok) { toast("🎡 +1 Spin mila!"); if (r.state) { S = r.state; render(); } }
      else toast(r.msg || "Spin nahi mila — fir try karo");
    };
    if (need > 0) setTimeout(send, need); else send();
  };
  let settled = false;
  const onDone = d => { settled = true; tryClaim(d); };
  const onFail = () => {
    settled = true;
    if (Date.now() - t0 >= 15000) tryClaim(false);
    else toast("Ad load nahi hua — fir try karo");
  };
  if (useMonetag) {
    monetagFn()
      .then(res => {
        if (res && res.reward_event_type === "non_valued") { settled = true; toast("Is ad ka reward nahi bana — ek aur try karo"); return; }
        onDone(true);
      })
      .catch(onFail);
  } else {
    window.Adsgram.init({ blockId }).show()
      .then(res => {
        settled = true;
        if (Date.now() - t0 >= 15000 || (res && res.done)) tryClaim(!!(res && res.done));
        else toast("Ad pura dekho, tabhi spin milega");
      })
      .catch(onFail);
  }
  const iv = setInterval(() => {
    if (claimed || settled) { clearInterval(iv); return; }
    if (Date.now() - t0 >= 20000) { clearInterval(iv); tryClaim(false); }
  }, 1000);
}

// ---------- reward history + support ----------
async function loadHistory() {
  const box = $("history-list"); if (!box) return;
  box.innerHTML = '<div class="card-sub">Loading…</div>';
  const r = await api("/api/history");
  if (!r.ok) { box.textContent = "History load nahi hui."; return; }
  box.innerHTML = (r.history || []).map(x => `<div class="wd-row"><span>${esc(x.note || x.type)}</span><b>${x.coins > 0 ? "+" : ""}${fmt(x.coins)} 🪙</b></div><div class="card-sub">${x.ts ? new Date(x.ts*1000).toLocaleString("en-IN") : ""}</div>`).join("") || '<div class="card-sub">Abhi koi activity nahi hai.</div>';
}
async function loadSupportTickets() {
  const box = $("support-ticket-list"); if (!box) return;
  const r = await api("/api/support/tickets");
  box.innerHTML = '<h3>My Support Tickets</h3>' + ((r.tickets || []).map(t => `<div class="wd-row"><span>#${t.id} · ${esc(t.category)}</span><b>${esc(t.status)}</b></div><div class="card-sub">${esc(t.message)}</div>`).join("") || '<div class="card-sub">Abhi koi ticket nahi hai.</div>');
}
$("support-submit").onclick = async () => {
  const message = $("support-message").value.trim();
  const category = $("support-category").value;
  const r = await api("/api/support/tickets", {category, message});
  toast(r.msg || "Request submit nahi hui");
  if (r.ok) { $("support-message").value = ""; loadSupportTickets(); }
};

// ---------- wiring ----------
$("btn-spin").onclick = spinNow;
$("btn-spin-ad").onclick = spinAd;
$("btn-withdraw").onclick = () => {
  const upi = $("upi-input").value.trim();
  const amt = Number($("wd-input").value);
  const holder_name = $("wd-name").value.trim();
  if (!holder_name) { toast("Account holder ka naam dalo"); return; }
  if (upi && Number.isFinite(amt) && amt > 0) act("/api/withdraw", { upi_id: upi, amount_rupees: amt.toFixed(2), holder_name });
  else toast("UPI ID aur amount dono dalo");
};
$("btn-ad").onclick = () => {
  const monetagFn = window.show_12248440;
  const blockId = window.SLIMEFI_ADS_BLOCK_ID;
  const useMonetag = typeof monetagFn === "function";
  if (!useMonetag && (!blockId || !window.Adsgram)) { toast("Ad abhi available nahi hai — thodi der baad try karo"); return; }
  // Token fetch and ad start TOGETHER: the ad must open straight from the
  // tap — if we wait for the network first, the webview blocks the ad.
  const startP = api("/api/ad/start", { v: 30 });
  const t0 = Date.now();
  let claimed = false;
  const tryClaim = async done => {
    if (claimed) return; claimed = true;
    const st = await startP;
    if (!st.ok) { toast(st.msg || "Reward nahi mila — fir try karo"); return; }
    // The server rejects claims whose one-time token is too young (done:
    // under 8s, no-signal: under 15s) WITHOUT burning the token — wait
    // out the margin here so a genuine claim is not lost.
    const need = (done ? 10000 : 17000) - (Date.now() - t0);
    const send = () => act("/api/ad/claim", { token: st.token, done: done ? 1 : 0 });
    if (need > 0) setTimeout(send, need); else send();
  };
  // Some Telegram clients play the ad but NEVER answer the SDK promise
  // (their WebApp bridge drops the completion callback) — the ad closes
  // and nothing happens. So: claim from whichever comes first — the SDK
  // settling after 15s+, or a 20-second timer. The server still gates
  // every claim on the one-time token's age (15s+ without the signal),
  // so instant closes and load failures stay unpaid.
  let settled = false;
  const onDone = isDone => { settled = true; tryClaim(isDone); };
  const onFail = () => {
    settled = true;
    if (Date.now() - t0 >= 15000) tryClaim(false);
    else toast("Ad load nahi hua — fir try karo");
  };
  if (useMonetag) {
    // Monetag Rewarded Interstitial, zone 12248440. Its promise resolves
    // after the ad closes: reward_event_type 'valued' means the view is
    // paid; 'non_valued' means this ad earns nothing — no claim then.
    monetagFn()
      .then(res => {
        if (res && res.reward_event_type === "non_valued") {
          settled = true;
          toast("Is ad ka reward nahi bana — ek aur try karo");
          return;
        }
        onDone(true);
      })
      .catch(onFail);
  } else {
    // AdsGram fallback (test block).
    window.Adsgram.init({ blockId }).show()
      .then(res => {
        settled = true;
        if (Date.now() - t0 >= 15000 || (res && res.done)) tryClaim(!!(res && res.done));
        else toast("Ad pura dekho, tabhi coins milenge");
      })
      .catch(onFail);
  }
  const iv = setInterval(() => {
    if (claimed || settled) { clearInterval(iv); return; }
    if (Date.now() - t0 >= 20000) { clearInterval(iv); tryClaim(false); }
  }, 1000);
};
window.openPayouts = () => {
  const url = "https://SlimeFi.pythonanywhere.com/payouts";
  try { if (tg && tg.openLink) { tg.openLink(url); return; } } catch (e) {}
  window.open(url, "_blank");
};
$("btn-ref").onclick = () => {
  const code = $("ref-input").value.trim();
  if (code) act("/api/referral/apply", { code });
};
$("btn-nt").onclick = async () => {
  const body = { title: $("nt-title").value.trim(), description: $("nt-desc").value.trim(),
                 link: $("nt-link").value.trim(), reward_coins: parseInt($("nt-reward").value, 10) || 0,
                 budget_coins: parseInt($("nt-budget").value, 10) || 0,
                 category: $("nt-category").value, verify_mode: $("nt-mode").value };
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
$("btn-copy").onclick = async () => {
  if (!S) return;
  try { await navigator.clipboard.writeText(S.referral_code); toast("Invite code copy ho gaya"); }
  catch (e) { toast("Invite code: " + S.referral_code); }
};
$("btn-share").onclick = () => {
  if (!S) return;
  const link = "https://t.me/SlimeFi_bot?start=" + S.referral_code;
  const shareUrl = "https://t.me/share/url?url=" + encodeURIComponent(link) +
    "&text=" + encodeURIComponent("SlimeFi par mere saath earn karo!");
  if (tg && tg.openTelegramLink) tg.openTelegramLink(shareUrl);
  else toast("Dost ko ye link bhejo: " + link);
};
document.querySelectorAll(".bottom-nav .nav-item").forEach(b =>
  b.onclick = () => go(b.dataset.go));
document.querySelectorAll(".chip[data-cat]").forEach(ch => ch.onclick = () => {
  TASK_CAT = ch.dataset.cat;
  document.querySelectorAll(".chip[data-cat]").forEach(x =>
    x.classList.toggle("active", x === ch));
  renderTasks();
});
load();
setInterval(load, 30000);

// ===== Slime pet transformation (from the user's video) =====
// The slime hops around; every few seconds the magic rings flare, a flash
// bursts, and it transforms into the slime-girl for a few seconds.
(function () {
  const stage = document.querySelector(".pet-stage");
  if (!stage) return;
  let girl = false;
  const swap = () => {
    if (document.hidden) { setTimeout(swap, 2000); return; }
    girl = !girl;
    stage.classList.add("tf-flash");
    setTimeout(() => {
      stage.classList.toggle("tf-on", girl);
      stage.classList.remove("tf-flash");
    }, 240);
    setTimeout(swap, girl ? 4500 : 8000);
  };
  setTimeout(swap, 5000);
})();
