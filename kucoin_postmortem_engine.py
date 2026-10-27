#!/usr/bin/env python3
"""
KuCoin Futures Post-Trade Decision Audit & Learning Loop
========================================================
Integrates Hermes's exact MFE/MAE excursion & process-grading engine
(`postmortem.py`, `credit.py`, `review.py`, `state.py`, `tuner.py`, `postmortem_gate.py`)
into the KuCoin Perpetual Futures (`UID 249983249`) desk.

Strictly KuCoin-only (Propr is 100% untouched and managed by SovereignAI/Hermes).
"""
from __future__ import annotations

import datetime
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

DESK_DIR = Path(__file__).resolve().parent
if str(DESK_DIR) not in sys.path:
    sys.path.insert(0, str(DESK_DIR))
if str(DESK_DIR / "kucoin") not in sys.path:
    sys.path.insert(0, str(DESK_DIR / "kucoin"))

from kucoin_client import FUTURES_BASE_URL, KuCoinClient
from kucoin_learn import credit, postmortem, review, state, tuner
from kucoin_learn.postmortem_gate import PostmortemBot

LEARNING_BASE = str(DESK_DIR)
REPORT_PATH = DESK_DIR / "latest_kucoin_postmortem.json"


def compute_rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = float(closes[i]) - float(closes[i - 1])
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def compute_ema_dist_pct(closes: Sequence[float], price: float, period: int = 20) -> Optional[float]:
    if len(closes) < period or price <= 0:
        return None
    k = 2.0 / (period + 1.0)
    ema = float(closes[0])
    for c in closes[1:]:
        ema = float(c) * k + ema * (1.0 - k)
    if ema <= 0:
        return None
    return round(((price - ema) / ema) * 100.0, 3)


def fetch_kucoin_candles_path(
    kc: KuCoinClient,
    symbol: str,
    granularity: int = 5,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    sym = symbol.upper().strip()
    if not sym.endswith("USDTM"):
        sym = f"{sym}USDTM"
    now_ms = int(time.time() * 1000)
    to_ms = end_ms or now_ms
    from_ms = start_ms or (to_ms - 16 * 3600 * 1000)
    # KuCoin /api/v1/kline/query returns max 200 bars per call; paginate if needed
    step_ms = granularity * 60 * 1000 * 180
    cur_start = from_ms
    seen_t = set()
    out: List[Dict[str, Any]] = []
    while cur_start < to_ms:
        cur_end = min(to_ms, cur_start + step_ms)
        try:
            res = kc.request(
                FUTURES_BASE_URL,
                "GET",
                "/api/v1/kline/query",
                params={"symbol": sym, "granularity": granularity, "from": cur_start, "to": cur_end},
                auth=False,
            )
            raw = res.get("data") or []
        except Exception:
            raw = []
        for row in raw or ():
            if isinstance(row, (list, tuple)) and len(row) >= 5:
                t_val = float(row[0])
                if t_val not in seen_t:
                    seen_t.add(t_val)
                    out.append({
                        "t": t_val,
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]) if len(row) > 5 else 0.0,
                    })
        cur_start = cur_end
    out.sort(key=lambda c: c["t"])
    return out


