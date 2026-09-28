"""Long-only BTCUSDT SMA bot for Binance Futures Demo. No production endpoint."""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

BASE = "https://demo-fapi.binance.com"
SYMBOL = "BTCUSDT"
STATE = Path(__file__).with_name("state-futures_demo.json")
D = Decimal


def api(method, path, params=None, signed=False):
    params = dict(params or {})
    headers = {}
    if signed:
        key = os.getenv("BINANCE_FUTURES_DEMO_API_KEY")
        secret = os.getenv("BINANCE_FUTURES_DEMO_API_SECRET")
        if not key or not secret:
            raise RuntimeError("Futures Demo credentials are missing")
        params.update(timestamp=int(time.time() * 1000), recvWindow=5000)
        payload = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers["X-MBX-APIKEY"] = key
    query = urllib.parse.urlencode(params)
    url = BASE + path + ("?" + query if query else "")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        raise RuntimeError(f"Futures Demo HTTP {error.code}: {detail}") from error


def save(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE)


def load():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"last_candle": None, "history": [], "managed_position": False}


def signal():
    candles = api("GET", "/fapi/v1/klines", {"symbol": SYMBOL, "interval": "5m", "limit": 52})
    closed = candles[:-1]
    if len(closed) < 51:
        raise RuntimeError("Not enough closed Futures Demo candles")
    closes = [D(row[4]) for row in closed]
    fast_prev, slow_prev = sum(closes[-21:-1]) / 20, sum(closes[-51:-1]) / 50
    fast_now, slow_now = sum(closes[-20:]) / 20, sum(closes[-50:]) / 50
    side = "BUY" if fast_prev <= slow_prev and fast_now > slow_now else "SELL" if fast_prev >= slow_prev and fast_now < slow_now else None
    return str(closed[-1][0]), closes[-1], side, fast_now, slow_now


def rules():
    symbol = next(x for x in api("GET", "/fapi/v1/exchangeInfo")["symbols"] if x["symbol"] == SYMBOL)
    if symbol["status"] != "TRADING":
        raise RuntimeError("BTCUSDT is not trading")
    filters = {x["filterType"]: x for x in symbol["filters"]}
    lot = filters["MARKET_LOT_SIZE"]
    step = D(lot["stepSize"])
    minimum = D(filters["MIN_NOTIONAL"]["notional"])
    return step, D(lot["minQty"]), minimum


def floor(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def account_position():
    account = api("GET", "/fapi/v2/account", signed=True)
    if not account.get("canTrade") or account.get("multiAssetsMargin"):
        raise RuntimeError("Account must allow trading in single-asset mode")
    positions = [p for p in account["positions"] if p["symbol"] == SYMBOL]
    if len(positions) != 1 or positions[0]["positionSide"] != "BOTH":
        raise RuntimeError("Bot requires one-way BTCUSDT position mode")
    return account, positions[0]


def reconcile(state):
    pending = state.get("pending_order")
    if not pending:
        return
    order = api("GET", "/fapi/v1/order", {"symbol": SYMBOL, "origClientOrderId": pending["client_id"]}, signed=True)
    status = order.get("status")
    if status not in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
        raise RuntimeError(f"Pending order {pending['client_id']} is {status}; no new order")
    if status != "FILLED" and D(order.get("executedQty", "0")) > 0:
        raise RuntimeError(f"Order {pending['client_id']} partially filled; manual reconciliation required")
    if status == "FILLED":
        state["managed_position"] = pending["side"] == "BUY"
    event = pending["event"]
    event.update(status=status, orderId=order.get("orderId"), quantity=order.get("executedQty", "0"))
    state["history"] = ([event] + state.get("history", []))[:100]
    state["last_candle"] = pending["candle"]
    del state["pending_order"]
    save(state)
    print(f"Reconciled order status={status}")


def main():
    state = load()
    reconcile(state)
    candle, price, side, fast, slow = signal()
    print(f"closed candle={candle} BTC={price} SMA20={fast:.2f} SMA50={slow:.2f} signal={side or 'HOLD'}")
    account, position = account_position()
    amount = D(position["positionAmt"])
    if amount < 0 or (amount > 0 and not state.get("managed_position")):
        raise RuntimeError("Unmanaged BTCUSDT position found; no order sent")
    if api("GET", "/fapi/v1/openOrders", {"symbol": SYMBOL}, signed=True):
        raise RuntimeError("Open BTCUSDT orders found; no order sent")
    if amount == 0:
        risk = api("GET", "/fapi/v2/positionRisk", {"symbol": SYMBOL}, signed=True)
        if len(risk) != 1 or D(risk[0]["positionAmt"]) != 0:
            raise RuntimeError("BTCUSDT position changed before setup; no order sent")
        if risk[0].get("marginType") != "isolated":
            api("POST", "/fapi/v1/marginType", {"symbol": SYMBOL, "marginType": "ISOLATED"}, signed=True)
        if int(position["leverage"]) != 1:
            leverage = api("POST", "/fapi/v1/leverage", {"symbol": SYMBOL, "leverage": 1}, signed=True)
            if int(leverage.get("leverage", 0)) != 1:
                raise RuntimeError("Could not verify 1x leverage")
    elif int(position["leverage"]) != 1:
        raise RuntimeError("Managed BTCUSDT position is not 1x; no order sent")
    if state["last_candle"] == candle:
        print("Already processed this candle")
        return
    event = {"time": int(time.time() * 1000), "candle": candle, "signal": side or "HOLD", "price": str(price), "action": "HOLD", "quantity": "0"}
    if side == "BUY" and amount == 0:
        step, min_qty, min_notional = rules()
        budget = min(D("60"), D(account["availableBalance"]) * D("0.9"))
        quantity = floor(budget / price, step)
        if quantity < min_qty or quantity * price < min_notional:
            event["action"] = "SKIPPED"
            print("Skipped: below Futures Demo minimum notional or insufficient available balance")
        else:
            submit(state, candle, event, "BUY", quantity, False)
    elif side == "SELL" and amount > 0:
        step, min_qty, _ = rules()
        quantity = floor(amount, step)
        if quantity < min_qty:
            raise RuntimeError("Managed position is below minimum close quantity")
        submit(state, candle, event, "SELL", quantity, True)
    elif side:
        event["action"] = "SKIPPED"
        print("Skipped: position already matches long-only signal")
    state["last_candle"] = candle
    state["history"] = ([event] + state.get("history", []))[:100]
    save(state)
    print(f"Futures Demo equity={account['totalMarginBalance']} USDT position={amount} BTC")


def submit(state, candle, event, side, quantity, reduce_only):
    client_id = f"sma1x-{candle}-{side.lower()}"
    event.update(action=side, quantity=str(quantity))
    state["pending_order"] = {"client_id": client_id, "candle": candle, "side": side, "event": event.copy()}
    save(state)
    params = {"symbol": SYMBOL, "side": side, "type": "MARKET", "quantity": format(quantity, "f"), "newClientOrderId": client_id, "newOrderRespType": "RESULT"}
    if reduce_only:
        params["reduceOnly"] = "true"
    result = api("POST", "/fapi/v1/order", params, signed=True)
    if result.get("status") != "FILLED":
        raise RuntimeError(f"Order {client_id} status={result.get('status')}; reconcile before next order")
    state["managed_position"] = side == "BUY"
    event.update(orderId=result.get("orderId"), status=result.get("status"), quantity=result.get("executedQty", str(quantity)))
    del state["pending_order"]
    print(f"Futures Demo {side} filled quantity={event['quantity']}")


if __name__ == "__main__":
    main()
