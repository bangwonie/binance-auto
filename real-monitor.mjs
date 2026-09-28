import { createHmac } from 'node:crypto';

const REST = 'https://fapi.binance.com';
const STREAM = 'wss://fstream.binance.com';

export function createRealMonitor() {
  const key = process.env.BINANCE_REAL_API_KEY;
  const secret = process.env.BINANCE_REAL_API_SECRET;
  const clients = new Set();
  const status = { configured: Boolean(key && secret), connected: false, accountAt: null, eventAt: null, error: '' };
  let account = null;
  let socket = null;
  let listenKey = null;
  let reconnectDelay = 1000;
  let refreshTimer = null;

  function broadcast(type, data) {
    const payload = `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
    for (const client of clients) {
      try { client.write(payload); } catch { clients.delete(client); }
    }
  }

  async function signedAccount() {
    const query = `timestamp=${Date.now()}&recvWindow=5000`;
    const signature = createHmac('sha256', secret).update(query).digest('hex');
    const response = await fetch(`${REST}/fapi/v2/account?${query}&signature=${signature}`, {
      headers: { 'X-MBX-APIKEY': key }, signal: AbortSignal.timeout(12000),
    });
    if (!response.ok) throw new Error(`Real account HTTP ${response.status}: ${(await response.text()).slice(0, 180)}`);
    const raw = await response.json();
    const position = raw.positions?.find(row => row.symbol === 'BTCUSDT' && row.positionSide === 'BOTH');
    return {
      usdt: raw.totalMarginBalance, available: raw.availableBalance, wallet: raw.totalWalletBalance,
      unrealizedPnl: raw.totalUnrealizedProfit, positionInitialMargin: raw.totalPositionInitialMargin,
      btc: position?.positionAmt || '0', leverage: position?.leverage || null,
      entryPrice: position?.entryPrice || null, liquidationPrice: position?.liquidationPrice || null,
      canTrade: raw.canTrade,
    };
  }

  async function refreshAccount() {
    if (!status.configured) return null;
    try {
      account = await signedAccount();
      status.accountAt = Date.now();
      status.error = '';
      broadcast('account', { account, status });
      return account;
    } catch (error) {
      status.error = error.message;
      broadcast('status', status);
      throw error;
    }
  }

  function scheduleRefresh() {
    if (refreshTimer) return;
    refreshTimer = setTimeout(async () => {
      refreshTimer = null;
      try { await refreshAccount(); } catch { /* Status is already published. */ }
    }, 250);
  }

  async function listenKeyRequest(method) {
    const response = await fetch(`${REST}/fapi/v1/listenKey`, {
      method, headers: { 'X-MBX-APIKEY': key }, signal: AbortSignal.timeout(12000),
    });
    if (!response.ok) throw new Error(`Real user stream HTTP ${response.status}: ${(await response.text()).slice(0, 180)}`);
    return response.json();
  }

  async function connect() {
    if (!status.configured) return;
    try {
      listenKey = (await listenKeyRequest('POST')).listenKey;
      if (!listenKey) throw new Error('Binance did not return a user stream key');
      const current = new WebSocket(`${STREAM}/ws/${listenKey}`);
      socket = current;
      current.addEventListener('open', () => {
        status.connected = true;
        status.error = '';
        reconnectDelay = 1000;
        broadcast('status', status);
        scheduleRefresh();
      });
      current.addEventListener('message', event => {
        let item;
        try { item = JSON.parse(event.data); } catch { return; }
        if (item.e === 'ACCOUNT_UPDATE' || item.e === 'ORDER_TRADE_UPDATE') {
          status.eventAt = Number(item.E) || Date.now();
          if (item.e === 'ORDER_TRADE_UPDATE') {
            const order = item.o || {};
            broadcast('order', {
              eventAt: status.eventAt, symbol: order.s, side: order.S, type: order.o,
              status: order.X, quantity: order.q, filled: order.z, price: order.ap || order.p,
            });
          }
          scheduleRefresh();
          broadcast('status', status);
        } else if (item.e === 'listenKeyExpired') {
          current.close();
        }
      });
      current.addEventListener('close', () => {
        if (socket !== current) return;
        status.connected = false;
        broadcast('status', status);
        setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 30000);
      });
      current.addEventListener('error', () => current.close());
    } catch (error) {
      status.connected = false;
      status.error = error.message;
      broadcast('status', status);
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 30000);
    }
  }

  function subscribe(req, res) {
    res.writeHead(200, {
      'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive', 'x-content-type-options': 'nosniff',
    });
    res.write(`event: snapshot\ndata: ${JSON.stringify({ account, status })}\n\n`);
    clients.add(res);
    const heartbeat = setInterval(() => { try { res.write(': keepalive\n\n'); } catch { /* close handler removes client */ } }, 15000);
    req.on('close', () => { clearInterval(heartbeat); clients.delete(res); });
  }

  function start() {
    if (!status.configured) return;
    refreshAccount().catch(error => { console.error(`[real-monitor] ${error.message}`); });
    connect();
    setInterval(() => {
      if (listenKey) listenKeyRequest('PUT').catch(error => {
        status.error = error.message;
        broadcast('status', status);
        socket?.close();
      });
    }, 30 * 60 * 1000);
    setInterval(() => { refreshAccount().catch(() => {}); }, 60 * 1000);
  }

  return { status, get account() { return account; }, subscribe, start };
}