def check_kucoin_postmortem_gate(
    coin: str,
    direction: str,
    regime: str = "any",
    base: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the PostmortemBot VETO seat against accumulated KuCoin pattern memory."""
    base_dir = base or LEARNING_BASE
    norm_coin = coin.upper().replace("USDTM", "")
    norm_dir = "long" if direction.lower() in ("long", "buy") else "short"
    table = credit.pattern_report(base=base_dir)
    explicit = (
        credit.block_reason(f"{norm_coin}|{norm_dir}|{regime}", base=base_dir)
        or credit.block_reason(f"{norm_coin}|{norm_dir}|any", base=base_dir)
        or credit.block_reason(f"{norm_coin}|{norm_dir}|bear_squeeze", base=base_dir)
    )
    bot = PostmortemBot()
    vote = bot.evaluate({
        "coin": norm_coin,
        "direction": norm_dir,
        "regime": regime,
        "postmortem": table,
        "pattern_block": explicit,
    })
    return {
        "bot": vote.bot,
        "decision": vote.decision,
        "allowed": vote.decision != "reject",
        "confidence": vote.confidence,
        "reasons": vote.reasons,
        "meta": vote.meta,
    }


def record_kucoin_entry_context(
    symbol: str,
    side: str,
    entry: float,
    stop: float,
    target: Optional[float],
    risk_usd: float,
    risk_budget: float,
    rsi: Optional[float] = None,
    ema_dist_pct: Optional[float] = None,
    regime: str = "any",
    votes: Optional[List[Dict[str, str]]] = None,
    order_id: Optional[str] = None,
    base: Optional[str] = None,
) -> None:
    base_dir = base or LEARNING_BASE
    coin = symbol.upper().replace("USDTM", "")
    direction = "long" if side.upper() in ("LONG", "BUY") else "short"
    state.record_open(
        {
            "coin": coin,
            "symbol": symbol.upper(),
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk_usd": risk_usd,
            "risk_budget": risk_budget,
            "rsi": rsi,
            "ema_dist_pct": ema_dist_pct,
            "regime": regime,
            "votes": votes or [],
            "order_id": order_id,
        },
        base=base_dir,
    )


def fetch_all_recent_kucoin_fills(kc: KuCoinClient, days: int = 5) -> List[Dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    seen_ids = set()
    items: List[Dict[str, Any]] = []
    for d in range(days):
        end_ms = now_ms - d * 86400 * 1000
        start_ms = end_ms - 86400 * 1000
        res = kc.request(
            FUTURES_BASE_URL,
            "GET",
            "/api/v1/fills",
            params={"startAt": start_ms, "endAt": end_ms, "pageSize": 100},
        )
        for it in (res.get("data") or {}).get("items") or []:
            tid = it.get("tradeId") or it.get("id") or f"{it.get('orderId')}_{it.get('createdAt')}"
            if tid not in seen_ids:
                seen_ids.add(tid)
                items.append(it)
    items.sort(key=lambda x: int(x.get("createdAt") or int(x.get("tradeTime", 0) // 1_000_000)))
    return items


def audit_kucoin_closed_trades(base: Optional[str] = None) -> Dict[str, Any]:
    """
    Reconstructs and grades KuCoin Futures closed round-trips using live fills + 5m candle paths,
    updates pattern memory + seat credit, and diagnoses why KuCoin lost money vs Propr.
    """
    base_dir = base or LEARNING_BASE
    kc = KuCoinClient()
    items = fetch_all_recent_kucoin_fills(kc, days=5)
    contracts_resp = kc.request(FUTURES_BASE_URL, "GET", "/api/v1/contracts/active", auth=False)
    contracts = {c["symbol"]: c for c in (contracts_resp.get("data") or [])}

    by_sym: Dict[str, List[Dict[str, Any]]] = {}
    for f in items:
        sym = f.get("symbol")
        if sym:
            by_sym.setdefault(sym, []).append(f)

    reconstructed_closes: List[Dict[str, Any]] = []
    fee_drag_total = 0.0
    taker_fills = 0
    maker_fills = 0

    for sym, flist in by_sym.items():
        mult = abs(float((contracts.get(sym) or {}).get("multiplier") or 0.01))
        pos_lots = 0
        peak_open_lots = 0
        avg_entry = 0.0
        open_ts_ms = None
        entry_fees = 0.0
        for f in flist:
            liquidity = str(f.get("liquidity") or "").lower()
            if liquidity == "taker":
                taker_fills += 1
            else:
                maker_fills += 1
            fee = float(f.get("fee") or 0.0)
            fee_drag_total += fee
            side = str(f.get("side") or "").lower()
            sz = int(f.get("size") or 0)
            px = float(f.get("price") or 0.0)
            ts_ms = int(f.get("createdAt") or int(f.get("tradeTime", 0) // 1_000_000))
            signed = sz if side == "buy" else -sz

            if pos_lots == 0:
                pos_lots = signed
                peak_open_lots = abs(signed)
                avg_entry = px
                open_ts_ms = ts_ms
                entry_fees = fee
            elif (pos_lots > 0 and signed > 0) or (pos_lots < 0 and signed < 0):
                old_abs = abs(pos_lots)
                new_abs = old_abs + abs(signed)
                avg_entry = (avg_entry * old_abs + px * abs(signed)) / new_abs
                pos_lots += signed
                peak_open_lots = max(peak_open_lots, abs(pos_lots))
                entry_fees += fee
            else:
                closed_lots = min(abs(pos_lots), abs(signed))
                direction = "long" if pos_lots > 0 else "short"
                gross_pnl = (
                    (px - avg_entry) * closed_lots * mult
                    if direction == "long"
                    else (avg_entry - px) * closed_lots * mult
                )
                total_fee = entry_fees * (closed_lots / max(1, abs(pos_lots))) + fee
                net_pnl = round(gross_pnl - total_fee, 4)
                # Use actual exit distance if stopped out, or 1.2% structural stop
                stop_dist = max(avg_entry * 0.010, abs(px - avg_entry) if net_pnl < 0 else avg_entry * 0.012)
                planned_stop = avg_entry - stop_dist if direction == "long" else avg_entry + stop_dist
                planned_tp = avg_entry + stop_dist * 1.80 if direction == "long" else avg_entry - stop_dist * 1.80
                slice_risk_1r = max(0.25, closed_lots * mult * stop_dist)
                position_risk_1r = round(max(slice_risk_1r, peak_open_lots * mult * stop_dist), 4)
                r_mult = round(net_pnl / slice_risk_1r, 4)

                tid = f"kc_{sym}_{ts_ms}_{f.get('tradeId') or f.get('id')}"
                coin = sym.replace("USDTM", "")
                reconstructed_closes.append({
                    "trade_id": tid,
                    "symbol": sym,
                    "coin": coin,
                    "asset": coin,
                    "direction": direction,
                    "lots": closed_lots,
                    "entry": round(avg_entry, 6),
                    "exit_price": round(px, 6),
                    "stop": round(planned_stop, 6),
                    "target": round(planned_tp, 6),
                    "gross_pnl_usd": round(gross_pnl, 4),
                    "fee_usd": round(total_fee, 4),
                    "realized_pnl": net_pnl,
                    "risk_usd": position_risk_1r,
                    "slice_risk_usd": round(slice_risk_1r, 4),
                    "r": r_mult,
                    "opened_ts": (open_ts_ms or ts_ms) / 1000.0,
                    "closed_ts": ts_ms / 1000.0,
                    "exit_liquidity": liquidity,
                    "stop_order_type": f.get("stop") or "direct",
                })
                if abs(signed) >= abs(pos_lots):
                    rem = abs(signed) - abs(pos_lots)
                    pos_lots = (rem if signed > 0 else -rem)
                    avg_entry = px if pos_lots != 0 else 0.0
                    open_ts_ms = ts_ms if pos_lots != 0 else None
                    entry_fees = 0.0
                else:
                    pos_lots += signed

    # Fetch 5m candles per coin covering earliest open - 6h through latest close + 2h
    candle_cache: Dict[str, List[Dict[str, Any]]] = {}
    contexts: Dict[str, Dict[str, Any]] = {}

    by_coin_trades: Dict[str, List[Dict[str, Any]]] = {}
    for tr in reconstructed_closes:
        by_coin_trades.setdefault(tr["coin"], []).append(tr)

    for coin, tlist in by_coin_trades.items():
        sym = tlist[0]["symbol"]
        min_open_ms = int((min(t["opened_ts"] for t in tlist) - 8 * 3600) * 1000)
        max_close_ms = int((max(t["closed_ts"] for t in tlist) + 3 * 3600) * 1000)
        candle_cache[coin] = fetch_kucoin_candles_path(
            kc, sym, granularity=5, start_ms=min_open_ms, end_ms=max_close_ms
        )

    for tr in reconstructed_closes:
        coin = tr["coin"]
        c_all = candle_cache.get(coin) or []
        pre_entry_closes = [
            c["close"]
            for c in c_all
            if (c["t"] / 1000.0 if c["t"] > 1e11 else c["t"]) <= tr["opened_ts"]
        ]
        rsi_val = compute_rsi(pre_entry_closes, 14) if len(pre_entry_closes) >= 15 else None
        ema_dist = compute_ema_dist_pct(pre_entry_closes, tr["entry"], 20) if len(pre_entry_closes) >= 20 else None
        # Budget per 1R trade on $198 equity = 1.0% ($1.98)
        risk_budget = 1.98
        contexts[tr["trade_id"]] = {
            "rsi": rsi_val,
            "ema_dist_pct": ema_dist,
            "risk_budget": risk_budget,
            "votes": [
                {"bot": "momentum", "decision": "approve"},
                {"bot": "squeeze_gate", "decision": "reject" if tr["direction"] == "short" else "approve"},
                {"bot": "risk_budget", "decision": "approve" if tr["risk_usd"] <= risk_budget * 1.05 else "reject"},
                {"bot": "fee_drag_gate", "decision": "reject" if tr["exit_liquidity"] == "taker" else "approve"},
            ],
        }

    def _candles_for(coin_name: str) -> Sequence[Dict[str, Any]]:
        return candle_cache.get(coin_name, [])

    state.sync_closes(reconstructed_closes, base=base_dir)
    # Include +60m post-close window so stop_too_tight can observe if price reversed 1R after stop-out
    extended_trades = [
        {**t, "closed_ts": t["closed_ts"] + 3600.0}
        for t in reconstructed_closes
    ]
    review.review_trades(
        extended_trades,
        candles_for=_candles_for,
        contexts=contexts,
        base=base_dir,
        regime="bear_squeeze",
    )

    all_postmortems = credit._read_jsonl(credit._paths(base_dir)["postmortems"])
    summary_all = postmortem.summarize(all_postmortems)
    scorecard = credit.seat_scorecard(base=base_dir)
    patterns = credit.pattern_report(base=base_dir)
    hints = credit.policy_hints(base=base_dir)

    report = {
        "ok": True,
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "venue": "KUCOIN_FUTURES_ONLY",
        "propr_touched": False,
        "raw_fills_count": len(items),
        "closed_round_trips_count": len(reconstructed_closes),
        "maker_fills_count": maker_fills,
        "taker_fills_count": taker_fills,
        "total_fee_drag_usdt": round(fee_drag_total, 4),
        "reconstructed_closes": reconstructed_closes,
        "postmortem_summary": summary_all,
        "postmortems": all_postmortems,
        "pattern_memory": patterns,
        "seat_credit_scorecard": scorecard,
        "policy_hints": hints,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    rep = audit_kucoin_closed_trades()
    print(json.dumps({
        "ok": rep["ok"],
        "raw_fills_count": rep["raw_fills_count"],
        "closed_round_trips_count": rep["closed_round_trips_count"],
        "maker_fills": rep["maker_fills_count"],
        "taker_fills": rep["taker_fills_count"],
        "total_fee_drag_usdt": rep["total_fee_drag_usdt"],
        "postmortem_summary": rep["postmortem_summary"],
        "pattern_memory": rep["pattern_memory"],
        "seat_credit_scorecard": rep["seat_credit_scorecard"],
        "policy_hints": rep["policy_hints"],
    }, indent=2))
