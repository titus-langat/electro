/* ============ AVIVA front-end v3: toasts, drawer, quick-add ============ */
const $  = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const kes = n => "KSh " + Number(n || 0).toLocaleString();

// ---------- toasts ----------
function toast(msg) {
  const wrap = $(".toast-wrap") || Object.assign(document.body.appendChild(document.createElement("div")), { className: "toast-wrap" });
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}
 $$(".flash").forEach(f => setTimeout(() => f.remove(), 4500));

// ---------- cart state ----------
let cartMap = {};

async function refreshCart() {
  try {
    const j = await (await fetch("/api/cart")).json();
    cartMap = {};
    j.items.forEach(i => cartMap[i.id] = i.qty);
    paintCart(j.count, j.total, j.items);
  } catch (e) { /* offline */ }
}

function paintCart(count, total, items) {
  $("#cart-badge") && ($("#cart-badge").textContent = count);
  $("#d-total") && ($("#d-total").textContent = kes(total));
  const bar = $("#cart-bar");
  if (bar) {
    bar.classList.toggle("show", count > 0);
    document.body.classList.toggle("has-bar", count > 0);
    $("#bar-count").textContent = count;
    $("#bar-total").textContent = kes(total);
  }
  renderDrawer(items || []);
  paintSteppers();
}

function paintSteppers() {
  $$(".card").forEach(card => {
    const pid = card.querySelector("[data-pid]")?.dataset.pid;
    if (!pid) return;
    const addBtn = card.querySelector(".add-btn");
    const step   = card.querySelector(".stepper");
    const q      = cartMap[pid] || 0;
    if (!addBtn || !step) return;
    if (q > 0) { addBtn.classList.add("hidden"); step.classList.remove("hidden");
                 step.querySelector(".qval").textContent = q; }
    else       { step.classList.add("hidden");  addBtn.classList.remove("hidden"); }
  });
}

// ---------- drawer ----------
const drawer = $("#drawer"), overlay = $("#overlay");
function openDrawer()  { drawer.classList.add("open");  overlay.classList.add("show");  refreshCart(); }
function closeDrawer() { drawer.classList.remove("open"); overlay.classList.remove("show"); }
 $("#open-cart")  && $("#open-cart").addEventListener("click", openDrawer);
 $("#cart-bar")   && $("#cart-bar").addEventListener("click", openDrawer);
 $("#close-drawer") && $("#close-drawer").addEventListener("click", closeDrawer);
overlay.addEventListener("click", closeDrawer);
document.addEventListener("keydown", e => e.key === "Escape" && closeDrawer());

function renderDrawer(items) {
  const box = $("#d-items");
  if (!box) return;
  if (!items.length) {
    box.innerHTML = '<p class="d-empty">Your cart is empty.<br>Tap ＋ Add on any item 🛍️</p>';
    return;
  }
  box.innerHTML = items.map(i => `
    <div class="d-item">
      ${i.image ? `<img src="/uploads/${i.image}" alt="">`
                : `<div class="ph">🛍️</div>`}
      <div class="di-mid">
        <div class="di-name">${i.name}</div>
        <div class="di-price">${kes(i.price)} × ${i.qty} = ${kes(i.line)}</div>
      </div>
      <div class="stepper">
        <button class="qminus" data-pid="${i.id}">−</button>
        <span class="qval">${i.qty}</span>
        <button class="qplus" data-pid="${i.id}">＋</button>
      </div>
    </div>`).join("");
}

// ---------- quick add / steppers ----------
document.addEventListener("click", async e => {
  const add = e.target.closest(".add-btn");
  const plus = e.target.closest(".qplus");
  const minus = e.target.closest(".qminus");

  if (add) {
    e.preventDefault();
    await changeQty(add.dataset.pid, 1, "add");
    toast(`✓ ${add.dataset.name || "Item"} added to cart`);
    return;
  }
  if (plus) { e.preventDefault(); await changeQty(plus.dataset.pid, 1, "relative"); return; }
  if (minus) { e.preventDefault(); await changeQty(minus.dataset.pid, -1, "relative"); return; }
});

async function changeQty(pid, delta, mode) {
  if (mode === "add") {
    await fetch("/api/add-to-cart", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pid, qty: 1 }) });
  } else {
    const current = cartMap[pid] || 0;
    await fetch("/api/cart/update", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pid, qty: Math.max(0, current + delta) }) });
  }
  await refreshCart();
}

refreshCart();

// ---------- payment page: STK push + status polling ----------
const payBtn = document.getElementById("stk-btn");
if (payBtn) {
  const orderNo = payBtn.dataset.order;
  payBtn.addEventListener("click", async () => {
    payBtn.disabled = true;
    document.getElementById("pay-msg").textContent = "Sending M-Pesa prompt...";
    try {
      const j = await (await fetch(`/api/pay/${orderNo}`, { method: "POST" })).json();
      document.getElementById("pay-msg").textContent = j.msg;
    } catch { document.getElementById("pay-msg").textContent = "Network error."; }
    payBtn.disabled = false;
  });
  const simBtn = document.getElementById("sim-btn");
  if (simBtn) simBtn.addEventListener("click", async () => {
    const j = await (await fetch(`/api/simulate/${orderNo}`, { method: "POST" })).json();
    document.getElementById("pay-msg").textContent = j.msg;
  });
  setInterval(async () => {
    try {
      const j = await (await fetch(`/api/order-status/${orderNo}`)).json();
      if (j.status === "paid") location.href = `/receipt/${orderNo}`;
    } catch {}
  }, 3000);
}

// ---------- dashboard: live till payments widget ----------
const liveBox = document.getElementById("live-txns");
if (liveBox) {
  const paint = rows => {
    liveBox.querySelector("tbody").innerHTML = rows.map(x => `
      <tr><td>${x.trans_id || ""}</td><td>${x.phone || ""}</td>
      <td>${kes(x.amount)}</td><td>${x.till || ""}</td>
      <td>${x.order_no ? "linked: " + x.order_no : "unlinked"}</td></tr>`).join("")
      || '<tr><td colspan="5">Waiting for payments…</td></tr>';
  };
  paint([]);
  setInterval(async () => {
    try { paint((await (await fetch("/api/live-transactions")).json()).transactions); } catch {}
  }, 5000);
}