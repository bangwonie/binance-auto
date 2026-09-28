import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createHmac } from 'node:crypto';
import { createRealMonitor } from './real-monitor.mjs';

const root = path.dirname(fileURLToPath(import.meta.url));
const host = '127.0.0.1';
const port = Number(process.env.PORT || 3000);
const autoMode = process.env.AUTO_MODE || 'paper';
if (!['paper', 'testnet', 'futures_demo'].includes(autoMode)) throw new Error('AUTO_MODE must be paper, testnet, or futures_demo');
const base = autoMode === 'futures_demo' ? 'https://demo-fapi.binance.com' : 'https://testnet.binance.vision';
const prefix = autoMode === 'futures_demo' ? '/fapi/v1' : '/api/v3';
const realBase = 'https://fapi.binance.com';
const realMonitor = createRealMonitor();
let running = false;
const automation = { mode: autoMode, status: 'starting', socket: 'connecting', lastRun: null, lastTrigger: null, lastResult: '', lastError: '', nextCheck: null };
let realRunning = false;
const realTradingEnabled = process.env.REAL_TRADING_ENABLED === '1';
const realAutomation = { mode: 'real', status: realTradingEnabled ? 'starting' : 'monitoring', socket: 'connecting', lastRun: null, lastTrigger: null, lastResult: realTradingEnabled ? '' : 'Theo dõi tài khoản Real; đặt lệnh chưa bật.', lastError: '', nextCheck: null, tradingEnabled: realTradingEnabled };

function json(res, status, value) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' });
  res.end(JSON.stringify(value));
}

async function state(mode) {
  try { return JSON.parse(await fs.readFile(path.join(root, `state-${mode}.json`), 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return { paper_usdt: '1000', paper_btc: '0', history: [], last_candle: null }; throw error; }
}

async function market(mode = autoMode) {
  const apiBase = mode === 'real' ? realBase : base;
  const apiPrefix = mode === 'real' ? '/fapi/v1' : prefix;
  const response = await fetch(`${apiBase}${apiPrefix}/klines?symbol=BTCUSDT&interval=1h&limit=121`, { signal: AbortSignal.timeout(12000) });
  if (!response.ok) throw new Error(`Market data HTTP ${response.status}`);
  const rows = (await response.json()).slice(0, -1);
  const closes = rows.map(row => Number(row[4]));
  const points = rows.map((row, index) => ({
    time: Number(row[0]), open: Number(row[1]), high: Number(row[2]), low: Number(row[3]), close: closes[index], volume: Number(row[5]),
    sma20: index >= 19 ? closes.slice(index - 19, index + 1).reduce((a, b) => a + b, 0) / 20 : null,
    sma50: index >= 49 ? closes.slice(index - 49, index + 1).reduce((a, b) => a + b, 0) / 50 : null
  }));
  return points;
}

async function currentCandle(mode = autoMode) {
  const apiBase = mode === 'real' ? realBase : base;
  const apiPrefix = mode === 'real' ? '/fapi/v1' : prefix;
  const response = await fetch(`${apiBase}${apiPrefix}/klines?symbol=BTCUSDT&interval=1h&limit=2`, { signal: AbortSignal.timeout(12000) });
  if (!response.ok) throw new Error(`Current candle HTTP ${response.status}`);
  const row = (await response.json()).at(-1);
  return { time: Number(row[0]), open: Number(row[1]), high: Number(row[2]), low: Number(row[3]), close: Number(row[4]), volume: Number(row[5]) };
}

async function exchangeGet(endpoint, mode = autoMode) {
  const response = await fetch(`${mode === 'real' ? realBase : base}${endpoint}`, { signal: AbortSignal.timeout(12000) });
  if (!response.ok) throw new Error(`Binance HTTP ${response.status}`);
  return response.json();
}

async function testnetBalances() {
  const futures = autoMode === 'futures_demo';
  const key = process.env[futures ? 'BINANCE_FUTURES_DEMO_API_KEY' : 'BINANCE_TESTNET_API_KEY'];
  const secret = process.env[futures ? 'BINANCE_FUTURES_DEMO_API_SECRET' : 'BINANCE_TESTNET_API_SECRET'];
  if (!key || !secret) return null;
  const query = `timestamp=${Date.now()}&recvWindow=5000`;
  const signature = createHmac('sha256', secret).update(query).digest('hex');
  const response = await fetch(`${base}${futures ? '/fapi/v2/account' : '/api/v3/account'}?${query}&signature=${signature}`, { headers: { 'X-MBX-APIKEY': key }, signal: AbortSignal.timeout(12000) });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 220);
    throw new Error(`Testnet account HTTP ${response.status}: ${detail}`);
  }
  const account = await response.json();
  if (futures) {
    const position = account.positions?.find(row => row.symbol === 'BTCUSDT' && row.positionSide === 'BOTH');
    return { usdt: account.totalMarginBalance, available: account.availableBalance, btc: position?.positionAmt || '0', wallet: account.totalWalletBalance, unrealizedPnl: account.totalUnrealizedProfit, canTrade: account.canTrade, leverage: position?.leverage || null };
  }
  const assets = Object.fromEntries(account.balances.map(row => [row.asset, row.free]));
  return { usdt: assets.USDT || '0', btc: assets.BTC || '0' };
}

