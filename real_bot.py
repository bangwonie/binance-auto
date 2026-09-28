"""Guarded, long-only BTCUSDT Futures Real bot. Disabled unless explicitly armed locally."""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

BASE = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
STATE = Path(__file__).with_name("state-real.json")
D = Decimal


class ApiError(RuntimeError):
    def __init__(self, status, detail):
        super().__init__(f"Futures Real HTTP {status}: {detail[:240]}")
        self.status = status
        try:
            self.code = json.loads(detail).get("code")
        except (ValueError, AttributeError):
            self.code = None


def api(method, path, params=None, signed=False):
    params = dict(params or {})
    headers = {}
    if signed:
        key = os.environ["BINANCE_REAL_API_KEY"]
        secret = os.environ["BINANCE_REAL_API_SECRET"]
        params.update(timestamp=int(time.time() * 1000), recvWindow=5000)
        query = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        headers["X-MBX-APIKEY"] = key
    query = urllib.parse.urlencode(params)
    url = BASE + path + ("?" + query if query else "")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=12) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ApiError(error.code, error.read().decode(errors="replace")) from error


def config():
    if os.getenv("REAL_TRADING_ENABLED") != "1":
        raise RuntimeError("Real trading is disabled locally")
    if not os.getenv("BINANCE_REAL_API_KEY") or not os.getenv("BINANCE_REAL_API_SECRET"):
        raise RuntimeError("Real API credentials are missing")
    try:
        limit = D(os.environ["REAL_MAX_USDT"])
        daily_loss = D(os.environ["REAL_DAILY_LOSS_USDT"])
        stop_pct = D(os.environ["REAL_STOP_LOSS_PCT"])
    except (KeyError, ValueError) as error:
        raise RuntimeError("Real risk limits are incomplete") from error
    if not (D("50") <= limit <= D("1000") and D("1") <= daily_loss <= D("1000") and D("0.1") <= stop_pct <= D("10")):
        raise RuntimeError("Real risk limits are outside supported bounds")
    return limit, daily_loss, stop_pct


def load():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"last_candle": None, "history": [], "managed_position": False, "stop_id": None}


def save(state):
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temp.replace(STATE)


def signal():
    rows = api("GET", "/fapi/v1/klines", {"symbol": SYMBOL, "interval": "1h", "limit": 52})[:-1]
    if len(rows) < 51:
        raise RuntimeError("Not enough closed Futures Real candles")
    closes = [D(row[4]) for row in rows]
    fast_prev, slow_prev = sum(closes[-21:-1]) / 20, sum(closes[-51:-1]) / 50
    fast_now, slow_now = sum(closes[-20:]) / 20, sum(closes[-50:]) / 50
    side = "BUY" if fast_prev <= slow_prev and fast_now > slow_now else "SELL" if fast_prev >= slow_prev and fast_now < slow_now else None
    return str(rows[-1][0]), closes[-1], side, fast_now, slow_now


def rules():
    symbol = next(s for s in api("GET", "/fapi/v1/exchangeInfo")["symbols"] if s["symbol"] == SYMBOL)
    if symbol["status"] != "TRADING":
        raise RuntimeError("BTCUSDT is not trading")
    filters = {f["filterType"]: f for f in symbol["filters"]}
    lot = filters["MARKET_LOT_SIZE"]
    return D(lot["stepSize"]), D(lot["minQty"]), D(filters["MIN_NOTIONAL"]["notional"]), D(filters["PRICE_FILTER"]["tickSize"])


