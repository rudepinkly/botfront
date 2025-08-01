// static/script.js

const webapp = window.Telegram?.WebApp;
if (webapp) {
  webapp.expand();
}
const params = new URLSearchParams(window.location.search);
const chat_id = params.get("chat_id");
let initData = webapp?.initData || "";

function log(...args) {
  console.log("[app]", ...args);
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body)
  });
  return res.json();
}

function formatProfile(user, crew) {
  return `
    <div class="stat"><strong>Рейтинг:</strong> ${user.rating}</div>
    <div class="stat"><strong>Титул:</strong> ${user.title}</div>
    <div class="stat"><strong>Стрик:</strong> ${user.streak} дней</div>
    <div class="stat"><strong>Престиж:</strong> x${user.prestige_multiplier.toFixed(2)}</div>
    <div class="stat"><strong>Клан:</strong> ${crew || "—"}</div>
  `;
}

async function loadProfile() {
  const res = await postJSON("/api/profile", { init_data: initData, chat_id });
  if (!res.ok) {
    document.getElementById("profile-body").innerText = "Ошибка загрузки профиля";
    return;
  }
  const user = res.user;
  document.getElementById("username").innerText = user.username || "Anon";
  document.getElementById("profile-body").innerHTML = formatProfile(user, res.crew);
  // Топ
  const list = document.getElementById("top-list");
  list.innerHTML = "";
  res.top.forEach((t, i) => {
    const el = document.createElement("div");
    el.className = "top-row";
    el.innerHTML = `${i+1}. <strong>${t.username}</strong> — ${t.rating}`;
    list.appendChild(el);
  });
}

async function doDaily() {
  const btn = document.getElementById("daily-btn");
  btn.disabled = true;
  const res = await postJSON("/api/daily", { init_data: initData, chat_id });
  if (!res.ok) {
    alert("Ошибка: " + (res.error || "unknown"));
  } else {
    animateRatingUpdate(res.user);
    showToast(`Δ ${res.daily.delta} рейтинга!`);
    document.getElementById("profile-body").innerHTML = formatProfile(res.user, null);
  }
  btn.disabled = false;
}

function animateRatingUpdate(user) {
  const card = document.getElementById("profile");
  card.classList.add("pulse");
  setTimeout(() => card.classList.remove("pulse"), 500);
}

function showToast(text) {
  let existing = document.getElementById("toast");
  if (existing) existing.remove();
  const t = document.createElement("div");
  t.id = "toast";
  t.style = "position:fixed;bottom:20px;right:20px;padding:12px 18px;background:rgba(255,255,255,.1);border:1px solid #9b79ff;border-radius:8px;font-weight:600;";
  t.innerText = text;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2500);
}

// Колесо

let spinning = false;
async function spinWheel() {
  if (spinning) return;
  spinning = true;
  const wheel = document.getElementById("wheel-wheel");
  // рандомное вращение (например от 3 до 5 оборотов + рандом)
  const extra = Math.floor(Math.random() * 360);
  const rotations = 4 * 360 + extra;
  wheel.style.transition = "transform 4s cubic-bezier(.17,.67,.83,.67)";
  wheel.style.transform = `rotate(${rotations}deg)`;
  // запрос к API параллельно
  const res = await postJSON("/api/wheel", { init_data: initData, chat_id });

  // после окончания анимации показать результат
  setTimeout(() => {
    let desc = res.wheel?.description || "—";
    document.getElementById("wheel-result").innerText = `Результат: ${desc}`;
    if (res.user) {
      document.getElementById("profile-body").innerHTML = formatProfile(res.user, null);
    }
    showToast(`Колесо: ${desc}`);
    // сброс трансформации плавно
    wheel.style.transition = "none";
    const actualDeg = rotations % 360;
    wheel.style.transform = `rotate(${actualDeg}deg)`;
    setTimeout(() => {
      wheel.style.transition = "";
      spinning = false;
    }, 50);
  }, 4200);
}

// Слот

async function spinSlot() {
  const r1 = document.getElementById("r1");
  const r2 = document.getElementById("r2");
  const r3 = document.getElementById("r3");
  const resultEl = document.getElementById("slot-result");

  // короткая анимация бегущих символов
  const symbols = ["🍒", "🍋", "🔔", "⭐", "7️⃣"];
  let count = 0;
  const interval = setInterval(() => {
    r1.innerText = symbols[Math.floor(Math.random() * symbols.length)];
    r2.innerText = symbols[Math.floor(Math.random() * symbols.length)];
    r3.innerText = symbols[Math.floor(Math.random() * symbols.length)];
    count++;
    if (count > 10) clearInterval(interval);
  }, 80);

  const res = await postJSON("/api/slot", { init_data: initData, chat_id });
  setTimeout(() => {
    if (res.slot) {
      r1.innerText = res.slot.result[0];
      r2.innerText = res.slot.result[1];
      r3.innerText = res.slot.result[2];
      if (res.slot.payout > 0) {
        resultEl.innerText = `Выигрыш: ${res.slot.payout}!`;
        showToast(`+${res.slot.payout} рейтинга`);
      } else {
        resultEl.innerText = `Ничего, попробуй ещё.`;
      }
      if (res.user) {
        document.getElementById("profile-body").innerHTML = formatProfile(res.user, null);
      }
    }
  }, 1000);
}

// Инициализация событий
document.getElementById("daily-btn").addEventListener("click", doDaily);
document.getElementById("spin-wheel-btn").addEventListener("click", spinWheel);
document.getElementById("spin-slot-btn").addEventListener("click", spinSlot);

loadProfile();