const baselinePath = path.join(root, autoMode === 'futures_demo' ? 'baseline-futures_demo.json' : 'baseline-testnet.json');
async function baseline(account, price) {
  if (!account) return null;
  try { return JSON.parse(await fs.readFile(baselinePath, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const value = { equity: autoMode === 'futures_demo' ? Number(account.usdt) : Number(account.usdt) + Number(account.btc) * price, time: Date.now() };
  await fs.writeFile(baselinePath, JSON.stringify(value, null, 2) + '\n');
  return value;
}

function runBot(mode) {
  return new Promise((resolve, reject) => {
    const args = mode === 'real' ? [path.join(root, 'real_bot.py')] : mode === 'futures_demo' ? [path.join(root, 'futures_bot.py')] : [path.join(root, 'bot.py'), '--mode', mode];
    const child = spawn(process.env.PYTHON || 'python', args, { cwd: root, env: process.env, windowsHide: true });
    let output = '';
    const timer = setTimeout(() => child.kill(), 30000);
    child.stdout.on('data', chunk => { output += chunk.toString(); });
    child.stderr.on('data', chunk => { output += chunk.toString(); });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => { clearTimeout(timer); code === 0 ? resolve(output.trim()) : reject(new Error(output.trim() || `Bot exited ${code}`)); });
  });
}

async function runRealAutomation(trigger) {
  if (!realTradingEnabled || realRunning || realAutomation.status === 'blocked') return;
  realRunning = true;
  realAutomation.status = 'checking';
  realAutomation.lastTrigger = trigger;
  realAutomation.lastRun = Date.now();
  try {
    realAutomation.lastResult = await runBot('real');
    realAutomation.lastError = '';
    realAutomation.status = 'waiting';
    console.log(`[real-auto:${trigger}] ${realAutomation.lastResult.replaceAll('\n', ' | ')}`);
  } catch (error) {
    realAutomation.lastError = error.message;
    realAutomation.status = 'error';
    console.error(`[real-auto:${trigger}] ${error.message}`);
  } finally { realRunning = false; }
}

function startRealAutomation() {
  if (!realTradingEnabled) return;
  const required = ['BINANCE_REAL_API_KEY', 'BINANCE_REAL_API_SECRET', 'REAL_MAX_USDT', 'REAL_DAILY_LOSS_USDT', 'REAL_STOP_LOSS_PCT'];
  if (required.some(name => !process.env[name])) {
    realAutomation.status = 'blocked';
    realAutomation.lastError = 'Real trading risk configuration or credentials are missing';
    return;
  }
  runRealAutomation('startup');
  setInterval(() => {
    realAutomation.nextCheck = Date.now() + 60000;
    runRealAutomation('fallback');
  }, 60000);
  let delay = 1000;
  const connect = () => {
    realAutomation.socket = 'connecting';
    const connection = new WebSocket('wss://fstream.binance.com/ws/btcusdt@kline_1h');
    connection.addEventListener('open', () => { realAutomation.socket = 'connected'; delay = 1000; });
    connection.addEventListener('message', event => {
      try { const item = JSON.parse(event.data); if (item.k?.x === true) runRealAutomation('candle_close'); }
      catch { /* The minute fallback remains active. */ }
    });
    connection.addEventListener('close', () => {
      realAutomation.socket = 'disconnected';
      setTimeout(connect, delay);
      delay = Math.min(delay * 2, 30000);
    });
    connection.addEventListener('error', () => connection.close());
  };
  connect();
}

async function runAutomation(trigger) {
  if (running || automation.status === 'blocked') return;
  running = true;
  automation.status = 'checking';
  automation.lastTrigger = trigger;
  automation.lastRun = Date.now();
  try {
    automation.lastResult = await runBot(autoMode);
    automation.lastError = '';
    automation.status = 'waiting';
    console.log(`[auto:${trigger}] ${automation.lastResult.replaceAll('\n', ' | ')}`);
  } catch (error) {
    automation.lastError = error.message;
    automation.status = 'error';
    console.error(`[auto:${trigger}] ${error.message}`);
  } finally { running = false; }
}

async function startAutomation() {
  if (autoMode === 'futures_demo' && !(process.env.BINANCE_FUTURES_DEMO_API_KEY && process.env.BINANCE_FUTURES_DEMO_API_SECRET)) {
    automation.status = 'blocked';
    automation.lastError = 'AUTO_MODE=futures_demo requires Futures Demo API credentials';
    return;
  }
  if (autoMode === 'testnet' && !(process.env.BINANCE_TESTNET_API_KEY && process.env.BINANCE_TESTNET_API_SECRET)) {
    automation.status = 'blocked';
    automation.lastError = 'AUTO_MODE=testnet requires Spot Testnet API credentials';
    return;
  }
  if (autoMode === 'testnet' || autoMode === 'futures_demo') {
    try { await testnetBalances(); }
    catch (error) {
      automation.status = 'blocked';
      automation.lastError = `${autoMode === 'futures_demo' ? 'Futures Demo' : 'Spot Testnet'} credential check failed: ${error.message}`;
      console.error(automation.lastError);
      return;
    }
  }
  runAutomation('startup');
  const check = () => { automation.nextCheck = Date.now() + 60000; runAutomation('fallback'); };
  automation.nextCheck = Date.now() + 60000;
  setInterval(check, 60000);
  let delay = 1000;
  const connect = () => {
    automation.socket = 'connecting';
    const connection = new WebSocket(autoMode === 'futures_demo' ? 'wss://fstream.binancefuture.com/ws/btcusdt@kline_1h' : 'wss://stream.testnet.binance.vision/ws/btcusdt@kline_1h');
    connection.addEventListener('open', () => { automation.socket = 'connected'; delay = 1000; });
    connection.addEventListener('message', event => {
      try { const item = JSON.parse(event.data); if (item.k?.x === true) runAutomation('candle_close'); }
      catch { /* Ignore malformed market events; the timer remains the fallback. */ }
    });
    connection.addEventListener('close', () => {
      automation.socket = 'disconnected';
      setTimeout(connect, delay);
      delay = Math.min(delay * 2, 30000);
    });
    connection.addEventListener('error', () => connection.close());
  };
  connect();
}

const files = { '/': ['index.html', 'text/html'], '/demo': ['index.html', 'text/html'], '/real': ['index.html', 'text/html'], '/app.js': ['app.js', 'text/javascript'], '/style.css': ['style.css', 'text/css'], '/tokens.css': ['../tokens.css', 'text/css'] };
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${host}:${port}`);
    if (req.method === 'GET' && url.pathname === '/api/real-events') return realMonitor.subscribe(req, res);
    if (req.method === 'GET' && url.pathname === '/api/real-status') return json(res, 200, realMonitor.status);
    if (req.method === 'GET' && url.pathname === '/api/dashboard') {
      const mode = url.searchParams.get('mode') === 'real' ? 'real' : autoMode;
      const apiPrefix = mode === 'real' ? '/fapi/v1' : prefix;
      const [points, liveBar, currentState, account, depth] = await Promise.all([
        market(mode), currentCandle(mode),
        state(mode),
        mode === 'real' ? realMonitor.account : mode === 'paper' ? null : testnetBalances(),
        exchangeGet(`${apiPrefix}/depth?symbol=BTCUSDT&limit=10`, mode),
      ]);
      const price = (Number(depth.bids?.[0]?.[0]) + Number(depth.asks?.[0]?.[0])) / 2 || points.at(-1)?.close;
      const startingValue = mode !== 'paper' && mode !== 'real' ? await baseline(account, price) : null;
      const modeAutomation = mode === 'real' ? { ...realAutomation, socket: realTradingEnabled ? realAutomation.socket : realMonitor.status.connected ? 'connected' : 'disconnected', lastRun: realTradingEnabled ? realAutomation.lastRun : realMonitor.status.accountAt, lastError: realAutomation.lastError || realMonitor.status.error } : automation;
      json(res, 200, { mode, points, currentCandle: liveBar, state: currentState, account, depth, baseline: startingValue, automation: modeAutomation, accountStream: realMonitor.status, testnetReady: !!(process.env.BINANCE_TESTNET_API_KEY && process.env.BINANCE_TESTNET_API_SECRET), running: mode === 'real' ? realRunning : running, fetchedAt: Date.now() });
      return;
    }
    if (req.method === 'GET' && url.pathname === '/api/automation') return json(res, 200, automation);
    if (req.method === 'GET' && url.pathname === '/api/account') {
      const mode = url.searchParams.get('mode') === 'real' ? 'real' : autoMode;
      const [currentState, account] = await Promise.all([state(mode), mode === 'real' ? realMonitor.account : mode === 'paper' ? null : testnetBalances()]);
      json(res, 200, { mode, state: currentState, account, automation: mode === 'real' ? { ...realAutomation, socket: realTradingEnabled ? realAutomation.socket : realMonitor.status.connected ? 'connected' : 'disconnected', lastRun: realTradingEnabled ? realAutomation.lastRun : realMonitor.status.accountAt, lastError: realAutomation.lastError || realMonitor.status.error } : automation, accountStream: realMonitor.status, running: mode === 'real' ? realRunning : running, fetchedAt: Date.now() });
      return;
    }
    if (req.method === 'GET' && files[url.pathname]) {
      const [name, type] = files[url.pathname];
      const body = await fs.readFile(path.join(root, 'public', name));
      res.writeHead(200, { 'content-type': `${type}; charset=utf-8`, 'x-content-type-options': 'nosniff' });
      res.end(body);
      return;
    }
    json(res, 404, { error: 'Not found' });
  } catch (error) { json(res, 500, { error: error.message }); }
});
server.listen(port, host, () => { console.log(`Dashboard: http://${host}:${port} · AUTO_MODE=${autoMode}`); startAutomation(); realMonitor.start(); startRealAutomation(); });
