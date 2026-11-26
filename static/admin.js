(() => {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const toastEl = $('#toast');
  let toastTimer;
  let activeTab = 'overview';
  let badgeReq = 0;
  let notificationReq = 0;

  function toast(message) {
    toastEl.textContent = message;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('show'), 2800);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: options.body ? { 'Content-Type': 'application/json' } : {},
      ...options,
    });
    let data = {};
    try { data = await response.json(); } catch (_) { /* ignore */ }
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function table(headers, rows) {
    if (!rows.length) return '<div class="empty">چیزی برای نمایش نیست.</div>';
    return `<table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
      <tbody>${rows.join('')}</tbody></table>`;
  }

  function fmtDate(ts) {
    if (!ts) return '-';
    return new Date(Number(ts) * (String(ts).length < 11 ? 1000 : 1)).toLocaleDateString('fa-IR');
  }

  function money(n) { return Number(n || 0).toLocaleString('fa-IR'); }

  /* ---------- auth ---------- */
  $('#loginForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const err = $('#loginError');
    err.hidden = true;
    try {
      await api('/api/admin/login', {
        method: 'POST',
        body: JSON.stringify({ password: $('#adminPassword').value }),
      });
      $('#loginView').hidden = true;
      $('#appView').hidden = false;
      bootDashboard();
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  });

  $('#logoutBtn').addEventListener('click', async () => {
    await api('/api/admin/logout', { method: 'POST' });
    location.reload();
  });

  async function checkSession() {
    try {
      const data = await api('/api/admin/session');
      if (data.authenticated) {
        $('#loginView').hidden = true;
        $('#appView').hidden = false;
        bootDashboard();
      }
    } catch (_) { /* show login */ }
  }

  /* ---------- tabs ---------- */
  const loaders = {};
  $$('.nav').forEach((btn) => {
    btn.addEventListener('click', () => {
      $$('.nav').forEach((n) => n.classList.remove('active'));
      $$('.tab').forEach((t) => t.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      activeTab = tab;
      $(`#tab-${tab}`).classList.add('active');
      if (loaders[tab]) loaders[tab]();
    });
  });
  document.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-go]');
    if (!btn) return;
    const nav = $(`.nav[data-tab="${btn.dataset.go}"]`);
    if (nav) nav.click();
  });

  /* ---------- dashboard ---------- */
  async function loadHealth() {
    try {
      const h = await api('/api/health');
      const xui = $('#xuiBadge');
      xui.textContent = `x-ui: ${h.xui && h.xui.ok ? 'OK (' + h.xui.inbounds + ')' : 'OFF'}`;
      xui.className = 'badge ' + (h.xui && h.xui.ok ? 'ok' : 'bad');
      const bot = $('#botBadge');
      bot.textContent = `ربات: ${h.bot ? 'ON' : 'OFF'}`;
      bot.className = 'badge ' + (h.bot ? 'ok' : 'bad');
      const servers = $('#serverBadge');
      servers.textContent = `سرورها: ${h.servers ?? 1}`;
      servers.className = 'badge info';
    } catch (_) {
      $('#xuiBadge').textContent = 'x-ui: OFF';
      $('#xuiBadge').className = 'badge bad';
      $('#serverBadge').textContent = 'سرورها: ?';
    }
  }

  loaders.overview = async () => {
    const [stats, orders] = await Promise.all([
      api('/api/admin/overview'),
      api('/api/admin/orders'),
    ]);
    const labels = {
      users: '👤 کاربران', customers: '🧑‍💼 مشتریان', plans: '📦 پلنها',
      pending: '🛒 در انتظار', approved: '✅ تاییدشده',
      active_configs: '🔗 سرویس فعال', expired_configs: '⏰ منقضی',
      revenue: '💰 فروش (تومان)', wallet_total: '👛 موجودی کیفها (تومان)',
    };
    const moneyKeys = new Set(['revenue', 'wallet_total']);
    $('#statCards').innerHTML = Object.entries(labels).map(([k, label]) =>
      `<div class="stat"><b>${moneyKeys.has(k) ? money(stats[k]) : esc(stats[k])}</b><span>${label}</span></div>`
    ).join('');
    $('#quickActions').innerHTML = [
      ['orders', '🛒', `${stats.pending} سفارش منتظر`, 'تایید سفارش'],
      ['extra', '💳', `${window.__extraPending ?? 0} شارژ منتظر`, 'تایید شارژ اضافه'],
      ['support', '🎧', `${window.__ticketOpen ?? 0} تیکت باز`, 'پاسخ سریع'],
      ['servers', '🖥', 'همگامسازی زنده', 'سرورها و اینابندها'],
      ['backup', '💾', 'بکاپ فوری', 'دانلود JSON'],
    ].map(([tab, icon, title, sub]) => `<button class="quick-card" data-go="${tab}">
      <b>${icon} ${esc(title)}</b><span>${esc(sub)}</span></button>`).join('');
    $('#pendingDot').classList.toggle('off', !stats.pending);
    $('#recentOrders').innerHTML = table(
      ['#', 'کاربر', 'پلن', 'وضعیت', 'تاریخ'],
      orders.items.slice(0, 6).map((o) => `<tr>
        <td>${o.id}</td><td>${esc(o.username || o.telegram_id)}</td>
        <td>${esc(o.plan_name || '?')}</td>
        <td><span class="pill ${o.status === 'approved' ? 'ok' : o.status === 'pending' ? 'warn' : 'bad'}">${esc(o.status)}</span></td>
        <td>${fmtDate(o.created_at)}</td></tr>`)
    );
  };

  /* ---------- live badges / insights ---------- */
  async function loadBadges() {
    const seq = ++badgeReq;
    try {
      const n = await api('/api/admin/notifications');
      if (seq !== badgeReq) return;
      $('#unreadDot').classList.toggle('off', !n.unread);
      window.__unread = n.unread;
    } catch (_) { /* keep previous state */ }
    try {
      const s = await api('/api/admin/support');
      if (seq !== badgeReq) return;
      window.__ticketOpen = s.open || 0;
      $('#supportOpen').textContent = `${s.open || 0} باز`;
      $('#ticketDot').classList.toggle('off', !s.open);
    } catch (_) { /* keep previous state */ }
    try {
      const e = await api('/api/admin/extra-charges');
      if (seq !== badgeReq) return;
      window.__extraPending = e.pending || 0;
      $('#extraDot').classList.toggle('off', !e.pending);
    } catch (_) { /* keep previous state */ }
  }

  loaders.insights = async () => {
    const data = await api('/api/admin/insights');
    const daily = data.daily || [];
    const maxOrders = Math.max(1, ...daily.map((d) => Number(d.orders) || 0));
    const maxRevenue = Math.max(1, ...daily.map((d) => Number(d.revenue) || 0));
    $('#revenueChart').innerHTML = daily.map((d) => `
      <div class="chart-col" title="${esc(d.day)} — ${money(d.orders)} سفارش / ${money(d.revenue)} تومان">
        <div class="chart-value">${money(d.revenue)}</div>
        <div class="chart-bars">
          <span class="chart-bar orders" style="height:${Math.round((Number(d.orders) || 0) / maxOrders * 100)}%"></span>
          <span class="chart-bar revenue" style="height:${Math.round((Number(d.revenue) || 0) / maxRevenue * 100)}%"></span>
        </div>
        <span class="chart-day">${esc(String(d.day).slice(5))}</span>
      </div>`).join('') || '<div class="empty">دادهایی برای نمایش نیست.</div>';
    const o = data.overview || {};
    $('#insightCards').innerHTML = [
      ['👤 کاربران', o.users], ['🧑‍💼 مشتریان', o.customers],
      ['🛒 در انتظار', o.pending], ['✅ سرویس فعال', o.active_configs],
      ['🎧 تیکت باز', data.tickets_open], ['🔔 اعلان جدید', data.unread],
    ].map(([label, value]) => `<div class="stat"><b>${esc(value ?? 0)}</b><span>${label}</span></div>`).join('');
    loadBadges();
  };
  $('#refreshInsights').addEventListener('click', loaders.insights);

  /* ---------- orders ---------- */
  loaders.orders = async () => {
    const data = await api('/api/admin/orders');
    $('#ordersTable').innerHTML = table(
      ['#', 'کاربر', 'پلن', ' وضعیت', 'تاریخ', 'عملیات'],
      data.items.map((o) => `<tr>
        <td>${o.id}</td><td>${esc(o.username || o.telegram_id)}</td>
        <td>${esc(o.plan_name || '?')} / ${o.days || '?'} روز</td>
        <td><span class="pill ${o.status === 'approved' ? 'ok' : o.status === 'pending' ? 'warn' : 'bad'}">${esc(o.status)}</span></td>
        <td>${fmtDate(o.created_at)}</td>
        <td>${o.status === 'pending' ? `<div class="actions">
          <button class="btn ok small" data-order="approve" data-id="${o.id}">تایید</button>
          <button class="btn small" data-order="walletapprove" data-id="${o.id}">💰 کیف پول</button>
          <button class="btn danger small" data-order="reject" data-id="${o.id}">رد</button></div>` : '-'}</td></tr>`)
    );
  };

  $('#ordersTable').addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-order]');
    if (!btn) return;
    btn.disabled = true;
    try {
      const data = await api('/api/admin/orders', {
        method: 'POST',
        body: JSON.stringify({ action: btn.dataset.order, id: Number(btn.dataset.id) }),
      });
      toast(data.message || 'انجام شد');
      loaders.orders();
      loaders.overview();
    } catch (e) { toast(e.message); btn.disabled = false; }
  });

  $('#refreshOrders').addEventListener('click', loaders.orders);

  /* ---------- plans (rich server + inbound picker) ---------- */
  let planTargets = [];
  let editingPlanId = 0;
  let serversCache = [];
  let selectedServerId = 0;
  let selectedInboundIds = new Set();

  function targetLabel(target) {
    const server = serversCache.find((s) => Number(s.id) === Number(target.server_id));
    const name = server ? server.name : `سرور ${target.server_id}`;
    return `srv${target.server_id} ${name} → [${target.inbound_ids.join(',')}]`;
  }

  function renderTargets() {
    $('#targetChips').innerHTML = planTargets.map((target, index) =>
      `<span class="chip">${esc(targetLabel(target))}<button type="button" data-remove-target="${index}">×</button></span>`
    ).join('') || '<span class="empty" style="padding:4px">حداقل یک مقصد اضافه کن.</span>';
  }

  function currentServer() {
    return serversCache.find((s) => Number(s.id) === Number(selectedServerId)) || null;
  }

  function renderServerTrigger() {
    const server = currentServer();
    $('#targetServerLabel').textContent = server
      ? `${server.name} ${server.ok ? '✓' : '✗'} · ${(server.inbounds || []).length} اینابند`
      : (serversCache.length ? 'انتخاب سرور...' : 'سروری تعریف نشده');
  }

  function renderServerMenu() {
    $('#serverMenu').innerHTML = serversCache.map((s) => `
      <button type="button" class="server-option ${Number(s.id) === Number(selectedServerId) ? 'active' : ''}" data-server-pick="${s.id}">
        <b>${s.ok ? '🟢' : '🔴'} ${esc(s.name)} <span class="pill">srv${s.id}</span></b>
        <small>${esc(s.host)}${esc(s.base_path)} · ${(s.inbounds || []).length} inbound${s.error ? ' · ' + esc(s.error) : ''}</small>
      </button>`).join('') || '<div class="empty">سروری تعریف نشده.</div>';
    renderServerTrigger();
  }

  function renderInboundPicker() {
    const server = currentServer();
    const list = (server && server.inbounds) || [];
    const now = Date.now() / 1000;
    $('#targetInbounds').innerHTML = list.length ? list.map((i) => {
      const id = Number(i.id);
      const selected = selectedInboundIds.has(id);
      const fresh = i.first_seen && now - Number(i.first_seen) < 180;
      return `<button type="button" class="inbound-option ${selected ? 'selected' : ''}"
          data-inbound-toggle="${id}" ${i.enable ? '' : 'disabled'}>
          <span class="tick">${selected ? '✓' : ''}</span>
          <span>#${i.id} <b>${esc(i.remark)}</b> · ${esc(i.network || i.protocol)}:${i.port}${fresh ? ' · NEW' : ''}${i.enable ? '' : ' · خاموش'}</span>
        </button>`;
    }).join('') : '<span class="empty" style="padding:4px">اینابندی برای این سرور پیدا نشد.</span>';
    $('#selectionHint').textContent = `${selectedInboundIds.size} اینابند انتخاب شد`;
  }

  async function loadTargetServers() {
    const data = await api('/api/admin/servers');
    serversCache = data.items || [];
    if (!serversCache.some((s) => Number(s.id) === Number(selectedServerId))) {
      const preferred = serversCache.find((s) => s.ok) || serversCache[0];
      selectedServerId = preferred ? Number(preferred.id) : 0;
      selectedInboundIds.clear();
    }
    renderServerMenu();
    renderInboundPicker();
  }

  $('#targetServerBtn').addEventListener('click', (event) => {
    event.stopPropagation();
    $('#serverMenu').hidden = !$('#serverMenu').hidden;
  });
  $('#serverMenu').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-server-pick]');
    if (!btn) return;
    selectedServerId = Number(btn.dataset.serverPick);
    selectedInboundIds.clear();
    $('#serverMenu').hidden = true;
    renderServerMenu();
    renderInboundPicker();
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('.picker-block')) $('#serverMenu').hidden = true;
  });
  $('#targetInbounds').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-inbound-toggle]');
    if (!btn || btn.disabled) return;
    const id = Number(btn.dataset.inboundToggle);
    const selected = !selectedInboundIds.has(id);
    if (selected) selectedInboundIds.add(id);
    else selectedInboundIds.delete(id);
    btn.classList.toggle('selected', selected);
    const tick = btn.querySelector('.tick');
    if (tick) tick.textContent = selected ? '✓' : '';
    $('#selectionHint').textContent = `${selectedInboundIds.size} اینابند انتخاب شد`;
  });
  $('#addTarget').addEventListener('click', () => {
    const serverId = Number(selectedServerId || 0);
    const ids = Array.from(selectedInboundIds).map(Number).filter(Boolean).sort((a, b) => a - b);
    if (!serverId || !ids.length) return toast('سرور و حداقل یک اینابند انتخاب کن');
    planTargets = planTargets.filter((t) => Number(t.server_id) !== serverId);
    planTargets.push({ server_id: serverId, inbound_ids: ids });
    selectedInboundIds.clear();
    renderInboundPicker();
    renderTargets();
  });
  $('#targetChips').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-remove-target]');
    if (!btn) return;
    planTargets.splice(Number(btn.dataset.removeTarget), 1);
    renderTargets();
  });
  $('#clearPlanBtn').addEventListener('click', () => {
    planTargets = []; editingPlanId = 0; selectedInboundIds.clear();
    $('#planForm').reset();
    $('#savePlanBtn').textContent = '💾 ذخیره پلن';
    renderInboundPicker();
    renderTargets();
  });

  function planPayload() {
    const values = Object.fromEntries(new FormData($('#planForm')));
    return {
      action: editingPlanId ? 'update' : 'add', id: editingPlanId,
      name: values.name, days: Number(values.days), total_gb: Number(values.total_gb),
      price: Number(values.price), sort: Number(values.sort || 0), active: true,
      targets: planTargets,
    };
  }

  $('#savePlanBtn').addEventListener('click', async () => {
    if (!planTargets.length) return toast('حداقل یک مقصد اضافه کن');
    try {
      await api('/api/admin/plans', { method: 'POST', body: JSON.stringify(planPayload()) });
      toast(editingPlanId ? 'پلن بروزرسانی شد' : 'پلن اضافه شد');
      editingPlanId = 0; planTargets = []; selectedInboundIds.clear();
      $('#planForm').reset();
      $('#savePlanBtn').textContent = '💾 ذخیره پلن';
      renderInboundPicker();
      renderTargets();
      loaders.plans();
    } catch (e) { toast(e.message); }
  });

  loaders.plans = async () => {
    await loadTargetServers();
    const data = await api('/api/admin/plans');
    $('#plansTable').innerHTML = table(
      ['#', 'نام', 'مدت', 'حجم', 'قیمت', 'مقصدها', 'وضعیت', 'عملیات'],
      data.items.map((p) => `<tr>
        <td>${p.id}</td><td>${esc(p.name)}</td><td>${p.days} روز</td>
        <td>${p.total_gb} GB</td><td>${money(p.price)}</td>
        <td>${esc((p.targets || []).map(targetLabel).join(' | ') || '-')}</td>
        <td><span class="pill ${p.active ? 'ok' : 'bad'}">${p.active ? 'فعال' : 'غیرفعال'}</span></td>
        <td><div class="actions">
          <button class="btn small" data-plan="edit" data-id="${p.id}">✏️</button>
          <button class="btn small" data-plan="toggle" data-id="${p.id}">${p.active ? '❚❚' : '▶'}</button>
          <button class="btn danger small" data-plan="delete" data-id="${p.id}">🗑</button></div></td></tr>`)
    );
    renderTargets();
    window.__plans = data.items;
  };

  $('#plansTable').addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-plan]');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (btn.dataset.plan === 'edit') {
      const plan = (window.__plans || []).find((p) => Number(p.id) === id);
      if (!plan) return;
      editingPlanId = id;
      planTargets = plan.targets || [];
      const firstTarget = planTargets[0];
      if (firstTarget) {
        selectedServerId = Number(firstTarget.server_id);
        selectedInboundIds = new Set((firstTarget.inbound_ids || []).map(Number));
      } else {
        selectedInboundIds.clear();
      }
      const fields = $('#planForm').elements;
      fields.name.value = plan.name; fields.days.value = plan.days;
      fields.total_gb.value = plan.total_gb; fields.price.value = plan.price; fields.sort.value = plan.sort || 0;
      $('#savePlanBtn').textContent = '💾 بروزرسانی پلن';
      renderServerMenu();
      renderInboundPicker();
      renderTargets();
      $('#targetChips').scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    try {
      await api('/api/admin/plans', {
        method: 'POST',
        body: JSON.stringify({ action: btn.dataset.plan, id }),
      });
      toast('انجام شد');
      loaders.plans();
    } catch (e) { toast(e.message); }
  });

  $('#refreshPlans').addEventListener('click', loaders.plans);

  /* ---------- customers ---------- */
  loaders.customers = async () => {
    const data = await api('/api/admin/customers');
    $('#customersTable').innerHTML = table(
      ['Telegram ID', 'یوزرنیم', 'نام', 'سفارشها', 'خریدها', 'سرویس فعال', 'مبلغ پرداختی', 'آخرین سفارش', 'عملیات'],
      data.items.map((c) => `<tr>
        <td>${c.telegram_id}</td><td>${esc(c.username || '-')}</td><td>${esc(c.first_name || '-')}</td>
        <td>${c.orders_count}</td><td>${c.purchases}</td><td>${c.active_services}</td>
        <td>${money(c.spent)}</td><td>${fmtDate(c.last_order_at)}</td>
        <td><button class="btn small" data-customer="${c.telegram_id}">👁 جزئیات</button></td></tr>`)
    );
  };

  async function openCustomer(telegramId) {
    const detail = await api(`/api/admin/customers?telegram_id=${encodeURIComponent(telegramId)}`);
    const u = detail.user || {};
    const orders = detail.orders || [];
    const configs = detail.configs || [];
    const tx = detail.transactions || [];
    const purchased = orders.filter((o) => o.status === 'approved');
    $('#customerDetail').hidden = false;
    $('#customerDetail').innerHTML = `
      <div class="panel-head"><h3>پروفایل مشتری ${esc(u.username ? '@' + u.username : u.telegram_id)}</h3>
        <button class="btn small" id="closeCustomer">بستن</button></div>
      <div class="customer-card">
        <div class="stat"><b>${u.telegram_id}</b><span>Telegram ID</span></div>
        <div class="stat"><b>${esc(u.first_name || '-')}</b><span>نام</span></div>
        <div class="stat"><b>${money(u.balance)}</b><span>موجودی (تومان)</span></div>
        <div class="stat"><b>${orders.length}</b><span>کل سفارشها</span></div>
        <div class="stat"><b>${purchased.length}</b><span>تعداد خریدها</span></div>
        <div class="stat"><b>${configs.filter((c) => c.active).length}</b><span>سرویس فعال</span></div>
      </div>
      <div class="detail-block"><h4>سفارشها</h4>${table(['#', 'پلن', 'وضعیت', 'تاریخ'], orders.map((o) => `<tr>
        <td>${o.id}</td><td>${esc(o.plan_name || '?')}</td>
        <td><span class="pill ${o.status === 'approved' ? 'ok' : o.status === 'pending' ? 'warn' : 'bad'}">${esc(o.status)}</span></td>
        <td>${fmtDate(o.created_at)}</td></tr>`))}</div>
      <div class="detail-block"><h4>مدیریت سرویسها</h4>${table(['#', 'ایمیل', 'سرور', 'حجم', 'انقضاء', 'لینک', 'عملیات'], configs.map((c) => `<tr>
        <td>${c.id}</td><td>${esc(c.email)}</td><td>${esc(c.server_name || 'srv' + c.server_id)}</td>
        <td>${c.total_gb} GB</td><td>${fmtDate(c.expiry_ts)}</td>
        <td><button class="btn small" data-copy="${esc((c.links_list || [c.link]).filter(Boolean).join('\n'))}">کپی</button></td>
        <td>${c.active ? `<div class="actions">
          <button class="btn ok small" data-config="extend" data-id="${c.id}">➕ تمدید</button>
          <button class="btn danger small" data-config="delete" data-id="${c.id}">🗑</button></div>` : 'حذفشده'}</td></tr>`))}</div>
      <div class="detail-block"><h4>تراکنشهای کیف پول</h4>${table(['#', 'مبلغ', 'نوع', 'یادداشت', 'تاریخ'], tx.map((t) => `<tr>
        <td>${t.id}</td><td class="${t.amount > 0 ? 'pos' : 'neg'} wallet-line">${t.amount > 0 ? '+' : ''}${money(t.amount)}</td>
        <td>${esc(t.kind)}</td><td>${esc(t.note || '-')}</td><td>${fmtDate(t.created_at)}</td></tr>`))}</div>
      <div class="detail-block"><h4>یادداشتهای داخلی</h4>
        <form id="noteForm" class="form-row">
          <input type="hidden" name="telegram_id" value="${u.telegram_id}">
          <input name="note" placeholder="یادداشتی درباره مشتری..." required>
          <button class="btn ok small" type="submit">➕ اضافه یادداشت</button>
        </form>
        <div id="noteList">${(detail.notes || []).map((n) => `<div class="notification-item">
          ${esc(n.note)}<small>${esc(n.author)} · ${fmtDate(n.created_at)}
          <button class="btn danger small" data-note="${n.id}">🗑</button></small></div>`).join('') || '<div class="empty">یادداشتی ثبت نشده.</div>'}</div>
      </div>`;
    $('#closeCustomer').addEventListener('click', () => { $('#customerDetail').hidden = true; });
  }

  $('#customersTable').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-customer]');
    if (btn) openCustomer(Number(btn.dataset.customer)).catch((e) => toast(e.message));
  });
  $('#refreshCustomers').addEventListener('click', loaders.customers);

  /* ---------- servers / live inbounds ---------- */
  loaders.servers = async () => {
    const data = await api('/api/admin/servers');
    serversCache = data.items || [];
    const now = Date.now() / 1000;
    $('#serversTable').innerHTML = serversCache.map((s) => {
      const inbounds = (s.inbounds || []).map((i) => {
        const fresh = i.first_seen && now - Number(i.first_seen) < 180;
        return `<span class="inbound-chip ${fresh ? 'new' : ''}">#${i.id} ${esc(i.remark)} · ${esc(i.network || i.protocol)}:${i.port}${fresh ? ' · NEW' : ''}</span>`;
      }).join('') || '<span class="empty" style="padding:4px">اینابندی یافت نشد</span>';
      return `<div class="server-card">
        <div class="server-head">
          <strong>${esc(s.name)} ${s.ok ? '🟢' : '🔴'}</strong>
          <div class="actions">
            <button class="btn small" data-server="edit" data-id="${s.id}">✏️</button>
            ${Number(s.id) !== 1 ? `<button class="btn danger small" data-server="delete" data-id="${s.id}">🗑</button>` : ''}
          </div>
        </div>
        <div class="server-meta">srv${s.id} · ${esc(s.host)}${esc(s.base_path)} · public: ${esc(s.public_host)} · token: ${esc(s.token_mask || (s.token_set ? '••••' : 'تنظیم نشده'))} · sync: ${fmtDate(s.last_sync)}</div>
        ${s.error ? `<div class="err" style="margin:8px 0">${esc(s.error)}</div>` : ''}
        <div class="inbound-list">${inbounds}</div>
      </div>`;
    }).join('') || '<div class="empty">سروری تعریف نشده.</div>';
    if (!serversCache.some((s) => Number(s.id) === Number(selectedServerId))) {
      const preferred = serversCache.find((s) => s.ok) || serversCache[0];
      selectedServerId = preferred ? Number(preferred.id) : 0;
      selectedInboundIds.clear();
    }
    renderServerMenu();
    renderInboundPicker();
  };

  /* ---------- rich 3D server editor ---------- */
  let editingServerId = 0;

  function modalField(name) {
    return document.querySelector(`#serverModalForm [name="${name}"]`);
  }

  function updateServerPreview() {
    const name = modalField('name').value.trim();
    const host = modalField('host').value.trim();
    const base = modalField('base_path').value.trim();
    const pub = modalField('public_host').value.trim();
    const token = modalField('token').value;
    const bin = modalField('xray_bin').value.trim();
    const srv = serversCache.find((s) => Number(s.id) === editingServerId);
    $('#previewName').textContent = name || 'سرور بدون نام';
    $('#previewEndpoint').textContent = (host || 'http://127.0.0.1:2053') + (base || '/panel...');
    $('#previewPublic').textContent = pub || '-';
    $('#previewToken').textContent = token ? '••••' + token.slice(-4)
      : (srv && srv.token_mask ? srv.token_mask : (srv && srv.token_set ? '••••' : 'تنظیم نشده'));
    $('#previewInbounds').textContent = String((srv && (srv.inbounds || []).length) || 0);
    $('#previewBin').textContent = bin || 'xray';
    $('#previewBadge').textContent = editingServerId ? `srv${editingServerId}` : 'srv-new';
    $('#previewStatus').textContent = srv ? (srv.ok ? '● متصل' : '● آفلاین') : '● جدید';
    $('#previewStatus').className = 'pill' + (srv && srv.ok ? ' ok' : '');
  }

  function openServerModal(server) {
    editingServerId = server ? Number(server.id) : 0;
    $('#serverModalForm').reset();
    ['name', 'host', 'base_path', 'public_host', 'xray_bin'].forEach((key) => {
      modalField(key).value = server ? (server[key] || '') : '';
    });
    modalField('token').value = '';
    modalField('token').type = 'password';
    modalField('token').required = !server;
    modalField('token').placeholder = server ? 'توکن جدید یا خالی برای بدون تغییر' : 'XUI API Token';
    $('#serverModalTitle').textContent = server ? `ویرایش ${server.name}` : 'اضافه سرور جدید';
    $('#serverModalSub').textContent = server
      ? `srv${server.id} · ${(server.inbounds || []).length} inbound · ${server.ok ? 'connected' : 'offline'}`
      : 'new x-ui panel · live discovery';
    $('#tokenHint').textContent = server
      ? `توکن فعلی: ${server.token_mask || (server.token_set ? '••••' : 'تنظیم نشده')} — اگر خالی بماند تغییری نمیکند.`
      : 'توکن API پنل x-ui را وارد کن. فقط در servers.json با مجوز 600 ذخیره میشود.';
    $('#deleteServerModal').hidden = !server || Number(server.id) === 1;
    $('#serverModalInbounds').innerHTML = server
      ? ((server.inbounds || []).map((i) => `<span class="inbound-chip">#${i.id} ${esc(i.remark)} · ${esc(i.network || i.protocol)}:${i.port}</span>`).join('')
        || '<span class="empty" style="padding:4px">اینابندی پیدا نشد.</span>')
      : '<span class="empty" style="padding:4px">بعد از ذخیره، اینابندها با همگامسازی نمایان میشوند.</span>';
    updateServerPreview();
    $('#serverModal').hidden = false;
    document.body.classList.add('modal-open');
    modalField('name').focus();
  }

  function closeServerModal() {
    $('#serverModal').hidden = true;
    document.body.classList.remove('modal-open');
  }

  $('#addServerBtn').addEventListener('click', () => openServerModal(null));
  $('#closeServerModal').addEventListener('click', closeServerModal);
  $('#cancelServerModal').addEventListener('click', closeServerModal);
  $('#serverModal').addEventListener('click', (event) => {
    if (event.target.hasAttribute('data-close-modal')) closeServerModal();
  });
  $('#serverModalForm').addEventListener('input', updateServerPreview);
  $('#toggleToken').addEventListener('click', () => {
    const field = modalField('token');
    field.type = field.type === 'password' ? 'text' : 'password';
    field.focus();
  });
  $('#serverModalCard').addEventListener('mousemove', (event) => {
    const box = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - box.left) / box.width - 0.5;
    const y = (event.clientY - box.top) / box.height - 0.5;
    event.currentTarget.style.setProperty('--ry', `${(x * 5).toFixed(2)}deg`);
    event.currentTarget.style.setProperty('--rx', `${(-y * 4).toFixed(2)}deg`);
  });
  $('#serverModalCard').addEventListener('mouseleave', (event) => {
    event.currentTarget.style.setProperty('--ry', '0deg');
    event.currentTarget.style.setProperty('--rx', '0deg');
  });

  $('#saveServerModal').addEventListener('click', async () => {
    const form = $('#serverModalForm');
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    const payload = { action: editingServerId ? 'update' : 'add', ...values };
    if (editingServerId) {
      payload.id = editingServerId;
      if (!payload.token) delete payload.token;
    }
    try {
      await api('/api/admin/servers', { method: 'POST', body: JSON.stringify(payload) });
      toast(editingServerId ? 'سرور بروزرسانی شد' : 'سرور اضافه شد');
      closeServerModal();
      loaders.servers(); loadTargetServers();
    } catch (e) { toast(e.message); }
  });

  $('#deleteServerModal').addEventListener('click', async () => {
    if (!editingServerId || !confirm('این سرور حذف شود؟')) return;
    try {
      await api('/api/admin/servers', {
        method: 'POST', body: JSON.stringify({ action: 'delete', id: editingServerId }),
      });
      toast('سرور حذف شد');
      closeServerModal();
      loaders.servers(); loadTargetServers();
    } catch (e) { toast(e.message); }
  });

  $('#serversTable').addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-server]');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const current = serversCache.find((s) => Number(s.id) === id);
    if (!current) return;
    if (btn.dataset.server === 'edit') return openServerModal(current);
    if (btn.dataset.server === 'delete') {
      if (!confirm('این سرور حذف شود؟')) return;
      try {
        await api('/api/admin/servers', { method: 'POST', body: JSON.stringify({ action: 'delete', id }) });
        toast('سرور حذف شد'); loaders.servers(); loadTargetServers();
      } catch (e) { toast(e.message); }
    }
  });

  $('#syncServers').addEventListener('click', async () => {
    try {
      const data = await api('/api/admin/servers', {
        method: 'POST', body: JSON.stringify({ action: 'sync' }),
      });
      toast(data.new && data.new.length ? `${data.new.length} اینابند جدید پیدا شد` : 'همگامسازی انجام شد');
      loaders.servers();
    } catch (e) { toast(e.message); }
  });
  $('#refreshServers').addEventListener('click', loaders.servers);

  /* ---------- wallet ---------- */
  loaders.wallet = async () => {
    const data = await api('/api/admin/wallet');
    const s = data.summary || {};
    $('#walletSummary').innerHTML = [
      ['👛 کل موجودی', money(s.balance_total)],
      ['➕ جمع شارژها', money(s.topped_up)],
      ['➖ جمع خرجها', money(s.spent)],
      ['🔢 تراکنشها', money(s.transactions)],
    ].map(([label, value]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`).join('');
    $('#walletUsers').innerHTML = table(
      ['Telegram ID', 'یوزرنیم', 'نام', 'موجودی', 'عملیات'],
      (data.users || []).map((u) => `<tr>
        <td>${u.telegram_id}</td><td>${esc(u.username || '-')}</td><td>${esc(u.first_name || '-')}</td>
        <td>${money(u.balance)}</td>
        <td><button class="btn small" data-fill-wallet="${u.telegram_id}">انتخاب</button></td></tr>`)
    );
    $('#walletTx').innerHTML = table(
      ['#', 'کاربر', 'مبلغ', 'نوع', 'یادداشت', 'تاریخ'],
      (data.transactions || []).map((t) => `<tr>
        <td>${t.id}</td><td>${t.telegram_id}</td>
        <td class="${t.amount > 0 ? 'pos' : 'neg'} wallet-line">${t.amount > 0 ? '+' : ''}${money(t.amount)}</td>
        <td>${esc(t.kind)}</td><td>${esc(t.note || '-')}</td><td>${fmtDate(t.created_at)}</td></tr>`)
    );
  };

  $('#walletUsers').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-fill-wallet]');
    if (!btn) return;
    $('#walletForm').elements.telegram_id.value = btn.dataset.fillWallet;
    $('#walletForm').scrollIntoView({ behavior: 'smooth', block: 'center' });
  });

  $('#walletForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      const data = await api('/api/admin/wallet', {
        method: 'POST',
        body: JSON.stringify({
          action: values.action,
          telegram_id: Number(values.telegram_id),
          amount: Math.abs(Number(values.amount)),
          note: values.note,
        }),
      });
      toast(`موجودی جدید: ${money(data.balance)} تومان`);
      event.currentTarget.reset();
      loaders.wallet();
      loaders.overview();
    } catch (e) { toast(e.message); }
  });
  $('#refreshWallet').addEventListener('click', loaders.wallet);

  /* ---------- extra charges (شارژ اضافه) ---------- */
  const extraKindLabel = {
    wallet: ['👛 کیف پول', (v) => `${money(v)} تومان`],
    days: ['📆 شارژ روز', (v) => `${money(v)} روز`],
    gb: ['📶 شارژ حجم', (v) => `${money(v)} GB`],
  };
  function extraKind(item) {
    return extraKindLabel[item.kind || 'wallet'] || extraKindLabel.wallet;
  }

  loaders.extra = async () => {
    const data = await api('/api/admin/extra-charges');
    $('#extraDot').classList.toggle('off', !data.pending);
    $('#extraCharges').innerHTML = table(
      ['#', 'کاربر', 'نوع شارژ', 'مبلغ', 'سرویس', 'کد پرداخت / یادداشت', 'وضعیت', 'تاریخ', 'عملیات'],
      (data.items || []).map((item) => {
        const [label, format] = extraKind(item);
        const status = item.status === 'approved' ? 'ok' : item.status === 'rejected' ? 'bad' : 'warn';
        const statusText = item.status === 'approved' ? 'تاییدشده' : item.status === 'rejected' ? 'ردشده' : 'منتظر تایید';
        const noteCell = item.status === 'pending'
          ? `<input class="input small" id="extraNote-${item.id}" placeholder="کد پرداخت" maxlength="200">`
          : esc(item.note || '-');
        const actionCell = item.status === 'pending'
          ? `<div class="actions">
               <button class="btn ok small" data-extra="approve" data-id="${item.id}">✅ تایید و اعمال</button>
               <button class="btn danger small" data-extra="reject" data-id="${item.id}">❌ رد</button>
             </div>`
          : `<span class="pill ${status}">${esc(item.decided_by || 'web')}</span>`;
        return `<tr>
          <td>${item.id}</td>
          <td>${esc(item.username || item.first_name || item.telegram_id)}<br><small>${item.telegram_id}</small></td>
          <td>${label}</td>
          <td><b>${format(item.amount)}</b></td>
          <td>${esc(item.config_email || '-')}</td>
          <td>${noteCell}</td>
          <td><span class="pill ${status}">${statusText}</span></td>
          <td>${fmtDate(item.created_at)}</td>
          <td>${actionCell}</td></tr>`;
      })
    );
  };

  async function extraAction(action, id) {
    const noteInput = $(`#extraNote-${id}`);
    const note = noteInput ? noteInput.value.trim() : '';
    try {
      const data = await api('/api/admin/extra-charges', {
        method: 'POST',
        body: JSON.stringify({ action, id, note }),
      });
      toast(data.message || (action === 'approve' ? 'شارژ تایید و اعمال شد' : 'درخواست رد شد'));
      await Promise.all([loaders.extra(), loaders.wallet(), loaders.overview(), loadBadges()]);
    } catch (e) { toast(e.message); }
  }
  $('#extraCharges').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-extra]');
    if (!btn) return;
    extraAction(btn.dataset.extra, Number(btn.dataset.id));
  });
  $('#refreshExtra').addEventListener('click', loaders.extra);

  /* ---------- configs ---------- */
  async function applyConfigAction(payload, after) {
    try {
      const data = await api('/api/admin/configs', { method: 'POST', body: JSON.stringify(payload) });
      toast(data.message || 'انجام شد');
      if (after) after();
    } catch (e) { toast(e.message); }
  }

  async function handleConfigClick(event, after) {
    const copy = event.target.closest('[data-copy]');
    if (copy) {
      try { await navigator.clipboard.writeText(copy.dataset.copy); toast('لینک کپی شد'); }
      catch (_) { toast('کپی نشد، لینک را از ربات بگیر'); }
      return;
    }
    const btn = event.target.closest('[data-config]');
    if (!btn) return;
    const payload = { action: btn.dataset.config, id: Number(btn.dataset.id) };
    if (payload.action === 'extend') {
      const days = prompt('چند روز تمدید شود؟', '30');
      if (!days) return;
      payload.days = Number(days);
      payload.gb = Number(prompt('چقدر حجم اضافه شود (GB)؟', '0') || 0);
    }
    if (payload.action === 'delete' && !confirm('از حذف این سرویس مطمئنی؟')) return;
    applyConfigAction(payload, after);
  }

  loaders.configs = async () => {
    const data = await api('/api/admin/configs');
    $('#configsTable').innerHTML = table(
      ['#', 'ایمیل', 'کاربر', 'سرور', 'اینابند', 'حجم', 'انقضاء', 'وضعیت', 'لینک', 'عملیات'],
      data.items.map((c) => `<tr>
        <td>${c.id}</td><td>${esc(c.email)}</td><td>${esc(c.username || c.telegram_id)}</td>
        <td>${esc(c.server_name || 'srv' + c.server_id)}</td><td>${c.inbound_id}</td>
        <td>${c.total_gb} GB</td><td>${fmtDate(c.expiry_ts)}</td>
        <td><span class="pill ${c.active ? 'ok' : 'bad'}">${c.active ? 'فعال' : 'حذفشده'}</span></td>
        <td><button class="btn small" data-copy="${esc((c.links_list || [c.link]).filter(Boolean).join('\n'))}">کپی</button></td>
        <td>${c.active ? `<div class="actions">
          <button class="btn ok small" data-config="extend" data-id="${c.id}">➕ تمدید</button>
          <button class="btn danger small" data-config="delete" data-id="${c.id}">🗑</button></div>` : '-'}</td></tr>`)
    );
  };

  $('#configsTable').addEventListener('click', (event) => {
    handleConfigClick(event, () => { loaders.configs(); loaders.overview(); });
  });
  $('#customerDetail').addEventListener('click', (event) => {
    handleConfigClick(event, () => { loaders.customers(); loaders.overview(); });
    const noteBtn = event.target.closest('[data-note]');
    if (noteBtn) {
      api('/api/admin/customer-notes', {
        method: 'POST', body: JSON.stringify({ action: 'delete', id: Number(noteBtn.dataset.note) }),
      }).then(() => {
        toast('یادداشت حذف شد');
        const id = $('#noteForm [name="telegram_id"]').value;
        openCustomer(id);
      }).catch((e) => toast(e.message));
    }
  });
  $('#customerDetail').addEventListener('submit', (event) => {
    if (event.target.id !== 'noteForm') return;
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target));
    api('/api/admin/customer-notes', {
      method: 'POST',
      body: JSON.stringify({ action: 'add', telegram_id: Number(values.telegram_id), note: values.note }),
    }).then(() => {
      toast('یادداشت ذخیره شد');
      openCustomer(values.telegram_id);
    }).catch((e) => toast(e.message));
  });
  $('#refreshConfigs').addEventListener('click', loaders.configs);

  /* ---------- users ---------- */
  loaders.users = async () => {
    const data = await api('/api/admin/users');
    $('#usersTable').innerHTML = table(
      ['ID', 'یوزرنیم', 'نام', 'نقش', 'وضعیت', 'تاریخ', 'عملیات'],
      data.items.map((u) => `<tr>
        <td>${u.telegram_id}</td><td>${esc(u.username || '-')}</td><td>${esc(u.first_name || '-')}</td>
        <td>${esc(u.role)}</td>
        <td><span class="pill ${u.banned ? 'bad' : 'ok'}">${u.banned ? 'مسدود' : 'فعال'}</span></td>
        <td>${fmtDate(u.created_at)}</td>
        <td><button class="btn small" data-user="${u.banned ? 'unban' : 'ban'}" data-id="${u.telegram_id}">
          ${u.banned ? '🔓 آزاد' : '⛔ مسدود'}</button></td></tr>`)
    );
  };
  $('#usersTable').addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-user]');
    if (!btn) return;
    try {
      await api('/api/admin/users', {
        method: 'POST',
        body: JSON.stringify({ action: btn.dataset.user, telegram_id: Number(btn.dataset.id) }),
      });
      toast('انجام شد');
      loaders.users();
    } catch (e) { toast(e.message); }
  });
  $('#refreshUsers').addEventListener('click', loaders.users);

  /* ---------- promos ---------- */
  loaders.promos = async () => {
    const data = await api('/api/admin/promos');
    $('#promosTable').innerHTML = table(
      ['کد', 'درصد', 'مبلغ', 'استفاده', 'وضعیت', 'عملیات'],
      data.items.map((p) => `<tr>
        <td>${esc(p.code)}</td><td>${p.percent}%</td><td>${money(p.amount)}</td>
        <td>${p.uses}/${p.max_uses || '∞'}</td>
        <td><span class="pill ${p.active ? 'ok' : 'bad'}">${p.active ? 'فعال' : 'غیرفعال'}</span></td>
        <td><button class="btn danger small" data-promo="${esc(p.code)}">🗑</button></td></tr>`)
    );
  };
  $('#promosTable').addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-promo]');
    if (!btn) return;
    try {
      await api('/api/admin/promos', { method: 'POST', body: JSON.stringify({ action: 'delete', code: btn.dataset.promo }) });
      toast('کد حذف شد');
      loaders.promos();
    } catch (e) { toast(e.message); }
  });
  $('#promoForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      await api('/api/admin/promos', { method: 'POST', body: JSON.stringify({ action: 'add', ...values }) });
      event.currentTarget.reset();
      toast('کد اضافه شد');
      loaders.promos();
    } catch (e) { toast(e.message); }
  });

  /* ---------- broadcast / settings / audit ---------- */
  $('#broadcastForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const text = new FormData(event.currentTarget).get('text');
    try {
      const data = await api('/api/admin/broadcast', { method: 'POST', body: JSON.stringify({ text }) });
      $('#broadcastResult').textContent = data.message;
      toast('پیام به صف ارسال اضافه شد');
      event.currentTarget.reset();
    } catch (e) { toast(e.message); }
  });

  async function loadBotCredentials() {
    const data = await api('/api/admin/bot-settings');
    const badge = $('#botCredStatus');
    badge.textContent = `ربات: ${data.bot_active ? 'فعال' : 'غیرفعال'} · پروکسی: ${data.proxy_active ? 'فعال' : 'غیرفعال'}`;
    badge.className = 'badge ' + (data.bot_active ? 'ok' : '');
    $('#botCredCurrent').textContent = [
      `Token: ${data.bot_token_mask || 'not set'}`,
      `Admin ID: ${data.admin_id || 'not set'}`,
      `SOCKS5: ${data.proxy_mask || 'disabled'}`,
    ].join(' · ');
  }

  function botCredentialPayload(form) {
    const values = Object.fromEntries(new FormData(form));
    return {
      action: 'save',
      bot_token: values.bot_token || '',
      admin_id: values.admin_id || '',
      proxy: values.proxy || '',
    };
  }

  loaders.settings = async () => {
    await Promise.all([
      api('/api/admin/settings').then((data) => {
        Object.entries(data).forEach(([k, v]) => {
          const el = $(`#settingsForm [name="${k}"]`);
          if (el) el.value = v;
        });
      }),
      loadBotCredentials(),
    ]);
  };

  $('#settingsForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      await api('/api/admin/settings', { method: 'POST', body: JSON.stringify(values) });
      $('#settingsResult').textContent = 'ذخیره شد ✓';
      toast('تنظیمات ذخیره شد');
    } catch (e) { toast(e.message); }
  });

  $('#botCredForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = $('#botCredResult');
    try {
      const data = await api('/api/admin/bot-settings', {
        method: 'POST',
        body: JSON.stringify(botCredentialPayload(form)),
      });
      result.textContent = data.message;
      form.reset();
      await loadBotCredentials();
      toast('تنظیمات ربات ذخیره شد');
    } catch (e) {
      result.textContent = e.message;
      toast(e.message);
    }
  });

  $('#testBotCred').addEventListener('click', async () => {
    const result = $('#botCredResult');
    const payload = botCredentialPayload($('#botCredForm'));
    payload.action = 'test';
    try {
      const data = await api('/api/admin/bot-settings', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      result.textContent = data.message;
      toast('اتصال تلگرام برقرار است');
    } catch (e) {
      result.textContent = e.message;
      toast(e.message);
    }
  });

  $('#removeBotProxy').addEventListener('click', async () => {
    const result = $('#botCredResult');
    try {
      const data = await api('/api/admin/bot-settings', {
        method: 'POST',
        body: JSON.stringify({ action: 'remove_proxy' }),
      });
      result.textContent = data.message;
      $('#botCredForm [name="proxy"]').value = '';
      await loadBotCredentials();
      toast('پروکسی حذف شد');
    } catch (e) {
      result.textContent = e.message;
      toast(e.message);
    }
  });

  loaders.audit = async () => {
    const data = await api('/api/admin/audit');
    $('#auditTable').innerHTML = table(
      ['#', 'انجامدهنده', 'عمل', 'جزئیات', 'تاریخ'],
      data.items.map((a) => `<tr><td>${a.id}</td><td>${esc(a.actor)}</td><td>${esc(a.action)}</td>
        <td>${esc(a.detail)}</td><td>${fmtDate(a.created_at)}</td></tr>`)
    );
  };

  /* ---------- support desk ---------- */
  async function openTicket(id) {
    const data = await api(`/api/admin/support?ticket_id=${encodeURIComponent(id)}`);
    const t = data.ticket || {};
    $('#ticketThread').hidden = false;
    $('#ticketTitle').textContent = `تیکت #${t.id} — ${t.subject || 'پشتیبانی'}`;
    $('#replyTicketId').value = t.id || '';
    $('#ticketMessages').innerHTML = (data.messages || []).map((m) => `
      <div class="ticket-msg ${esc(m.sender)}"><small>${esc(m.sender)} · ${fmtDate(m.created_at)}</small>${esc(m.message)}</div>
    `).join('') || '<div class="empty">پیامی ثبت نشده.</div>';
    $('#ticketMessages').scrollTop = $('#ticketMessages').scrollHeight;
  }

  loaders.support = async () => {
    const status = $('#ticketFilter').value;
    const data = await api(`/api/admin/support${status ? '?status=' + encodeURIComponent(status) : ''}`);
    window.__ticketOpen = data.open || 0;
    $('#supportOpen').textContent = `${data.open || 0} باز`;
    $('#ticketDot').classList.toggle('off', !data.open);
    $('#ticketsTable').innerHTML = table(
      ['#', 'کاربر', 'موضوع', 'پیامها', 'وضعیت', 'آخرین بروزرسانی', 'عملیات'],
      (data.items || []).map((t) => `<tr>
        <td>${t.id}</td><td>${esc(t.username ? '@' + t.username : t.telegram_id)}</td>
        <td>${esc(t.subject || '-')}</td><td>${t.messages || 0}</td>
        <td><span class="pill ${t.status === 'open' ? 'warn' : 'ok'}">${t.status === 'open' ? 'باز' : 'بسته'}</span></td>
        <td>${fmtDate(t.updated_at)}</td>
        <td><button class="btn small" data-ticket="${t.id}">👁 بازکردن</button></td></tr>`)
    );
  };

  $('#ticketFilter').addEventListener('change', loaders.support);
  $('#refreshSupport').addEventListener('click', loaders.support);
  $('#ticketsTable').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-ticket]');
    if (btn) openTicket(btn.dataset.ticket).catch((e) => toast(e.message));
  });
  $('#closeThread').addEventListener('click', () => { $('#ticketThread').hidden = true; });

  async function ticketAction(action, extra = {}) {
    const id = Number($('#replyTicketId').value || 0);
    if (!id) return;
    try {
      await api('/api/admin/support', {
        method: 'POST', body: JSON.stringify({ action, id, ...extra }),
      });
      toast(action === 'delete' ? 'تیکت حذف شد' : 'انجام شد');
      if (action === 'delete') $('#ticketThread').hidden = true;
      else openTicket(id);
      loaders.support();
      loadBadges();
    } catch (e) { toast(e.message); }
  }
  $('#closeTicket').addEventListener('click', () => ticketAction('status', { status: 'closed' }));
  $('#deleteTicket').addEventListener('click', () => {
    if (confirm('این تیکت و تمام پیامهایش حذف شود؟')) ticketAction('delete');
  });
  $('#replyForm').addEventListener('submit', (event) => {
    event.preventDefault();
    const message = $('#replyText').value.trim();
    if (!message) return;
    ticketAction('reply', { message }).then(() => { $('#replyText').value = ''; });
  });

  /* ---------- notification drawer ---------- */
  function renderNotifications(items) {
    $('#notificationList').innerHTML = (items || []).map((n) => `
      <div class="notification-item ${n.read ? '' : 'unread'}" data-notif="${n.id}" data-link="${esc(n.link || 'overview')}">
        ${esc(n.text)}<small>${esc(n.kind)} · ${fmtDate(n.created_at)}</small>
      </div>`).join('') || '<div class="empty">اعلانی وجود ندارد.</div>';
  }
  async function loadNotifications() {
    const seq = ++notificationReq;
    const data = await api('/api/admin/notifications');
    if (seq !== notificationReq) return;
    renderNotifications(data.items);
    $('#unreadDot').classList.toggle('off', !data.unread);
  }
  $('#notificationBtn').addEventListener('click', async () => {
    const panel = $('#notificationPanel');
    panel.hidden = !panel.hidden;
    if (!panel.hidden) {
      try { await loadNotifications(); } catch (e) { toast(e.message); }
    }
  });
  $('#closeNotifications').addEventListener('click', () => { $('#notificationPanel').hidden = true; });
  $('#readAllNotifications').addEventListener('click', async () => {
    try {
      await api('/api/admin/notifications', { method: 'POST', body: JSON.stringify({ action: 'read_all' }) });
      await Promise.all([loadNotifications(), loadBadges()]);
      await loaders.insights();
    } catch (e) { toast(e.message); }
  });
  $('#notificationList').addEventListener('click', async (event) => {
    const item = event.target.closest('[data-notif]');
    if (!item) return;
    await api('/api/admin/notifications', {
      method: 'POST', body: JSON.stringify({ action: 'read', id: Number(item.dataset.notif) }),
    });
    await Promise.all([loadNotifications(), loadBadges()]);
    const nav = $(`.nav[data-tab="${item.dataset.link}"]`);
    if (nav) nav.click();
  });

  /* ---------- backup / tools ---------- */
  async function exportBackup() {
    try {
      const data = await api('/api/admin/export');
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      $('#backupPreview').textContent = JSON.stringify({
        exported_at: fmtDate(data.exported_at),
        tables: Object.fromEntries(Object.entries(data).filter(([k]) => Array.isArray(data[k])).map(([k, v]) => [k, v.length])),
      }, null, 2);
      const link = document.createElement('a');
      link.href = url;
      link.download = `coo-monster-backup-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast('بکاپ دانلود شد');
    } catch (e) { toast(e.message); }
  }
  $('#exportBtn').addEventListener('click', exportBackup);
  $('#testNotifyBtn').addEventListener('click', async () => {
    try {
      const data = await api('/api/admin/notifications', {
        method: 'POST', body: JSON.stringify({ action: 'test' }),
      });
      toast(`اعلان تست ثبت شد (غیرخوانده: ${data.unread})`);
      await Promise.all([loadBadges(), $('#notificationPanel').hidden ? Promise.resolve() : loadNotifications()]);
    } catch (e) { toast(e.message); }
  });

  /* ---------- command palette ---------- */
  const commands = $$('.nav').map((btn) => ({
    label: btn.textContent.trim(), hint: 'رفتن به بخش', tab: btn.dataset.tab,
  })).concat([
    { label: '🔄 همگامسازی سرورها', hint: 'Servers → Sync', action: 'sync' },
    { label: '🔔 خواندن همه اعلانها', hint: 'Notifications', action: 'read_all' },
    { label: '💾 دانلود بکاپ', hint: 'Export JSON', action: 'export' },
    { label: '📤 ارسال اعلان تست', hint: 'Test notification', action: 'test' },
  ]);

  function renderCommands(filter = '') {
    const q = filter.trim().toLowerCase();
    const items = commands.filter((c) => !q || c.label.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q));
    $('#commandResults').innerHTML = items.slice(0, 12).map((c, i) => `
      <button class="command-item" data-command="${i}"><span>${esc(c.label)}</span><small>${esc(c.hint)}</small></button>
    `).join('') || '<div class="empty">فرمانی پیدا نشد.</div>';
    window.__visibleCommands = items.slice(0, 12);
  }
  function openPalette() {
    $('#commandPalette').hidden = false;
    $('#commandInput').value = '';
    renderCommands();
    $('#commandInput').focus();
  }
  function closePalette() { $('#commandPalette').hidden = true; }
  $('#commandBtn').addEventListener('click', openPalette);
  $('#commandInput').addEventListener('input', (event) => renderCommands(event.target.value));
  $('#commandInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      const first = $('#commandResults .command-item');
      if (first) first.click();
    }
  });
  $('#commandResults').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-command]');
    if (!btn) return;
    const cmd = (window.__visibleCommands || [])[Number(btn.dataset.command)];
    if (!cmd) return;
    closePalette();
    if (cmd.tab) {
      const nav = $(`.nav[data-tab="${cmd.tab}"]`);
      if (nav) nav.click();
    } else if (cmd.action === 'sync') $('#syncServers').click();
    else if (cmd.action === 'read_all') $('#readAllNotifications').click();
    else if (cmd.action === 'export') {
      const nav = $('.nav[data-tab="backup"]');
      if (nav) nav.click();
      exportBackup();
    }
    else if (cmd.action === 'test') $('#testNotifyBtn').click();
  });
  $('#commandPalette').addEventListener('click', (event) => {
    if (event.target.id === 'commandPalette') closePalette();
  });
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && String(event.key).toLowerCase() === 'k') {
      event.preventDefault();
      $('#commandPalette').hidden ? openPalette() : closePalette();
    } else if (event.key === 'Escape') {
      closePalette();
      $('#notificationPanel').hidden = true;
      if (!$('#serverModal').hidden) closeServerModal();
    }
  });

  async function bootDashboard() {
    loadHealth();
    await loadBadges().catch(() => {});
    loaders.overview();
    loadTargetServers().catch(() => {});
    initScenes();
    setInterval(() => { loadHealth(); loadBadges(); loaders.overview(); }, 30000);
    setInterval(() => {
      if (activeTab === 'servers' || activeTab === 'plans') loaders.servers().catch(() => {});
      if (activeTab === 'support') loaders.support().catch(() => {});
      if (activeTab === 'extra') loaders.extra().catch(() => {});
    }, 15000);
  }

  /* ---------- 3D scenes ---------- */
  function initScenes() {
    const loginCanvas = $('#loginScene');
    const dashCanvas = $('#dashScene');
    const supportCanvas = $('#supportScene');
    if (!window.THREE) return;

    function makeScene(canvas, kind) {
      if (!canvas) return;
      let renderer;
      try {
        renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
      } catch (_) { return; }
      renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
      if (THREE.SRGBColorSpace) renderer.outputColorSpace = THREE.SRGBColorSpace;

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(45, 1, .1, 100);
      camera.position.z = kind === 'hero' ? 5.2 : kind === 'support' ? 4.9 : 6;
      scene.add(new THREE.AmbientLight(0x99bbff, .8));
      const l1 = new THREE.PointLight(0x3df5d0, 24, 20); l1.position.set(3, 4, 5); scene.add(l1);
      const l2 = new THREE.PointLight(0xff5ea8, 20, 20); l2.position.set(-4, 2, 3); scene.add(l2);

      const group = new THREE.Group();
      scene.add(group);
      const cyan = new THREE.MeshStandardMaterial({ color: 0x35e6bd, roughness: .35, metalness: .15, emissive: 0x062e27 });
      const pink = new THREE.MeshStandardMaterial({ color: 0xff5ea8, roughness: .3, metalness: .2, emissive: 0x301022 });

      if (kind === 'hero') {
        const body = new THREE.Mesh(new THREE.SphereGeometry(1, 40, 40), cyan);
        body.scale.set(1, 1.08, .92); group.add(body);
        [-.35, .35].forEach((x) => {
          const eye = new THREE.Mesh(new THREE.SphereGeometry(.23, 22, 22), new THREE.MeshStandardMaterial({ color: 0xffffff }));
          eye.position.set(x, .32, .86); group.add(eye);
          const pupil = new THREE.Mesh(new THREE.SphereGeometry(.1, 18, 18), new THREE.MeshStandardMaterial({ color: 0x0d1b2a }));
          pupil.position.set(x, .32, 1.05); group.add(pupil);
        });
        const mouth = new THREE.Mesh(new THREE.TorusGeometry(.28, .045, 10, 32, Math.PI), new THREE.MeshStandardMaterial({ color: 0x102033 }));
        mouth.position.set(0, -.32, .92); mouth.rotation.z = Math.PI; group.add(mouth);
        [-.52, .52].forEach((x, i) => {
          const horn = new THREE.Mesh(new THREE.ConeGeometry(.18, .6, 18), pink);
          horn.position.set(x, 1.08, 0);
          horn.rotation.z = i === 0 ? .35 : -.35; group.add(horn);
        });
      } else if (kind === 'support') {
        const knot = new THREE.Mesh(
          new THREE.TorusKnotGeometry(.72, .2, 96, 16, 2, 3),
          new THREE.MeshStandardMaterial({ color: 0x9b7bff, roughness: .25, metalness: .45, emissive: 0x1a0c3a })
        );
        group.add(knot);
        [0, 1, 2].forEach((i) => {
          const ring = new THREE.Mesh(
            new THREE.TorusGeometry(1.35 + i * .18, .018, 8, 80),
            new THREE.MeshBasicMaterial({ color: i % 2 ? 0xff5ea8 : 0x3df5d0, transparent: true, opacity: .55 })
          );
          ring.rotation.x = Math.PI / 2 + i * .45;
          ring.rotation.y = i * .7;
          group.add(ring);
        });
        for (let i = 0; i < 4; i += 1) {
          const bubble = new THREE.Mesh(new THREE.SphereGeometry(.16 + i * .02, 18, 18), i % 2 ? pink : cyan);
          const a = i * Math.PI / 2 + .4;
          bubble.position.set(Math.cos(a) * 1.65, Math.sin(a) * .85, Math.sin(a * 2) * .5);
          group.add(bubble);
        }
      } else {
        const core = new THREE.Mesh(
          new THREE.IcosahedronGeometry(1.35, 1),
          new THREE.MeshStandardMaterial({ color: 0x0d3a3a, roughness: .3, metalness: .5, emissive: 0x05201f, flatShading: true })
        );
        group.add(core);
        const wire = new THREE.Mesh(
          new THREE.IcosahedronGeometry(1.38, 1),
          new THREE.MeshBasicMaterial({ color: 0x3df5d0, wireframe: true, transparent: true, opacity: .55 })
        );
        group.add(wire);
        for (let i = 0; i < 14; i += 1) {
          const node = new THREE.Mesh(
            new THREE.SphereGeometry(.055 + Math.random() * .045, 12, 12),
            i % 3 === 0 ? pink : cyan
          );
          const theta = Math.random() * Math.PI * 2;
          const phi = Math.acos(2 * Math.random() - 1);
          const r = 1.9 + Math.random() * .7;
          node.position.set(r * Math.sin(phi) * Math.cos(theta), r * Math.cos(phi) * .6, r * Math.sin(phi) * Math.sin(theta));
          group.add(node);
        }
      }

      const count = 260;
      const arr = new Float32Array(count * 3);
      for (let i = 0; i < count; i += 1) {
        arr[i * 3] = (Math.random() - .5) * 12;
        arr[i * 3 + 1] = (Math.random() - .5) * 7;
        arr[i * 3 + 2] = (Math.random() - .5) * 9;
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(arr, 3));
      const particles = new THREE.Points(geo, new THREE.PointsMaterial({
        color: 0x9ffff0, size: .035, transparent: true, opacity: .7,
        depthWrite: false, blending: THREE.AdditiveBlending,
      }));
      scene.add(particles);

      function resize() {
        const w = canvas.clientWidth, h = canvas.clientHeight;
        if (!w || !h) return;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      }
      resize();
      if (window.ResizeObserver) new ResizeObserver(resize).observe(canvas);

      const clock = new THREE.Clock();
      function animate() {
        requestAnimationFrame(animate);
        const t = clock.getElapsedTime();
        group.position.y = Math.sin(t * 1.4) * .12;
        group.rotation.y = t * (kind === 'hero' ? .25 : .4);
        group.rotation.x = Math.sin(t * .5) * .12;
        particles.rotation.y = -t * .04;
        renderer.render(scene, camera);
      }
      animate();
    }

    makeScene(loginCanvas, 'hero');
    makeScene(dashCanvas, 'network');
    makeScene(supportCanvas, 'support');
  }

  checkSession();
  if (!$('#appView').hidden) initScenes();
})();
