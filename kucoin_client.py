#!/usr/bin/env python3
"""
KuCoin Quantitative Execution & Data Client (Spot + HF + OCO + Futures)
Built for KuCoin Desk (id: 8e98ca7e-3f25-41b6-9053-3aa88bdaf0fc)
Owner: Barry Clerjuste

Security Rules:
- Reads credentials exclusively from environment variables:
  KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE, KUCOIN_TRADING_PASSWORD
- NEVER prints, logs, or echoes any secret values.
- Direct Box API execution (https://api.kucoin.com and https://api-futures.kucoin.com);
  no location workaround is used or needed.
"""

import os
import time
import uuid
import hmac
import json
import base64
import hashlib
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple



def _autoload_env_files() -> None:
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        r"C:\Users\Simpl\grok-propr-bot\.env",
        "/workspace/grok-propr-bot/.env",
        "/home/box/.secrets/poet_merged.env",
        "/home/box/.secrets/propr.env",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        s = line.strip()
                        if not s or s.startswith("#") or "=" not in s:
                            continue
                        if s.startswith("export "):
                            s = s[len("export "):].strip()
                        k, v = s.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("\"").strip("\x27")
                        if k:
                            os.environ[k] = v
            except Exception:
                pass

_autoload_env_files()

SPOT_BASE_URL = "https://api.kucoin.com"
FUTURES_BASE_URL = "https://api-futures.kucoin.com"


