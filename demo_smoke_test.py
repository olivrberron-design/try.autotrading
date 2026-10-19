#!/usr/bin/env python3
"""Binance Spot DEMO-only smoke test. Standard library only."""
import argparse
from decimal import Decimal, ROUND_DOWN
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "https://demo-api.binance.com"
PAIR = "BTCUSDC"
QUOTE = Decimal("25")
STATE = Path(__file__).resolve().parent / ".demo_smoke_state.json"


def load_keys():
    # Do not log credentials. Read only a local .env file or the environment.
    values = {}
    path = Path(__file__).resolve().parent / ".env"
    if path.exists():
        if path.is_symlink():
            raise RuntimeError(".env cannot be a symlink")
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if sep:
                values[name.strip()] = value.strip().strip('"').strip("'")
    key = os.environ.get("BINANCE_DEMO_API_KEY") or values.get("BINANCE_DEMO_API_KEY")
    secret = os.environ.get("BINANCE_DEMO_API_SECRET") or values.get("BINANCE_DEMO_API_SECRET")
    if not key or not secret:
        raise RuntimeError("Missing demo keys: fill in local .env (see .env.example)")
    return key, secret


class DemoClient:
    def __init__(self, key, secret):
        self.key, self.secret = key, secret
        self.offset = 0

    def request(self, method, endpoint, params=None, signed=False):
        if not endpoint.startswith("/api/v3/") or "://" in endpoint:
            raise ValueError("Unexpected API path")
        params = dict(params or {})
        if signed:
            params["timestamp"] = int(time.time() * 1000) + self.offset
            params["recvWindow"] = 5000
        query = urllib.parse.urlencode(params)
        if signed:
            sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            query += "&signature=" + sig
        url = BASE + endpoint
        payload = None
        if method == "GET":
            if query:
                url += "?" + query
        elif method == "POST":
            payload = query.encode()
        else:
            raise ValueError("Unsupported request method")
        req = urllib.request.Request(url, data=payload, method=method,
                                     headers={"X-MBX-APIKEY": self.key,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Only expose Binance API code/message, never URL, headers or signature.
            try:
                body = json.loads(exc.read())
                raise RuntimeError(f"Binance HTTP {exc.code}: {body.get('code')} {body.get('msg')}") from None
            except (ValueError, TypeError):
                raise RuntimeError(f"Binance HTTP {exc.code}") from None

    def sync_time(self):
        remote = self.request("GET", "/api/v3/time")["serverTime"]
        self.offset = int(remote) - int(time.time() * 1000)

    def account(self):
        return self.request("GET", "/api/v3/account", signed=True)

    def order(self, params, test=False):
        return self.request("POST", "/api/v3/order/test" if test else "/api/v3/order",
                            params=params, signed=True)

    def get_order(self, client_order_id):
        return self.request("GET", "/api/v3/order",
                            params={"symbol": PAIR, "origClientOrderId": client_order_id}, signed=True)


def balances(account):
    return {b["asset"]: Decimal(b["free"]) for b in account["balances"]}


def round_to_step(amount, step):
    amount, step = Decimal(str(amount)), Decimal(str(step))
    if step <= 0:
        raise ValueError("Invalid step size")
    return (amount / step).to_integral_value(rounding=ROUND_DOWN) * step


def qty_text(value):
    return format(Decimal(value).normalize(), "f")


def symbol_rules(info):
    symbols = [s for s in info["symbols"] if s["symbol"] == PAIR]
    if len(symbols) != 1:
        raise RuntimeError("BTCUSDC missing")
    s = symbols[0]
    if s["status"] != "TRADING" or not s.get("isSpotTradingAllowed", False):
        raise RuntimeError("BTCUSDC not available for spot")
    if "MARKET" not in s["orderTypes"]:
        raise RuntimeError("Market orders not available")
    filters = {f["filterType"]: f for f in s["filters"]}
    if "LOT_SIZE" not in filters:
        raise RuntimeError("Missing LOT_SIZE")
    return filters


def sell_quantity(available, filters):
    lot = filters.get("MARKET_LOT_SIZE", {})
    step = Decimal(lot.get("stepSize", "0"))
    if step == 0:
        lot = filters["LOT_SIZE"]
        step = Decimal(lot["stepSize"])
    qty = round_to_step(available, step)
    minqty = Decimal(lot.get("minQty", "0"))
    maxqty = Decimal(lot.get("maxQty", "0"))
    if qty <= 0 or qty < minqty or (maxqty and qty > maxqty):
        raise RuntimeError("Sell quantity outside lot size limits")
    return qty


def save_state(state):
    tmp = STATE.with_suffix(".json.tmp")
    with tmp.open("w") as fp:
        json.dump(state, fp, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, STATE)
    os.chmod(STATE, 0o600)


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else None


def describe_order(client, state, side):
    ident = state[side + "_id"]
    try:
        order = client.get_order(ident)
    except Exception as exc:
        print(f"{side.upper()} status unknown; check Binance demo order history manually: {type(exc).__name__}: {exc}")
        return None
    print(f"{side.upper()} order: status={order['status']}, executedQty={order['executedQty']}, id={ident}")
    return order


def main():
    parser = argparse.ArgumentParser(description="Binance Spot Demo-only feasibility smoke test")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--trade", action="store_true", help="Place real DEMO-only orders after confirmation")
    group.add_argument("--status", action="store_true", help="Read previous DEMO order statuses")
    args = parser.parse_args()
    print("Environment: BINANCE SPOT DEMO ONLY |", BASE, "|", PAIR)
    key, secret = load_keys()
    client = DemoClient(key, secret)
    client.sync_time()
    account = client.account()
    print("Authentication OK | canTrade:", account.get("canTrade"))
    print("Available demo USDC:", balances(account).get("USDC", Decimal(0)))
    state = load_state()

    if args.status:
        if not state:
            print("No stored DEMO test order.")
            return
        for side in ("buy", "sell"):
            if state.get(side + "_id"):
                describe_order(client, state, side)
        return

    if not account.get("canTrade"):
        raise RuntimeError("Account does not permit trading")
    info = client.request("GET", "/api/v3/exchangeInfo", {"symbol": PAIR})
    filters = symbol_rules(info)
    # quoteOrderQty market BUY lets Binance round to valid BTC lot.
    market_allowed = info["symbols"][0].get("quoteOrderQtyMarketAllowed", False)
    if not market_allowed:
        raise RuntimeError("quoteOrderQty MARKET BUY not permitted for pair")
    min_notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
    threshold = Decimal(min_notional.get("minNotional", "0"))
    if threshold > QUOTE:
        raise RuntimeError("25 USDC is below min notional")
    if balances(account).get("USDC", Decimal(0)) < QUOTE:
        raise RuntimeError("Insufficient DEMO USDC")

    buy_params = {"symbol": PAIR, "side": "BUY", "type": "MARKET", "quoteOrderQty": str(QUOTE)}
    client.order(buy_params, test=True)
    print("BUY /order/test accepted (no order placed).")
    if not args.trade:
        print("READ-ONLY TEST COMPLETE. To trade 25 DEMO USDC, run: python3 demo_smoke_test.py --trade")
        return
    if state:
        raise RuntimeError("Existing state file: inspect --status and Binance order history before starting a new round trip")
    print("WARNING: will place a 25 USDC MARKET BUY and a MARKET SELL on the VIRTUAL DEMO exchange.")
    if input("Type DEMO TRADE to proceed: ").strip() != "DEMO TRADE":
        print("Cancelled.")
        return

    state = {"buy_id": "demoB" + uuid.uuid4().hex[:24], "phase": "buy_intent"}
    save_state(state)  # Persist intent BEFORE network call; never blindly retry an ambiguous submission.
    buy_params["newClientOrderId"] = state["buy_id"]
    try:
        client.order(buy_params)
    except Exception:
        print("BUY outcome uncertain. Run --status; do NOT retry until you verify Binance order history.")
        raise
    state["phase"] = "buy_submitted"
    save_state(state)
    bought = describe_order(client, state, "buy")
    if not bought or bought["status"] != "FILLED" or Decimal(bought["executedQty"]) <= 0:
        print("BUY not confirmed FILLED. Inspect --status and exchange history; no SELL sent.")
        return

    # Count actual BTC received after commissions in BTC, avoiding sale of pre-existing BTC.
    fills = client.request("GET", "/api/v3/myTrades",
                          {"symbol": PAIR, "orderId": bought["orderId"], "limit": 1000}, signed=True)
    if not fills:
        raise RuntimeError("Missing BUY fill details: do not sell")
    acquired = sum((Decimal(t["qty"]) for t in fills), Decimal(0))
    fee_btc = sum((Decimal(t["commission"]) for t in fills if t["commissionAsset"] == "BTC"), Decimal(0))
    acquired -= fee_btc
    if acquired <= 0:
        raise RuntimeError("No net BTC acquired")
    current_btc = balances(client.account()).get("BTC", Decimal(0))
    qty = sell_quantity(min(acquired, current_btc), filters)
    if Decimal(bought["cummulativeQuoteQty"]) > 0 and qty <= 0:
        raise RuntimeError("Sell quantity invalid")
    sell_params = {"symbol": PAIR, "side": "SELL", "type": "MARKET", "quantity": qty_text(qty)}
    client.order(sell_params, test=True)
    print("SELL /order/test accepted (no order placed). Quantity:", qty_text(qty))
    # The sell is another independent explicit operation, never retried on error.
    if input("Type SELL DEMO to sell the DEMO BTC just acquired: ").strip() != "SELL DEMO":
        print("BTC remains on DEMO account. Run --status and check positions.")
        return
    state["sell_id"] = "demoS" + uuid.uuid4().hex[:24]
    state["phase"] = "sell_intent"
    save_state(state)
    sell_params["newClientOrderId"] = state["sell_id"]
    try:
        client.order(sell_params)
    except Exception:
        print("SELL outcome uncertain. Run --status; do NOT retry blindly.")
        raise
    state["phase"] = "sell_submitted"
    save_state(state)
    sold = describe_order(client, state, "sell")
    if sold and sold["status"] == "FILLED":
        state["phase"] = "completed"
        save_state(state)
        print("DEMO round trip completed; check both fills and remaining dust on Binance.")
    else:
        print("SELL not confirmed filled; check --status.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, urllib.error.URLError) as exc:
        print("STOPPED:", type(exc).__name__, str(exc))
        raise SystemExit(1)