def floor(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def account_position():
    account = api("GET", "/fapi/v2/account", signed=True)
    if not account.get("canTrade") or account.get("multiAssetsMargin"):
        raise RuntimeError("Real account must allow trading in single-asset mode")
    positions = [p for p in account["positions"] if p["symbol"] == SYMBOL]
    if len(positions) != 1 or positions[0]["positionSide"] != "BOTH":
        raise RuntimeError("Real bot requires one-way BTCUSDT position mode")
    return account, positions[0]


def reconcile_order(state, stop_pct):
    pending = state.get("pending_order")
    if not pending:
        return
    order = api("GET", "/fapi/v1/order", {"symbol": SYMBOL, "origClientOrderId": pending["id"]}, signed=True)
    status = order.get("status")
    filled = D(order.get("executedQty", "0"))
    if filled > 0 and pending["side"] == "BUY":
        state["managed_position"] = True
        state["entry_price"] = order.get("avgPrice") or state.get("entry_price")
        save(state)
    if status not in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
        if filled > 0 and pending["side"] == "BUY":
            _, position = account_position()
            ensure_stop(state, position, stop_pct)
        raise RuntimeError(f"Pending Real order {pending['id']} status={status}; no new order")
    if pending["side"] == "SELL" and status == "FILLED":
        state["managed_position"] = False
    event = pending["event"]
    event.update(status=status, quantity=str(filled), orderId=order.get("orderId"))
    state["history"] = ([event] + state.get("history", []))[:100]
    state["last_candle"] = pending["candle"]
    del state["pending_order"]
    save(state)


def stop_status(stop_id):
    return api("GET", "/fapi/v1/algoOrder", {"clientAlgoId": stop_id}, signed=True)


def ensure_stop(state, position, stop_pct):
    if D(position["positionAmt"]) <= 0:
        return
    stop_id = state.get("stop_id")
    if stop_id:
        try:
            order = stop_status(stop_id)
        except Exception as error:
            raise RuntimeError(f"Cannot verify protective stop {stop_id}; no new order: {error}") from error
        if order.get("algoStatus") in ("NEW", "WORKING", "ACCEPTED"):
            return
        raise RuntimeError(f"Protective stop {stop_id} is {order.get('algoStatus')}; position needs attention")
    entry = D(state.get("entry_price") or position["entryPrice"])
    if entry <= 0:
        raise RuntimeError("Cannot determine entry price for protective stop")
    tick = rules()[3]
    trigger = floor(entry * (D(1) - stop_pct / 100), tick)
    if trigger <= 0:
        raise RuntimeError("Invalid protective stop price")
    stop_id = f"sma-real-sl-{state['entry_candle']}"
    state["stop_id"] = stop_id
    state["stop_state"] = "intent"
    save(state)
    try:
        order = api("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL", "symbol": SYMBOL, "side": "SELL", "type": "STOP_MARKET",
            "triggerPrice": format(trigger, "f"), "workingType": "MARK_PRICE", "closePosition": "true",
            "clientAlgoId": stop_id,
        }, signed=True)
        state["stop_state"] = "submitted"
        state["stop_algo_id"] = order.get("algoId")
        save(state)
        verified = stop_status(stop_id)
        if verified.get("algoStatus") not in ("NEW", "WORKING", "ACCEPTED"):
            raise RuntimeError(f"Protective stop status={verified.get('algoStatus')}")
    except Exception:
        state["stop_state"] = "uncertain"
        save(state)
        raise


def cancel_stop(state):
    stop_id = state.get("stop_id")
    if not stop_id:
        return
    try:
        order = stop_status(stop_id)
        if order.get("algoStatus") in ("NEW", "WORKING", "ACCEPTED"):
            api("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": stop_id}, signed=True)
    except ApiError as error:
        if error.code not in (-2011, -2013):
            raise
    state["stop_id"] = None
    state.pop("stop_state", None)
    state.pop("stop_algo_id", None)
    save(state)


def submit_market(state, candle, event, side, quantity):
    client_id = f"sma-real-{candle}-{side.lower()}"
    event.update(action=side, quantity=str(quantity))
    state["pending_order"] = {"id": client_id, "side": side, "candle": candle, "event": event.copy()}
    if side == "BUY":
        state["entry_candle"] = candle
    save(state)
    params = {"symbol": SYMBOL, "side": side, "type": "MARKET", "quantity": format(quantity, "f"), "newClientOrderId": client_id, "newOrderRespType": "RESULT"}
    if side == "SELL":
        params["reduceOnly"] = "true"
    result = api("POST", "/fapi/v1/order", params, signed=True)
    if result.get("status") != "FILLED":
        raise RuntimeError(f"Real order {client_id} status={result.get('status')}; reconciliation required")
    state["managed_position"] = side == "BUY"
    if side == "BUY":
        state["entry_price"] = result.get("avgPrice")
    event.update(status="FILLED", quantity=result.get("executedQty", str(quantity)), orderId=result.get("orderId"))
    state["history"] = ([event] + state.get("history", []))[:100]
    state["last_candle"] = candle
    del state["pending_order"]
    save(state)


def emergency_close(state, reason):
    account, position = account_position()
    amount = D(position["positionAmt"])
    if amount <= 0:
        cancel_stop(state)
        state["managed_position"] = False
        save(state)
        return
    if state.get("pending_order"):
        raise RuntimeError(f"Protective stop failed; pending order needs reconciliation before emergency close: {reason}")
    step = rules()[0]
    quantity = floor(amount, step)
    if quantity <= 0:
        raise RuntimeError(f"Protective stop failed; position below close quantity: {reason}")
    candle = state.get("entry_candle") or str(int(time.time() * 1000))
    client_id = f"sma-real-emg-{candle}"
    event = {"time": int(time.time() * 1000), "candle": candle, "signal": "STOP_ERROR", "price": position.get("markPrice", position.get("entryPrice", "0")), "action": "SELL", "quantity": str(quantity), "reason": reason}
    state["pending_order"] = {"id": client_id, "side": "SELL", "candle": candle, "event": event}
    save(state)
    result = api("POST", "/fapi/v1/order", {"symbol": SYMBOL, "side": "SELL", "type": "MARKET", "quantity": format(quantity, "f"), "reduceOnly": "true", "newClientOrderId": client_id, "newOrderRespType": "RESULT"}, signed=True)
    if result.get("status") != "FILLED":
        raise RuntimeError(f"Emergency close status={result.get('status')}; reconcile before any new order")
    state["managed_position"] = False
    state["history"] = ([dict(event, status="FILLED", orderId=result.get("orderId"))] + state.get("history", []))[:100]
    del state["pending_order"]
    save(state)
    cancel_stop(state)
    raise RuntimeError(f"Real position emergency-closed because protective stop failed: {reason}")