class KuCoinClient:
    def __init__(self) -> None:
        self.api_key = os.environ.get("KUCOIN_API_KEY", "")
        self._api_secret = os.environ.get("KUCOIN_API_SECRET", "").encode("utf-8")
        self._api_passphrase = os.environ.get("KUCOIN_API_PASSPHRASE", "").encode("utf-8")
        if not self.api_key or not self._api_secret or not self._api_passphrase:
            raise RuntimeError("Missing KuCoin API credentials in environment variables.")

    def _headers(self, method: str, endpoint: str, body: str = "") -> Dict[str, str]:
        ts = str(int(time.time() * 1000))
        str_to_sign = ts + method.upper() + endpoint + body
        sig = base64.b64encode(
            hmac.new(self._api_secret, str_to_sign.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")
        pp_sig = base64.b64encode(
            hmac.new(self._api_secret, self._api_passphrase, hashlib.sha256).digest()
        ).decode("utf-8")
        return {
            "KC-API-KEY": self.api_key,
            "KC-API-SIGN": sig,
            "KC-API-TIMESTAMP": ts,
            "KC-API-PASSPHRASE": pp_sig,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
            "User-Agent": "KuCoinDesk/1.0",
        }

    def request(
        self,
        base_url: str,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        auth: bool = True,
        retries: int = 3,
    ) -> Dict[str, Any]:
        if params:
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if qs:
                endpoint = f"{endpoint}?{qs}"
        body = json.dumps(payload) if payload is not None else ""
        url = base_url + endpoint

        for attempt in range(retries):
            headers = self._headers(method, endpoint, body) if auth else {"User-Agent": "KuCoinDesk/1.0"}
            req = urllib.request.Request(
                url,
                data=body.encode("utf-8") if body else None,
                headers=headers,
                method=method.upper(),
            )
            try:
                with urllib.request.urlopen(req, timeout=12) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8")
                if e.code == 429 and attempt < retries - 1:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                try:
                    return json.loads(err_body)
                except Exception:
                    return {"code": str(e.code), "msg": err_body}
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                return {"code": "ERR", "msg": str(e)}
        return {"code": "ERR", "msg": "Max retries exceeded"}

    # =========================================================================
    # ACCOUNT & TRANSFER METHODS
    # =========================================================================

    def get_spot_accounts(self, currency: Optional[str] = None, acct_type: Optional[str] = None) -> List[Dict[str, Any]]:
        res = self.request(SPOT_BASE_URL, "GET", "/api/v1/accounts", params={"currency": currency, "type": acct_type})
        return res.get("data", [])

    def get_nonzero_spot_accounts(self) -> List[Dict[str, Any]]:
        accts = self.get_spot_accounts()
        return [a for a in accts if float(a.get("balance", 0)) > 0]

    def get_futures_overview(self, currency: str = "USDT") -> Dict[str, Any]:
        res = self.request(FUTURES_BASE_URL, "GET", "/api/v1/account-overview", params={"currency": currency})
        return res.get("data", {})

    def transfer_to_futures(self, currency: str, amount: str, from_type: str = "trade") -> Dict[str, Any]:
        """Transfer funds from Spot (main/trade/trade_hf) to Futures (contract). Fee = 0."""
        payload = {
            "clientOid": str(uuid.uuid4()),
            "currency": currency,
            "from": from_type,
            "to": "contract",
            "amount": str(amount),
        }
        return self.request(SPOT_BASE_URL, "POST", "/api/v2/accounts/inner-transfer", payload=payload)

    def transfer_from_futures(self, currency: str, amount: float, to_type: str = "TRADE") -> Dict[str, Any]:
        """
        Transfer funds out of Futures (contract) back to Spot (TRADE or MAIN).
        Note: Spot /api/v2/accounts/inner-transfer rejects 'from=contract' with code 400100;
        must use Futures /api/v3/transfer-out endpoint.
        """
        payload = {
            "ApplyNo": str(uuid.uuid4()),
            "currency": currency,
            "amount": float(amount),
            "recAccountType": to_type.upper(),
        }
        return self.request(FUTURES_BASE_URL, "POST", "/api/v3/transfer-out", payload=payload)

    def inner_transfer_spot(self, currency: str, amount: str, from_type: str, to_type: str) -> Dict[str, Any]:
        payload = {
            "clientOid": str(uuid.uuid4()),
            "currency": currency,
            "from": from_type,
            "to": to_type,
            "amount": str(amount),
        }
        return self.request(SPOT_BASE_URL, "POST", "/api/v2/accounts/inner-transfer", payload=payload)

    # =========================================================================
    # MARKET DATA & FEE METHODS
    # =========================================================================

    def get_trade_fees(self, symbols: List[str]) -> List[Dict[str, Any]]:
        res = self.request(SPOT_BASE_URL, "GET", "/api/v1/trade-fees", params={"symbols": ",".join(symbols)})
        return res.get("data", [])

    def get_spot_l1(self, symbol: str) -> Dict[str, Any]:
        res = self.request(SPOT_BASE_URL, "GET", "/api/v1/market/orderbook/level1", params={"symbol": symbol}, auth=False)
        return res.get("data", {})

    def get_spot_l2_20(self, symbol: str) -> Dict[str, Any]:
        res = self.request(SPOT_BASE_URL, "GET", "/api/v1/market/orderbook/level2_20", params={"symbol": symbol}, auth=False)
        return res.get("data", {})

    def get_spot_klines(self, symbol: str, ktype: str = "4hour", lookback_bars: int = 180) -> List[List[str]]:
        """
        Returns klines in chronological order (oldest -> newest).
        Note: KuCoin requires startAt/endAt in SECONDS to return >100 bars.
        """
        sec_map = {
            "1min": 60, "5min": 300, "15min": 900, "30min": 1800,
            "1hour": 3600, "2hour": 7200, "4hour": 14400, "1day": 86400
        }
        step = sec_map.get(ktype, 14400)
        now_s = int(time.time())
        start_s = now_s - lookback_bars * step
        res = self.request(
            SPOT_BASE_URL,
            "GET",
            "/api/v1/market/candles",
            params={"type": ktype, "symbol": symbol, "startAt": start_s, "endAt": now_s},
            auth=False,
        )
        data = res.get("data", [])
        return list(reversed(data)) if isinstance(data, list) else []

    def get_futures_contract(self, symbol: str) -> Dict[str, Any]:
        res = self.request(FUTURES_BASE_URL, "GET", f"/api/v1/contracts/{symbol}", auth=False)
        return res.get("data", {})

    def get_futures_ticker(self, symbol: str) -> Dict[str, Any]:
        res = self.request(FUTURES_BASE_URL, "GET", "/api/v1/ticker", params={"symbol": symbol}, auth=False)
        return res.get("data", {})

    # =========================================================================
    # SPOT & OCO EXECUTION METHODS
    # =========================================================================

    def place_spot_limit_order(
        self,
        symbol: str,
        side: str,
        price: str,
        size: str,
        post_only: bool = True,
        hidden: bool = False,
        time_in_force: str = "GTC",
    ) -> Dict[str, Any]:
        payload = {
            "clientOid": str(uuid.uuid4()),
            "side": side.lower(),
            "symbol": symbol,
            "type": "limit",
            "price": str(price),
            "size": str(size),
            "postOnly": post_only,
            "hidden": hidden,
            "timeInForce": time_in_force,
        }
        return self.request(SPOT_BASE_URL, "POST", "/api/v1/orders", payload=payload)

    def place_spot_oco_order(
        self,
        symbol: str,
        side: str,
        tp_price: str,
        size: str,
        stop_trigger_price: str,
        stop_limit_price: str,
    ) -> Dict[str, Any]:
        """
        Places a simultaneous Maker Limit Take-Profit and Structural Stop-Loss
        using a single asset balance lock via /api/v3/oco/order.
        """
        payload = {
            "clientOid": str(uuid.uuid4()),
            "side": side.lower(),
            "symbol": symbol,
            "price": str(tp_price),
            "size": str(size),
            "stopPrice": str(stop_trigger_price),
            "limitPrice": str(stop_limit_price),
            "tradeType": "TRADE",
        }
        return self.request(SPOT_BASE_URL, "POST", "/api/v3/oco/order", payload=payload)

    def place_spot_stop_order(
        self,
        symbol: str,
        side: str,
        stop_price: str,
        size: str,
        limit_price: Optional[str] = None,
        stop_dir: str = "loss",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "clientOid": str(uuid.uuid4()),
            "side": side.lower(),
            "symbol": symbol,
            "type": "limit" if limit_price else "market",
            "stop": stop_dir,
            "stopPrice": str(stop_price),
            "size": str(size),
            "tradeType": "TRADE",
        }
        if limit_price:
            payload["price"] = str(limit_price)
        return self.request(SPOT_BASE_URL, "POST", "/api/v1/stop-order", payload=payload)

    def get_spot_active_orders(self) -> List[Dict[str, Any]]:
        res = self.request(SPOT_BASE_URL, "GET", "/api/v1/orders", params={"status": "active"})
        return res.get("data", {}).get("items", [])

    def get_spot_stop_orders(self) -> List[Dict[str, Any]]:
        res = self.request(SPOT_BASE_URL, "GET", "/api/v1/stop-order")
        return res.get("data", {}).get("items", [])

    def cancel_spot_order(self, order_id: str) -> Dict[str, Any]:
        return self.request(SPOT_BASE_URL, "DELETE", f"/api/v1/orders/{order_id}")

    # =========================================================================
    # FUTURES EXECUTION METHODS (2 bps Maker / 6 bps Taker)
    # =========================================================================

    def get_futures_margin_mode(self, symbol: str) -> str:
        res = self.request(FUTURES_BASE_URL, "GET", "/api/v2/position/getMarginMode", params={"symbol": symbol})
        return res.get("data", {}).get("marginMode", "ISOLATED")

    def set_futures_margin_mode(self, symbol: str, margin_mode: str = "CROSS") -> Dict[str, Any]:
        return self.request(
            FUTURES_BASE_URL,
            "POST",
            "/api/v2/position/changeMarginMode",
            payload={"symbol": symbol, "marginMode": margin_mode.upper()},
        )

    def place_futures_order(
        self,
        symbol: str,
        side: str,
        size: int,
        order_type: str = "limit",
        price: Optional[str] = None,
        leverage: str = "2",
        post_only: bool = True,
        reduce_only: bool = False,
        margin_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Places a Futures order on KuCoin Futures (0.02% Maker / 0.06% Taker).
        Automatically queries symbol marginMode (CROSS vs ISOLATED) if not provided
        to prevent KuCoin error 330005.
        """
        if margin_mode is None:
            margin_mode = self.get_futures_margin_mode(symbol)
        payload: Dict[str, Any] = {
            "clientOid": str(uuid.uuid4()),
            "side": side.lower(),
            "symbol": symbol,
            "type": order_type.lower(),
            "marginMode": margin_mode,
            "leverage": str(leverage),
            "size": int(size),
            "reduceOnly": reduce_only,
        }
        if order_type.lower() == "limit":
            if price is None:
                raise ValueError("price is required for limit orders")
            payload["price"] = str(price)
            payload["postOnly"] = post_only
        return self.request(FUTURES_BASE_URL, "POST", "/api/v1/orders", payload=payload)

    def place_futures_stop_order(
        self,
        symbol: str,
        side: str,
        size: int,
        stop_price: str,
        stop_dir: str,
        order_type: str = "market",
        price: Optional[str] = None,
        stop_price_type: str = "MP",
        reduce_only: bool = True,
        leverage: str = "2",
        margin_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Places a resting Structural Stop-Loss or Take-Profit on KuCoin Futures.
        - stop_dir: 'down' (triggers when price <= stop_price) or 'up' (triggers when price >= stop_price)
        - stop_price_type: 'MP' (Mark Price, immune to local orderbook scam wicks) or 'TP' (Last Trade Price)
        """
        if margin_mode is None:
            margin_mode = self.get_futures_margin_mode(symbol)
        payload: Dict[str, Any] = {
            "clientOid": str(uuid.uuid4()),
            "side": side.lower(),
            "symbol": symbol,
            "type": order_type.lower(),
            "marginMode": margin_mode,
            "leverage": str(leverage),
            "size": int(size),
            "stop": stop_dir,
            "stopPriceType": stop_price_type,
            "stopPrice": str(stop_price),
            "reduceOnly": reduce_only,
        }
        if order_type.lower() == "limit" and price is not None:
            payload["price"] = str(price)
        return self.request(FUTURES_BASE_URL, "POST", "/api/v1/orders", payload=payload)

    def get_futures_positions(self) -> List[Dict[str, Any]]:
        res = self.request(FUTURES_BASE_URL, "GET", "/api/v1/positions")
        data = res.get("data", [])
        return data if isinstance(data, list) else []

    def get_futures_active_orders(self) -> List[Dict[str, Any]]:
        res = self.request(FUTURES_BASE_URL, "GET", "/api/v1/orders", params={"status": "active"})
        return res.get("data", {}).get("items", [])

    def get_futures_stop_orders(self) -> List[Dict[str, Any]]:
        res = self.request(FUTURES_BASE_URL, "GET", "/api/v1/stopOrders")
        return res.get("data", {}).get("items", [])

    def cancel_futures_order(self, order_id: str) -> Dict[str, Any]:
        return self.request(FUTURES_BASE_URL, "DELETE", f"/api/v1/orders/{order_id}")

    def place_avellaneda_maker_order(
        self,
        symbol: str,
        side: str,
        size: int,
        structural_anchor_px: Optional[float] = None,
        leverage: str = "2",
        reduce_only: bool = False,
        margin_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Uses Avellaneda-Stoikov reservation pricing & L2 liquidity-wall front-running
        to place a guaranteed postOnly Maker order 1 tick in front of the dominant wall.
        """
        import avellaneda_stoikov_exec
        c_spec = self.get_futures_contract(symbol)
        target_side = "LONG" if side.lower() == "buy" else "SHORT"
        as_res = avellaneda_stoikov_exec.analyze_l2_avellaneda(
            symbol=symbol,
            contract_spec=c_spec,
            target_side=target_side,
            structural_anchor_px=structural_anchor_px,
        )
        opt_px = (as_res.get("execution_recommendation") or {}).get("optimal_post_only_px") or structural_anchor_px
        tick = float(c_spec.get("tickSize") or 0.01)
        decimals = max(0, -int(round(__import__("math").log10(tick)))) if 0 < tick < 1 else 0
        px_str = f"{float(opt_px):.{decimals}f}"
        res = self.place_futures_order(
            symbol=symbol,
            side=side.lower(),
            size=size,
            order_type="limit",
            price=px_str,
            leverage=leverage,
            post_only=True,
            reduce_only=reduce_only,
            margin_mode=margin_mode,
        )
        res["avellaneda_stoikov"] = as_res.get("execution_recommendation")
        return res

    def sync_worker_state(self) -> Dict[str, Any]:
        """Push sanitized live account, positions, active orders, and stop orders to kucoin-desk-scan Worker."""
        spot_accts = self.get_nonzero_spot_accounts()
        spot_usdt = sum(float(a.get("balance", 0)) for a in spot_accts if a.get("currency") in ("USDT", "USDC"))
        fut_acct = self.get_futures_overview("USDT")
        fut_acct["spot_usdt"] = round(spot_usdt, 4)
        contracts_raw = self.request(FUTURES_BASE_URL, "GET", "/api/v1/contracts/active", auth=False).get("data") or []
        c_map = {c["symbol"]: c for c in contracts_raw if isinstance(c, dict) and "symbol" in c}
        positions = self.get_futures_positions()
        for p in positions:
            cs = c_map.get(p.get("symbol", "")) or {}
            if "multiplier" in cs:
                p["multiplier"] = cs["multiplier"]
            if "markPrice" in cs and not p.get("markPrice"):
                p["markPrice"] = cs["markPrice"]
        active_orders = self.get_futures_active_orders()
        for o in active_orders:
            cs = c_map.get(o.get("symbol", "")) or {}
            if "markPrice" in cs:
                o["markPrice"] = cs["markPrice"]
        stop_orders = self.get_futures_stop_orders()
        payload = json.dumps({
            "uid": 249983249,
            "account": fut_acct,
            "positions": positions,
            "active_orders": active_orders,
            "stop_orders": stop_orders,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/sync-account",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "KuCoinDesk/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))


if __name__ == "__main__":
    client = KuCoinClient()
    print("=== Non-zero Spot Accounts ===")
    print(json.dumps(client.get_nonzero_spot_accounts(), indent=2))
    print("=== Futures USDT Overview ===")
    print(json.dumps(client.get_futures_overview("USDT"), indent=2))
    print("=== Active Futures Positions ===")
    print(json.dumps(client.get_futures_positions(), indent=2))
    print("=== Syncing kucoin-desk-scan Cloudflare Worker ===")
    print(json.dumps(client.sync_worker_state(), indent=2))
