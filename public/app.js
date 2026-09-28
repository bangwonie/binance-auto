const $ = id => document.getElementById(id);
const fmt = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
const time = value => value ? new Date(Number(value)).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' }) : '—';
let mode = 'paper', dashboard = null, hover = -1;
let liveTicker = null, liveCandle = null, liveTradePrice = null, socket = null, reconnectTimer = null, reconnectDelay = 1000, lastPacketAt = 0, lastTickerAt = 0, lastExchangeEventAt = 0, lastTransportLagMs = null, renderTimer = null, accountEvents = null, recentOrders = [];
function notice(value) { $('notice').textContent = value; $('notice').hidden = !value; }
function currentPrice() { if (Number(liveTradePrice) > 0 && Date.now() - lastTickerAt < 5000) return Number(liveTradePrice); const streamed = Number(liveTicker?.c); if (streamed > 0 && Date.now() - lastTickerAt < 15000) return streamed; const bid = Number(dashboard?.bestBook?.bid || dashboard?.depth?.bids?.[0]?.[0]), ask = Number(dashboard?.bestBook?.ask || dashboard?.depth?.asks?.[0]?.[0]); return bid > 0 && ask > 0 ? (bid + ask) / 2 : dashboard?.points?.at(-1)?.close; }
async function load() { try { const response = await fetch(`/api/dashboard?mode=${mode}`, { cache: 'no-store' }); const result = await response.json(); if (!response.ok) throw new Error(result.error || response.status); if (result.mode !== mode) return; dashboard = result; if (!liveCandle || Number(result.currentCandle?.time) >= Number(liveCandle.time)) liveCandle = result.currentCandle; if (mode === 'real') dashboard.state.history = [...recentOrders, ...(dashboard.state.history || [])].slice(0, 100); render(); notice(mode === 'real' && !result.accountStream?.configured ? 'Thị trường Real đang trực tiếp. Tài khoản Real chưa kết nối API.' : ''); } catch (error) { notice(`Không tải được dữ liệu: ${error.message}`); } }
async function refreshAccount() { if (!dashboard) return; const selected = mode; try { const response = await fetch(`/api/account?mode=${selected}`, { cache: 'no-store' }); const result = await response.json(); if (!response.ok || selected !== mode) return; dashboard.state = result.state; if (mode === 'real') dashboard.state.history = [...recentOrders, ...(dashboard.state.history || [])].slice(0, 100); dashboard.account = result.account; dashboard.accountStream = result.accountStream; dashboard.automation = result.automation; render(); } catch { /* Keep the last known balance and retry on the next interval. */ } }
function setFeed(state) { const dot = $('feedDot'); dot.classList.toggle('feed-wait', state === 'wait'); dot.classList.toggle('feed-off', state === 'off'); $('feedStatus').textContent = state === 'live' ? 'Giá trực tiếp' : state === 'wait' ? 'Đang kết nối giá' : 'Giá đang gián đoạn'; }
function scheduleRender() { if (renderTimer) return; renderTimer = setTimeout(() => { renderTimer = null; if (dashboard) render(); }, 250); }
function connectMarket() {
  clearTimeout(reconnectTimer); setFeed('wait');
  const streams = mode === 'real' ? 'btcusdt@aggTrade/btcusdt@ticker/btcusdt@bookTicker/btcusdt@depth10@100ms/btcusdt@kline_5m' : 'btcusdt@ticker/btcusdt@depth10@100ms/btcusdt@kline_5m';
  const streamHost = mode === 'real' ? 'wss://fstream.binance.com' : mode === 'futures_demo' ? 'wss://fstream.binancefuture.com' : 'wss://stream.testnet.binance.vision';
  const connection = new WebSocket(`${streamHost}/stream?streams=${streams}`);
  socket = connection;
  connection.addEventListener('open', () => { reconnectDelay = 1000; });
  connection.addEventListener('message', event => {
    let packet; try { packet = JSON.parse(event.data); } catch { return; }
    const item = packet.data; if (!item || !packet.stream) return;
    lastPacketAt = Date.now(); lastExchangeEventAt = Number(item.E) || lastExchangeEventAt; if (Number(item.E)) lastTransportLagMs = Math.max(0, Date.now() - Number(item.E)); setFeed('live');
    if (packet.stream.endsWith('@ticker')) { liveTicker = item; lastTickerAt = Date.now(); }
    else if (packet.stream.endsWith('@aggTrade')) { liveTradePrice = Number(item.p); lastTickerAt = Date.now(); }
    else if (packet.stream.endsWith('@bookTicker') && dashboard) {
      dashboard.bestBook = { bid: item.b, ask: item.a };
      const price = (Number(item.b) + Number(item.a)) / 2;
      if (mode === 'real' && Number.isFinite(price)) {
        const barTime = Math.floor(Date.now() / 300000) * 300000;
        if (!liveCandle || liveCandle.time !== barTime) liveCandle = { time: barTime, open: price, high: price, low: price, close: price, volume: 0 };
        else liveCandle = { ...liveCandle, high: Math.max(Number(liveCandle.high || price), price), low: Math.min(Number(liveCandle.low || price), price), close: price };
      }
    }
    else if (packet.stream.includes('@depth10') && dashboard) {
      const bids = item.bids || item.b;
      const asks = item.asks || item.a;
      if (bids && asks) dashboard.depth = { bids, asks };
    }
    else if (packet.stream.endsWith('@kline_5m') && item.k) {
      if (item.k.x) { liveCandle = null; load(); }
      else liveCandle = { time: Number(item.k.t), close: Number(item.k.c) };
    }
    scheduleRender();
  });
  connection.addEventListener('close', () => { if (socket !== connection) return; setFeed('off'); liveTicker = null; liveTradePrice = null; liveCandle = null; load(); reconnectTimer = setTimeout(connectMarket, reconnectDelay); reconnectDelay = Math.min(reconnectDelay * 2, 30000); });
  connection.addEventListener('error', () => connection.close());
}
function renderBook(id, rows, kind) { const element = $(id); element.replaceChildren(); const shown = (kind === 'ask' ? rows.slice(0, 7).reverse() : rows.slice(0, 7)); const max = Math.max(...shown.map(row => Number(row[1])), 0.00001); for (const row of shown) { const div = document.createElement('div'); div.className = `book-row ${kind}`; const bar = document.createElement('i'); bar.style.width = `${Math.min(100, Number(row[1]) / max * 100)}%`; const price = document.createElement('span'); price.textContent = fmt(row[0]); const qty = document.createElement('span'); qty.className = 'qty'; qty.textContent = fmt(row[1], 5); div.append(bar, price, qty); element.append(div); } }
function renderDecision() {
  const map = dashboard.tradeMap;
  if (!map) return;
  const verdict = map.verdict;
  const node = $('mapVerdict');
  node.textContent = verdict === 'LONG' ? 'LONG' : verdict === 'SHORT' ? 'SHORT' : 'ĐỨNG NGOÀI';
  node.className = verdict.toLowerCase();
  $('mapScore').textContent = (map.score > 0 ? '+' : '') + map.score + '/' + map.maxScore;
  $('mapReason').textContent = map.confidence + '% đồng thuận · ' + map.model;
  $('mapEntry').textContent = verdict === 'WAIT' ? 'Chưa có' : fmt(map.entryLow) + ' – ' + fmt(map.entryHigh);
  $('mapStop').textContent = map.stop ? fmt(map.stop) : '—';
  $('mapTarget').textContent = map.target ? fmt(map.target) : '—';
  $('mapAtr').textContent = fmt(map.atr) + ' · ' + fmt(map.atrPct) + '%';
  $('longVotes').textContent = map.longVotes;
  $('shortVotes').textContent = map.shortVotes;
  $('neutralVotes').textContent = map.neutralVotes;
  $('engineAge').textContent = Math.max(0, Math.round((Date.now() - map.generatedAt) / 1000)) + 's';
  const grid = $('factorMap'); grid.replaceChildren();
  for (const item of map.factors) {
    const card = document.createElement('article');
    card.className = 'factor ' + (item.vote > 0 ? 'long' : item.vote < 0 ? 'short' : 'neutral');
    const head = document.createElement('div'), label = document.createElement('span'), vote = document.createElement('b');
    label.textContent = item.label; vote.textContent = item.vote > 0 ? 'LONG' : item.vote < 0 ? 'SHORT' : 'WAIT'; head.append(label, vote);
    const value = document.createElement('strong'); value.textContent = fmt(item.value, Math.abs(item.value) < 1 ? 4 : 2) + item.unit;
    const note = document.createElement('small'); note.textContent = item.detail;
    card.append(head, value, note); grid.append(card);
  }
}
function render() {
  const points = dashboard.points, last = points.at(-1), reference = points.at(-25);
  const price = currentPrice(); const streamedChange = liveTicker && Date.now() - lastTickerAt < 15000 ? Number(liveTicker.P) : NaN; const change = Number.isFinite(streamedChange) ? streamedChange : reference ? (price / reference.close - 1) * 100 : 0;
  $('price').textContent = `$${fmt(price)}`; $('chartPrice').textContent = fmt(liveCandle?.close || last.close); $('chartRange').textContent = liveCandle ? '5m · gồm nến đang chạy' : '5m · 120 nến đã đóng'; $('change').textContent = `${change >= 0 ? '+' : ''}${fmt(change)}%`; $('change').style.color = change >= 0 ? '#64d6ae' : '#ed8998';
  const signal = dashboard.tradeMap?.verdict === 'LONG' ? 'BUY' : dashboard.tradeMap?.verdict === 'SHORT' ? 'SELL' : 'HOLD';
  $('signal').textContent = signal; $('signalDetail').textContent = signal === 'HOLD' ? 'Chưa có giao cắt' : signal === 'BUY' ? (mode === 'futures_demo' ? 'Mở Long' : 'Mua BTC') : (mode === 'futures_demo' ? 'Đóng Long' : 'Bán BTC'); $('signalDetail').style.color = signal === 'BUY' ? '#64d6ae' : signal === 'SELL' ? '#ed8998' : '#e8edf5';
  $('sma20').textContent = fmt(last.sma20); $('sma50').textContent = fmt(last.sma50);
  const usdt = mode === 'paper' ? Number(dashboard.state.paper_usdt) : dashboard.account ? Number(['futures_demo', 'real'].includes(mode) ? dashboard.account.available : dashboard.account.usdt) : NaN;
  const btc = mode === 'paper' ? Number(dashboard.state.paper_btc) : dashboard.account ? Number(dashboard.account.btc) : NaN;
  const valid = Number.isFinite(usdt) && Number.isFinite(btc); const equity = valid ? (['futures_demo', 'real'].includes(mode) ? Number(dashboard.account.usdt) : usdt + btc * price) : NaN;
  $('equity').textContent = valid ? `$${fmt(equity)}` : '—'; $('usdtBalance').textContent = valid ? `${fmt(usdt)} USDT` : '— USDT'; $('btcBalance').textContent = valid ? `${fmt(btc, 6)} BTC` : '— BTC';
  $('modeLabel').textContent = mode === 'real' ? 'Futures Real' : mode === 'paper' ? 'Paper trading' : mode === 'futures_demo' ? 'Futures Demo' : 'Spot Testnet'; $('activeMode').textContent = mode === 'real' ? 'REAL · Theo dõi' : mode === 'paper' ? 'AUTO · Paper' : mode === 'futures_demo' ? 'AUTO · Futures Demo' : 'AUTO · Testnet'; $('balanceNote').textContent = mode === 'real' ? dashboard.account ? `Vị thế ${fmt(btc, 4)} BTC · ${dashboard.account.leverage || '—'}x · ${dashboard.accountStream?.connected ? 'Tài khoản realtime' : 'Đang nối lại tài khoản'}` : 'Chưa kết nối API tài khoản Real' : mode === 'futures_demo' ? `Margin balance · Position ${fmt(btc, 4)} BTC · ${dashboard.account?.leverage || '—'}x` : mode === 'paper' ? 'Paper · số dư mô phỏng' : dashboard.testnetReady ? 'Testnet · số dư tiền thử nghiệm' : 'Cần cấu hình API key Testnet';
  const starting = mode === 'paper' ? 1000 : Number(dashboard.baseline?.equity);
  if (mode === 'real' && valid) { const pnl = Number(dashboard.account.unrealizedPnl || 0), margin = Number(dashboard.account.positionInitialMargin || 0), roi = margin > 0 ? pnl / margin * 100 : NaN; $('pnl').textContent = `${pnl >= 0 ? '+' : '-'}$${fmt(Math.abs(pnl))}`; $('roi').textContent = Number.isFinite(roi) ? `${roi >= 0 ? '+' : ''}${fmt(roi)}%` : '—'; $('pnl').style.color = $('roi').style.color = pnl >= 0 ? '#68d8b2' : '#ed8998'; $('pnlNote').textContent = 'PnL chưa chốt từ Binance Futures'; }
  else if (valid && Number.isFinite(starting) && starting > 0) { const pnl = equity - starting; const roi = pnl / starting * 100; $('pnl').textContent = `${pnl >= 0 ? '+' : '-'}$${fmt(Math.abs(pnl))}`; $('roi').textContent = `${roi >= 0 ? '+' : ''}${fmt(roi)}%`; $('pnl').style.color = $('roi').style.color = pnl >= 0 ? '#68d8b2' : '#ed8998'; $('pnlNote').textContent = mode === 'paper' ? 'So với vốn paper 1,000 USDT' : `Từ ${time(dashboard.baseline.time)}`; }
  else { $('pnl').textContent = '—'; $('roi').textContent = '—'; $('pnlNote').textContent = mode === 'real' ? 'Chưa kết nối tài khoản Real' : 'Chưa có vốn gốc Testnet'; }
  $('allocation').textContent = mode === 'real' ? dashboard.account ? `${fmt(Number(btc) * price)} USDT` : '—' : mode === 'futures_demo' ? `${fmt(dashboard.account?.unrealizedPnl || 0)} USDT` : valid && equity > 0 ? `${fmt(btc * price / equity * 100, 0)}% / ${fmt(usdt / equity * 100, 0)}%` : '—';
  $('chartStart').textContent = time(points[0].time); $('chartEnd').textContent = time(liveCandle?.time || last.time);
  renderDecision();
  renderBook('asks', dashboard.depth.asks, 'ask'); renderBook('bids', dashboard.depth.bids, 'bid'); const bestBid = Number(dashboard.bestBook?.bid || dashboard.depth.bids?.[0]?.[0]), bestAsk = Number(dashboard.bestBook?.ask || dashboard.depth.asks?.[0]?.[0]); $('midPrice').textContent = fmt((bestBid + bestAsk) / 2);
  const history = dashboard.state.history || []; $('activityCount').textContent = `${history.length} sự kiện`; const tbody = $('historyBody'); tbody.replaceChildren();
  if (!history.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = 6; td.className = 'empty'; td.textContent = mode === 'real' ? 'Lệnh Real mới sẽ xuất hiện khi kết nối tài khoản.' : 'Bot chưa ghi nhận giao dịch hoặc tín hiệu mới.'; tr.append(td); tbody.append(tr); }
  for (const item of history.slice(0, 15)) { const tr = document.createElement('tr'); const values = [time(item.time), item.source === 'Binance' ? 'Binance' : item.source === 'Manual' ? 'Thủ công' : 'Bot SMA', item.action, `$${fmt(item.price)}`, item.quantity && item.quantity !== '0' ? `${fmt(item.quantity, 6)} BTC` : '—', item.status || (item.action === 'HOLD' ? 'Quan sát' : item.action === 'SKIPPED' ? 'Bỏ qua' : 'Hoàn tất')]; values.forEach((value, index) => { const td = document.createElement('td'); td.textContent = value; if (index === 2 && item.action === 'BUY') td.className = 'trade-buy'; if (index === 2 && item.action === 'SELL') td.className = 'trade-sell'; tr.append(td); }); tbody.append(tr); }
  renderAutomation(); drawChart();
}
function renderAutomation() {
  const auto = dashboard.automation || {};
  const labels = { starting: 'Khởi động', checking: 'Đang kiểm tra', waiting: 'Đang chờ nến', monitoring: 'Chỉ theo dõi', error: 'Có lỗi', blocked: 'Thiếu API key' };
  $('autoState').textContent = labels[auto.status] || 'Không rõ';
  $('autoState').className = `auto-badge ${auto.status === 'waiting' || auto.status === 'checking' || auto.status === 'monitoring' ? 'running' : auto.status === 'error' || auto.status === 'blocked' ? 'error' : ''}`;
  $('autoMode').textContent = auto.mode === 'real' ? 'Futures Real · Theo dõi' : auto.mode === 'futures_demo' ? 'Futures Demo' : auto.mode === 'testnet' ? 'Spot Testnet' : 'Paper';
  $('lastRun').textContent = time(auto.lastRun);
  $('lastTrigger').textContent = auto.lastTrigger === 'candle_close' ? 'Nến đóng' : auto.lastTrigger === 'fallback' ? 'Kiểm tra dự phòng' : auto.lastTrigger === 'startup' ? 'Khởi động' : '—';
  $('autoSocket').textContent = mode === 'real' && !dashboard.accountStream?.configured ? 'Chưa có API key Real' : (mode === 'real' ? dashboard.accountStream?.connected : auto.socket === 'connected') ? 'Đã kết nối' : auto.socket === 'disconnected' ? 'Đang nối lại' : 'Đang kết nối';
  $('lastResult').textContent = auto.lastResult || 'Đang chờ lần kiểm tra đầu tiên.';
  $('autoError').textContent = mode === 'real' ? dashboard.accountStream?.error || '' : auto.lastError || '';
  $('autoError').hidden = !$('autoError').textContent;
}
function chartPoints() {
  if (!dashboard) return [];
  const closed = dashboard.points;
  if (!liveCandle || liveCandle.time <= closed.at(-1).time || !Number.isFinite(liveCandle.close)) return closed;
  const closes = closed.map(point => point.close);
  const average = count => (closes.slice(-(count - 1)).reduce((sum, value) => sum + value, 0) + liveCandle.close) / count;
  return [...closed, { ...liveCandle, sma20: average(20), sma50: average(50) }];
}
function drawChart() { if (!dashboard) return; const canvas = $('chart'), bounds = canvas.getBoundingClientRect(), ratio = devicePixelRatio || 1; canvas.width = Math.round(bounds.width * ratio); canvas.height = Math.round(bounds.height * ratio); const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio); const w = bounds.width, h = bounds.height, pts = chartPoints(), pad = { l: 10, r: 55, t: 15, b: 17 }; const values = pts.flatMap(p => [p.close, p.sma20, p.sma50]).filter(v => v != null), low = Math.min(...values), high = Math.max(...values), margin = (high - low || 1) * .1, min = low - margin, max = high + margin; const x = i => pad.l + (w - pad.l - pad.r) * i / (pts.length - 1), y = v => pad.t + (max - v) * (h - pad.t - pad.b) / (max - min); ctx.font = '10px Manrope, sans-serif'; for (let i = 0; i < 5; i++) { const yy = pad.t + i * (h - pad.t - pad.b) / 4; ctx.strokeStyle = '#2a394b'; ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r + 4, yy); ctx.stroke(); ctx.setLineDash([]); ctx.fillStyle = '#728198'; ctx.fillText(fmt(max - (max - min) * i / 4, 0), w - pad.r + 9, yy + 3); } function line(key, color, width) { ctx.beginPath(); let started = false; pts.forEach((p, i) => { if (p[key] == null) return; started ? ctx.lineTo(x(i), y(p[key])) : ctx.moveTo(x(i), y(p[key])); started = true; }); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.stroke(); } const fill = ctx.createLinearGradient(0, 0, 0, h); fill.addColorStop(0, '#f5ad5833'); fill.addColorStop(1, '#f5ad5800'); ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(x(i), y(p.close)) : ctx.moveTo(x(i), y(p.close))); ctx.lineTo(x(pts.length - 1), h - pad.b); ctx.lineTo(x(0), h - pad.b); ctx.closePath(); ctx.fillStyle = fill; ctx.fill(); line('sma50', '#7994ea', 1.4); line('sma20', '#68d7b6', 1.4); line('close', '#f3ac57', 2); if (hover >= 0 && hover < pts.length) { const p = pts[hover]; ctx.strokeStyle = '#8b9aaf'; ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.moveTo(x(hover), pad.t); ctx.lineTo(x(hover), h - pad.b); ctx.stroke(); ctx.setLineDash([]); ctx.beginPath(); ctx.arc(x(hover), y(p.close), 4, 0, Math.PI * 2); ctx.fillStyle = '#f3ac57'; ctx.fill(); const label = `${time(p.time)}  $${fmt(p.close)}`, tx = Math.min(x(hover) + 8, w - 176); ctx.fillStyle = '#28374b'; ctx.fillRect(tx, 10, 170, 24); ctx.fillStyle = '#e6edf6'; ctx.fillText(label, tx + 7, 26); } }
$('chart').addEventListener('mousemove', event => { if (!dashboard) return; const rect = $('chart').getBoundingClientRect(); hover = Math.max(0, Math.min(chartPoints().length - 1, Math.round((event.clientX - rect.left - 10) / (rect.width - 65) * (chartPoints().length - 1)))); drawChart(); }); $('chart').addEventListener('mouseleave', () => { hover = -1; drawChart(); }); window.addEventListener('resize', drawChart);
$('refreshBtn').addEventListener('click', load);
setInterval(() => { $('clock').textContent = new Date().toLocaleTimeString('vi-VN'); }, 1000);
setInterval(refreshAccount, 5000);
setInterval(load, 60000);
setInterval(() => {
  const age = lastPacketAt ? Date.now() - lastPacketAt : Infinity;
  const latency = $('latency');
  if (latency) latency.textContent = age < 5000 && lastTransportLagMs !== null ? `Nhận sau ~${fmt(lastTransportLagMs, 0)} ms` : age < Infinity ? `Gói gần nhất ${fmt(age / 1000, 0)}s trước` : 'Chờ dữ liệu';
  if (socket?.readyState === WebSocket.OPEN && age > (mode === 'real' ? 5000 : 15000)) {
    setFeed('off'); liveTicker = null; liveTradePrice = null; liveCandle = null; socket.close();
  }
}, 1000);

