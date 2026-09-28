"""Small BTC/USDT Spot Testnet bot. Python 3.10+, standard library only."""
import argparse
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

BASE = "https://testnet.binance.vision"
SYMBOL = "BTCUSDT"
D = Decimal


def request(method, path, params=None, signed=False):
    params = dict(params or {})
    headers = {}
    if signed:
        key = os.getenv("BINANCE_TESTNET_API_KEY")
        secret = os.getenv("BINANCE_TESTNET_API_SECRET")
        if not key or not secret:
            raise RuntimeError("Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET")
        params.update(timestamp=int(time.time() * 1000), recvWindow=5000)
        payload = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers["X-MBX-APIKEY"] = key
    query = urllib.parse.urlencode(params)
    url = BASE + path + ("?" + query if query else "")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Binance HTTP {exc.code}: {detail[:300]}") from exc


def state_path(mode):
    return Path(__file__).with_name(f"state-{mode}.json")


def load_state(mode):
    path = state_path(mode)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"last_candle": None, "paper_usdt": "1000", "paper_btc": "0"}


def save_state(mode, state):
    path = state_path(mode)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def signal():
    candles = request("GET", "/api/v3/klines", {"symbol": SYMBOL, "interval": "1h", "limit": 52})
    closed = candles[:-1]
    if len(closed) < 51:
        raise RuntimeError("Not enough closed candles")
    closes = [D(row[4]) for row in closed]
    short_prev = sum(closes[-21:-1]) / 20
    long_prev = sum(closes[-51:-1]) / 50
    short_now = sum(closes[-20:]) / 20
    long_now = sum(closes[-50:]) / 50
    side = "BUY" if short_prev <= long_prev and short_now > long_now else "SELL" if short_prev >= long_prev and short_now < long_now else None
    return str(closed[-1][0]), closes[-1], side, short_now, long_now


def rules():
    data = request("GET", "/api/v3/exchangeInfo", {"symbol": SYMBOL})["symbols"][0]
    filters = {f["filterType"]: f for f in data["filters"]}
    lot = filters.get("MARKET_LOT_SIZE", filters["LOT_SIZE"])
    if D(lot["stepSize"]) == 0:
        lot = filters["LOT_SIZE"]
    minimum = D(filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {})).get("minNotional", "0"))
    return D(lot["stepSize"]), D(lot["minQty"]), minimum


def floor_step(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def balances(mode, state):
    if mode == "paper":
        return D(state["paper_usdt"]), D(state["paper_btc"])
    account = request("GET", "/api/v3/account", signed=True)
    assets = {row["asset"]: D(row["free"]) for row in account["balances"]}
    return assets.get("USDT", D(0)), assets.get("BTC", D(0))


def reconcile_pending(state):
    pending = state.get("pending_order")
    if not pending:
        return
    client_id = pending["client_id"]
    try:
        order = request("GET", "/api/v3/order", {"symbol": SYMBOL, "origClientOrderId": client_id}, signed=True)
    except Exception as exc:
        raise RuntimeError(f"Pending order {client_id} needs reconciliation; no new order sent: {exc}") from exc
    event = pending["event"]
    event.update(orderId=order.get("orderId"), status=order.get("status"), quantity=order.get("executedQty", event["quantity"]))
    state["history"] = ([event] + state.get("history", []))[:100]
    state["last_candle"] = pending["candle"]
    del state["pending_order"]
    save_state("testnet", state)
    print(f"Reconciled pending order {client_id}: {order.get('status')}")


def step(mode, max_usdt):
    state = load_state(mode)
    if mode == "testnet":
        reconcile_pending(state)
    candle, price, side, fast, slow = signal()
    print(f"closed candle={candle} BTC={price} SMA20={fast:.2f} SMA50={slow:.2f} signal={side or 'HOLD'}")
    if state["last_candle"] == candle:
        print("Already processed this candle")
        return
    usdt, btc = balances(mode, state)
    event = {"time": int(time.time() * 1000), "candle": candle, "signal": side or "HOLD", "price": str(price), "action": "HOLD", "quantity": "0"}
    if side:
        step_size, min_qty, min_notional = rules()
        if side == "BUY":
            budget = min(max_usdt, usdt * D("0.95"))
            quantity = floor_step(budget / price, step_size)
        else:
            quantity = floor_step(btc, step_size)
        notional = quantity * price
        if quantity < min_qty or notional < min_notional or notional <= 0:
            print("Skipped: below exchange minimum or insufficient balance")
            event["action"] = "SKIPPED"
        elif mode == "paper":
            # Conservative illustrative fee; actual fees and fills vary.
            fee = notional * D("0.001")
            if side == "BUY":
                if notional + fee > usdt:
                    quantity = floor_step(usdt / (price * D("1.001")), step_size)
                    notional = quantity * price
                    fee = notional * D("0.001")
                if quantity >= min_qty and notional >= min_notional:
                    state["paper_usdt"] = str(usdt - notional - fee)
                    state["paper_btc"] = str(btc + quantity)
            else:
                state["paper_usdt"] = str(usdt + notional - fee)
                state["paper_btc"] = str(btc - quantity)
            print(f"PAPER {side} {quantity} BTC at {price}; estimated fee {fee:.4f} USDT")
            event.update(action=side, quantity=str(quantity), fee=str(fee))
        else:
            # Save intent before sending. A timeout must never trigger a blind retry.
            client_id = f"sma-{candle}-{side.lower()}"
            state["pending_order"] = {"client_id": client_id, "candle": candle, "event": dict(event, action=side, quantity=str(quantity))}
            save_state(mode, state)
            result = request("POST", "/api/v3/order", {"symbol": SYMBOL, "side": side, "type": "MARKET", "quantity": format(quantity, "f"), "newClientOrderId": client_id}, signed=True)
            print(f"TESTNET orderId={result.get('orderId')} status={result.get('status')}")
            event.update(action=side, quantity=str(quantity), orderId=result.get("orderId"), status=result.get("status"))
            del state["pending_order"]
    state["last_candle"] = candle
    state["history"] = ([event] + state.get("history", []))[:100]
    save_state(mode, state)
    print(f"mode={mode} balance before step: {usdt} USDT, {btc} BTC")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["paper", "testnet"], default="paper")
    parser.add_argument("--max-usdt", type=D, default=D("25"), help="Maximum USDT per BUY; default 25")
    args = parser.parse_args()
    if args.max_usdt <= 0 or args.max_usdt > 100:
        parser.error("--max-usdt must be greater than 0 and at most 100")
    step(args.mode, args.max_usdt)


if __name__ == "__main__":
    main()