def daily_pnl(account):
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = api("GET", "/fapi/v1/income", {"startTime": int(midnight.timestamp() * 1000), "limit": 1000}, signed=True)
    if len(rows) >= 1000:
        raise RuntimeError("Daily income history exceeded one page; no new entry")
    realized = sum((D(row["income"]) for row in rows if row.get("incomeType") in ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")), D(0))
    return realized + min(D(account["totalUnrealizedProfit"]), D(0))


def run():
    max_usdt, loss_limit, stop_pct = config()
    state = load()
    reconcile_order(state, stop_pct)
    account, position = account_position()
    amount = D(position["positionAmt"])
    if amount < 0 or (amount > 0 and not state.get("managed_position")):
        raise RuntimeError("Unmanaged Real BTCUSDT position; no order sent")
    if state.get("managed_position") and amount > 0:
        try:
            ensure_stop(state, position, stop_pct)
        except Exception as error:
            emergency_close(state, str(error))
    elif amount == 0:
        cancel_stop(state)
        state["managed_position"] = False
        save(state)
    candle, price, side, fast, slow = signal()
    print(f"Real closed candle={candle} BTC={price} SMA20={fast:.2f} SMA50={slow:.2f} signal={side or 'HOLD'}")
    if state["last_candle"] == candle:
        print("Already processed this candle")
        return
    event = {"time": int(time.time() * 1000), "candle": candle, "signal": side or "HOLD", "price": str(price), "action": "HOLD", "quantity": "0"}
    if side == "BUY" and amount == 0:
        if int(time.time() * 1000) - (int(candle) + 3600000) > 120000:
            event["action"] = "SKIPPED"
            print("Skipped stale Real entry signal")
            state["last_candle"] = candle
            state["history"] = ([event] + state.get("history", []))[:100]
            save(state)
            return
        if daily_pnl(account) <= -loss_limit:
            raise RuntimeError("Daily Real loss limit reached; no new entry")
        if api("GET", "/fapi/v1/openOrders", {"symbol": SYMBOL}, signed=True):
            raise RuntimeError("Open Real BTCUSDT orders found; no new entry")
        risk = api("GET", "/fapi/v2/positionRisk", {"symbol": SYMBOL}, signed=True)
        if len(risk) != 1 or D(risk[0]["positionAmt"]) != 0:
            raise RuntimeError("BTCUSDT position changed before Real entry")
        if risk[0].get("marginType") != "isolated":
            api("POST", "/fapi/v1/marginType", {"symbol": SYMBOL, "marginType": "ISOLATED"}, signed=True)
        if int(position["leverage"]) != 1:
            leverage = api("POST", "/fapi/v1/leverage", {"symbol": SYMBOL, "leverage": 1}, signed=True)
            if int(leverage.get("leverage", 0)) != 1:
                raise RuntimeError("Cannot verify Real 1x leverage")
        step, minimum, min_notional, _ = rules()
        quantity = floor(min(max_usdt, D(account["availableBalance"]) * D("0.9")) / price, step)
        if quantity < minimum or quantity * price < min_notional:
            event["action"] = "SKIPPED"
        else:
            submit_market(state, candle, event, "BUY", quantity)
            _, updated = account_position()
            try:
                ensure_stop(state, updated, stop_pct)
            except Exception as error:
                emergency_close(state, str(error))
            print(f"Real Long opened with protective stop; quantity={quantity}")
            return
    elif side == "SELL" and amount > 0:
        step = rules()[0]
        submit_market(state, candle, event, "SELL", floor(amount, step))
        cancel_stop(state)
        print("Real Long closed")
        return
    elif side:
        event["action"] = "SKIPPED"
    state["last_candle"] = candle
    state["history"] = ([event] + state.get("history", []))[:100]
    save(state)


def main():
    lock_path = STATE.with_suffix(".lock")
    with open(lock_path, "a+b") as lock:
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another Real bot instance is running") from error
        run()


if __name__ == "__main__":
    main()