function connectAccountEvents() {
  accountEvents = new EventSource('/api/real-events');
  const update = event => {
    let data; try { data = JSON.parse(event.data); } catch { return; }
    if (!dashboard) return;
    if (data.account !== undefined) dashboard.account = data.account;
    if (data.status) { dashboard.accountStream = data.status; dashboard.automation.lastRun = data.status.accountAt; }
    render();
  };
  accountEvents.addEventListener('snapshot', update);
  accountEvents.addEventListener('account', update);
  accountEvents.addEventListener('status', event => { try { if (dashboard) { dashboard.accountStream = JSON.parse(event.data); dashboard.automation.lastRun = dashboard.accountStream.accountAt; render(); } } catch { /* malformed event */ } });
  accountEvents.addEventListener('order', event => {
    try {
      const order = JSON.parse(event.data);
      if (order.symbol !== 'BTCUSDT') return;
      recentOrders = [{ time: order.eventAt, source: 'Binance', action: order.side, price: order.price, quantity: order.filled || order.quantity, status: order.status }, ...recentOrders].slice(0, 50);
      if (dashboard) { dashboard.state.history = recentOrders; render(); }
    } catch { /* malformed event */ }
  });
  accountEvents.onerror = () => { if (dashboard?.accountStream) { dashboard.accountStream.connected = false; render(); } };
}

