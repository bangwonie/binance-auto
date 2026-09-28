"""Read-only Binance Futures Demo API credential check."""
import getpass
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request


def main():
    key = getpass.getpass("Futures Demo API key: ").strip()
    secret = getpass.getpass("Futures Demo API secret: ").strip()
    query = urllib.parse.urlencode({"timestamp": int(time.time() * 1000), "recvWindow": 5000})
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"https://demo-fapi.binance.com/fapi/v2/account?{query}&signature={signature}"
    req = urllib.request.Request(url, headers={"X-MBX-APIKEY": key})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            account = json.load(response)
    except urllib.error.HTTPError as error:
        print(f"HTTP {error.code}: {error.read().decode(errors='replace')[:240]}")
        return
    print(json.dumps({
        "canTrade": account.get("canTrade"),
        "multiAssetsMargin": account.get("multiAssetsMargin"),
        "availableBalance": account.get("availableBalance"),
        "totalWalletBalance": account.get("totalWalletBalance"),
        "totalUnrealizedProfit": account.get("totalUnrealizedProfit"),
        "btcPositions": [
            {k: position.get(k) for k in ("symbol", "positionAmt", "positionSide", "leverage", "marginType")}
            for position in account.get("positions", []) if position.get("symbol") == "BTCUSDT"
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
