const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
if (tg) { tg.ready(); tg.expand(); }
let USER = "owner";
try {
  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) USER = "tg" + tg.initDataUnsafe.user.id;
  else USER = localStorage.getItem("slimefi_user") || "owner";
} catch (e) {}

const $ = id => document.getElementById(id);
let S = null;

async function api(path, body) {
  const opt = { headers: { "X-User-Id": USER, "Content-Type": "application/json", "X-Init-Data": tg ? (tg.initData || "") : "" } };
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
function render() {
  if (!S) return;
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

  $("ref-code").textContent = S.referral_code;
  $("squad-line").textContent = S.invites + " friends joined - Squad boost +" + S.squad_boost_pct + "%";

  document.querySelectorAll("[data-upgrade]").forEach(b => b.onclick = () => act("/api/upgrade", { building_id: b.dataset.upgrade }));
  document.querySelectorAll("[data-refine]").forEach(b => b.onclick = () => act("/api/refine", { recipe_id: b.dataset.refine }));
  document.querySelectorAll("[data-quest]").forEach(b => b.onclick = () => act("/api/quest/claim", { quest_id: b.dataset.quest }));
}
async function act(path, body) {
  const r = await api(path, body || {});
  if (r.state) { S = r.state; render(); }
  if (r.msg) toast(r.msg);
  else if (r.ok && r.got) toast("Collected: " + fmtCost(r.got));
  else if (r.ok && r.reward) toast("Daily reward: +" + r.reward + " coins");
  else if (r.ok) toast("Ho gaya");
}
async function load() {
  S = await api("/api/state"); render();
  try {
    const ref = new URLSearchParams(location.search).get("ref");
    if (ref && !localStorage.getItem("slimefi_ref_done")) {
      localStorage.setItem("slimefi_ref_done", "1");
      act("/api/referral/apply", { code: ref });
    }
  } catch (e) {}
}

$("btn-collect").onclick = () => act("/api/collect");
$("btn-daily").onclick = () => act("/api/daily");
$("btn-ref").onclick = () => {
  const code = $("ref-input").value.trim();
  if (code) act("/api/referral/apply", { code });
};
document.querySelectorAll(".tabbar button").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tabbar button").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".pane").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  $("tab-" + b.dataset.tab).classList.add("active");
});
load();
setInterval(load, 30000);