function installModeNav() {
  const nav = document.createElement('div'); nav.className = 'environment-nav'; nav.setAttribute('aria-label', 'Môi trường giao dịch');
  for (const [href, label, active] of [['/demo', 'Demo / Testnet', mode !== 'real'], ['/real', 'Real', mode === 'real']]) {
    const link = document.createElement('a'); link.href = href; link.textContent = label; if (active) link.className = 'active'; nav.append(link);
  }
  document.querySelector('.top').insertBefore(nav, document.querySelector('.top-right'));
  const latency = document.createElement('span'); latency.id = 'latency'; latency.className = 'latency'; latency.textContent = 'Chờ dữ liệu';
  document.querySelector('.top-right').insertBefore(latency, $('clock'));
}
async function boot() {
  if (location.pathname === '/real') mode = 'real';
  else try { const response = await fetch('/api/automation'); const result = await response.json(); if (response.ok && ['paper', 'testnet', 'futures_demo'].includes(result.mode)) mode = result.mode; }
  catch { /* The dashboard request below will show the connection error. */ }
  installModeNav();
  if (mode === 'futures_demo') {
    document.title = 'BTC/USDT · Futures Demo';
    document.querySelector('.logo-sub').textContent = 'FUTURES';
    document.querySelector('.pair small').textContent = 'Bitcoin · USDⓈ-M Futures Demo';
    document.querySelector('.stats .stat:first-child small').textContent = 'Số dư ký quỹ USDT';
    document.querySelector('.stats .stat:nth-child(4) span').textContent = 'PNL CHƯA CHỐT';
    document.querySelector('.stats .stat:nth-child(4) small').textContent = 'Lợi nhuận vị thế đang mở';
    document.querySelector('.auto-details div:last-child dt').textContent = 'Giới hạn mỗi lệnh mở Long';
    document.querySelector('.auto-details div:last-child dd').textContent = '60 USDT · isolated · 1x';
    document.querySelector('footer').textContent = 'Binance Futures Demo · Giá và sổ lệnh qua WebSocket · Tài khoản làm mới mỗi 5 giây';
  }
  if (mode === 'real') {
    document.title = 'BTC/USDT · Futures Real';
    document.body.classList.add('real-site');
    document.querySelector('.logo-sub').textContent = 'REAL';
    document.querySelector('.pair small').textContent = 'Bitcoin · USDⓈ-M Futures Real';
    document.querySelector('.stats .stat:first-child small').textContent = 'Margin balance từ Binance';
    document.querySelector('.stats .stat:nth-child(2) span').textContent = 'PNL CHƯA CHỐT';
    document.querySelector('.stats .stat:nth-child(3) span').textContent = 'ROI VỊ THẾ';
    document.querySelector('.stats .stat:nth-child(4) span').textContent = 'GIÁ TRỊ VỊ THẾ BTC';
    document.querySelector('.stats .stat:nth-child(4) small').textContent = 'Theo giá giao dịch gần nhất';
    document.querySelector('.chart-signal > span:first-child').textContent = 'Tín hiệu tham khảo';
    document.querySelector('.auto-panel h2').textContent = 'Kết nối tài khoản';
    document.querySelector('.auto-strategy strong').textContent = 'Theo dõi Futures Real';
    document.querySelector('.auto-strategy span').textContent = 'Giá thị trường trực tiếp · tài khoản qua user stream';
    document.querySelector('.auto-details div:last-child dt').textContent = 'Đặt lệnh trên trang Real';
    document.querySelector('.auto-details div:last-child dd').textContent = 'Chưa bật';
    document.querySelector('.auto-note').textContent = 'Thị trường đi thẳng từ WebSocket Binance. Tài khoản dùng user stream khi có API key Real; REST đồng bộ lại khi kết nối lại.';
    document.querySelector('footer').textContent = 'Binance Futures Real · Giá, nến, sổ lệnh qua WebSocket · Tài khoản qua user stream khi được kết nối';
    connectAccountEvents();
  }
  load(); connectMarket();
}
boot();
