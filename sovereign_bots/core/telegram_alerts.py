"""
Institutional Executive Telegram Alert & Formatting Engine (`telegram_alerts.py`).

Upgrades all outgoing notifications across:
  1. SovereignAI (Propr / Hyperliquid 50-Seat Crypto Committee + 1,492 Coin Specialists)
  2. Hermes-Edge (KuCoin Futures & Spot OCO Multi-Service Desk)
  3. Prop Bet Edge (24-Seat bet365 Ontario Sports Committee)
  4. Polymarket CLOB & Gamma Desk (29-Seat + 518-Tag Category Specialists)

Features:
  - Clean institutional HTML card layout (`parse_mode="HTML"`) with automatic plain-text fallback
  - Automatic `upgrade_raw_alert(text)` parser that converts legacy one-liner notifications
    (`🚀 [LIVE] ...`, `🛑 [Doubt] VETO ...`, `🛑 [Bots] VETO ...`, `🎯 [Bracket] ...`,
    `🔎 [Postmortem] ...`, `🧠 [Learn] ...`, `🛑 KILL SWITCH ...`, `[Momentum] WAIT ...`)
    into structured executive cards with America/Toronto (EDT/EST) + UTC timestamps
  - Rich structured builders for Trade Entries, Bracket Ratchets, Committee Vetoes,
    Post-Mortem MFE/MAE Grading, KuCoin Multi-Service Portfolio Digests, and Sports/Polymarket Cards
"""
from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

TORONTO_TZ = ZoneInfo("America/Toronto")
DIVIDER = "──────────────────────────────"


def now_toronto_str() -> str:
    """Return current timestamp formatted in America/Toronto (EDT/EST) and UTC."""
    now_utc = datetime.now(timezone.utc)
    now_tor = now_utc.astimezone(TORONTO_TZ)
    tz_label = now_tor.strftime("%Z") or "EDT"
    return f"{now_tor.strftime('%Y-%m-%d %H:%M:%S')} {tz_label} ({now_utc.strftime('%H:%M:%S')} UTC)"


def _esc(val: Any) -> str:
    return html.escape(str(val if val is not None else "N/A"))


def _is_num(val: Any) -> bool:
    try:
        float(val)
        return True
    except Exception:
        return False


def _fmt_px(val: Any) -> str:
    try:
        num = float(val)
        if num >= 100:
            return f"${num:,.2f}"
        if num >= 1:
            return f"${num:,.4f}"
        return f"${num:.6g}"
    except Exception:
        return _esc(val)


def format_trade_entry_card(
    *,
    desk: str = "SOVEREIGN AI • PROPR DESK",
    mode: str = "LIVE",
    symbol: str,
    side: str,
    size: Any,
    entry_px: Any,
    stop_px: Any,
    tp_px: Any,
    risk_usd: Optional[Any] = None,
    multiplier: Optional[Any] = None,
    fee_usd: Optional[Any] = None,
    regime: Optional[str] = None,
    confidence: Optional[Any] = None,
    coin_bot_id: Optional[str] = None,
    committee_summary: Optional[str] = None,
) -> str:
    """Format an institutional trade execution card in Telegram HTML."""
    mode_up = str(mode).upper()
    badge = "🟢 LIVE ORDER EXECUTED" if mode_up == "LIVE" else "🧪 SIMULATED ORDER"
    dir_badge = "📈 LONG (BUY)" if str(side).upper() in ("BUY", "LONG") else "📉 SHORT (SELL)"

    rr_str = None
    try:
        ep, sp, tp = float(entry_px), float(stop_px), float(tp_px)
        risk_d = abs(ep - sp)
        rew_d = abs(tp - ep)
        if risk_d > 1e-12:
            rr_str = f"{rew_d / risk_d:.2f}R"
    except Exception:
        rr_str = None

    lines = [
        f"🏛 <b>{_esc(desk)}</b>",
        f"{badge} — <code>{_esc(symbol)}</code>",
        DIVIDER,
        f"• <b>Direction:</b> {dir_badge}",
        f"• <b>Instrument / Size:</b> <code>{_esc(symbol)}</code> • <code>{_esc(size)}</code>",
        f"• <b>Entry Price:</b> <code>{_fmt_px(entry_px)}</code>",
        f"• <b>Structural Stop (SL):</b> <code>{_fmt_px(stop_px)}</code>",
        f"• <b>Take-Profit (TP):</b> <code>{_fmt_px(tp_px)}</code>" + (f" (<code>{rr_str}</code>)" if rr_str else ""),
    ]
    if risk_usd is not None or fee_usd is not None or multiplier is not None:
        parts = []
        if risk_usd is not None:
            parts.append(f"Risk ${float(risk_usd):.2f}" if _is_num(risk_usd) else f"Risk {_esc(risk_usd)}")
        if multiplier is not None:
            parts.append(f"Kelly/Mult {float(multiplier):.2f}x" if _is_num(multiplier) else f"Mult {_esc(multiplier)}")
        if fee_usd is not None:
            parts.append(f"RT Fee ${float(fee_usd):.2f}" if _is_num(fee_usd) else f"Fee {_esc(fee_usd)}")
        lines.append(f"• <b>Risk &amp; Friction:</b> <code>{' | '.join(parts)}</code>")
    if regime or confidence is not None:
        r_parts = []
        if regime:
            r_parts.append(f"Regime: {_esc(regime)}")
        if confidence is not None:
            r_parts.append(f"Conf: {_esc(confidence)}")
        lines.append(f"• <b>Regime / Conviction:</b> <code>{' | '.join(r_parts)}</code>")
    if coin_bot_id:
        lines.append(f"• <b>Coin Specialist Seat:</b> <code>{_esc(coin_bot_id)}</code>")
    if committee_summary:
        lines.append(f"• <b>Committee Gate:</b> <code>{_esc(committee_summary)}</code>")
    lines.extend([
        DIVIDER,
        f"⏱ <i>{now_toronto_str()}</i>",
    ])
    return "\n".join(lines)


def format_veto_card(
    *,
    desk: str = "SOVEREIGN AI • 103-SEAT RISK COMMITTEE",
    gate_layer: str,
    symbol: str,
    decision: str = "VETO",
    reasons: Sequence[str],
) -> str:
    """Format an institutional hard-veto / adversarial doubt card."""
    clean_reasons = [r.strip() for r in reasons if str(r).strip()]
    if not clean_reasons:
        clean_reasons = ["Failed institutional threshold"]

    reason_bullets = "\n".join(f"  ▫️ <code>{_esc(r)}</code>" for r in clean_reasons[:6])
    lines = [
        f"🏛 <b>{_esc(desk)}</b>",
        f"🛑 <b>CAPITAL PRESERVATION VETO</b> — <code>{_esc(symbol)}</code>",
        DIVIDER,
        f"• <b>Veto Authority:</b> <code>{_esc(gate_layer)}</code>",
        f"• <b>Verdict:</b> <code>{_esc(decision)} (0 bps capital risked)</code>",
        f"• <b>Disqualifying Factors:</b>\n{reason_bullets}",
        DIVIDER,
        f"⏱ <i>{now_toronto_str()}</i>",
    ]
    return "\n".join(lines)


def format_bracket_card(
    *,
    desk: str = "SOVEREIGN AI • BRACKET GOVERNOR",
    symbol: str,
    action: str,
    sl: Any = None,
    tp: Any = None,
    reasons: Optional[Sequence[str]] = None,
    extra: Optional[str] = None,
) -> str:
    """Format a bracket ratchet / take-profit / time-stop management card."""
    act_up = str(action).upper()
    icon = "🎯" if "PROTECT" in act_up or "ADJUST" in act_up else ("✅" if "PROFIT" in act_up else "⏱")
    lines = [
        f"🏛 <b>{_esc(desk)}</b>",
        f"{icon} <b>POSITION MANAGEMENT: {_esc(act_up)}</b> — <code>{_esc(symbol)}</code>",
        DIVIDER,
    ]
    if sl is not None or tp is not None:
        lines.append(f"• <b>Updated Stop-Loss:</b> <code>{_fmt_px(sl)}</code>")
        lines.append(f"• <b>Updated Take-Profit:</b> <code>{_fmt_px(tp)}</code>")
    if reasons:
        lines.append(f"• <b>Ratchet Trigger:</b> <code>{_esc(', '.join(str(x) for x in reasons))}</code>")
    if extra:
        lines.append(f"• <b>Execution Detail:</b> <code>{_esc(extra)}</code>")
    lines.extend([
        DIVIDER,
        f"⏱ <i>{now_toronto_str()}</i>",
    ])
    return "\n".join(lines)


def format_postmortem_card(
    *,
    desk: str = "SOVEREIGN AI • EDGE JOURNAL & BAYESIAN LOOP",
    headline: str,
    metrics: Dict[str, Any],
    notes: Optional[Sequence[str]] = None,
) -> str:
    """Format a post-trade MFE/MAE audit and Bayesian weight update card."""
    lines = [
        f"🏛 <b>{_esc(desk)}</b>",
        f"🧠 <b>{_esc(headline)}</b>",
        DIVIDER,
    ]
    for k, v in metrics.items():
        lines.append(f"• <b>{_esc(k)}:</b> <code>{_esc(v)}</code>")
    if notes:
        for n in notes[:4]:
            lines.append(f"  💡 <i>{_esc(n)}</i>")
    lines.extend([
        DIVIDER,
        f"⏱ <i>{now_toronto_str()}</i>",
    ])
    return "\n".join(lines)


def format_kucoin_cycle_card(summary: Dict[str, Any]) -> str:
    """Format a comprehensive KuCoin Futures + Spot OCO Executive Portfolio Card."""
    eq = float(summary.get("futures_equity_usdt") or 0.0)
    upnl = float(summary.get("futures_unrealised_pnl_usdt") or 0.0)
    avail = float(summary.get("futures_available_margin_usdt") or 0.0)
    positions = summary.get("open_futures_positions") or []
    spot_bals = summary.get("spot_trade_balances") or []
    seats = summary.get("committee_crypto_seats") or 50

    upnl_str = f"+${upnl:,.4f}" if upnl >= 0 else f"-${abs(upnl):,.4f}"
    lines = [
        "⚡ <b>HERMES-EDGE • KUCOIN MULTI-SERVICE DESK</b>",
        f"📊 <b>PORTFOLIO &amp; BRACKET AUDIT ({seats}-SEAT + 1,492-COIN)</b>",
        DIVIDER,
        f"• <b>Futures Equity:</b> <code>${eq:,.2f} USDT</code> (uPnL: <code>{upnl_str} USDT</code>)",
        f"• <b>Available Margin:</b> <code>${avail:,.2f} USDT</code>",
        f"• <b>Active Orders:</b> <code>{summary.get('active_limit_orders', 0)} Limit</code> | <code>{summary.get('active_stop_orders', 0)} Server Stops</code>",
    ]
    if positions:
        lines.append("• <b>Open Futures Positions:</b>")
        for p in positions:
            sym = p.get("symbol", "?")
            side = p.get("side", "?")
            lots = p.get("qty_lots", 0)
            ep = p.get("entry_px", 0.0)
            mp = p.get("mark_px", 0.0)
            pnl = float(p.get("unrealised_pnl_usdt") or 0.0)
            pnl_s = f"+${pnl:.3f}" if pnl >= 0 else f"-${abs(pnl):.3f}"
            sl_ok = "🛡SL✅" if p.get("has_server_mp_stop") else "⚠️SL❌"
            tp_ok = "🎯TP✅" if p.get("has_maker_tp") else "⚠️TP❌"
            bot = p.get("coin_specialist_bot", "coin_bot")
            lines.append(
                f"  ▫️ <code>{_esc(sym)}</code> <b>{_esc(side)}</b> ({lots}L @ <code>{_fmt_px(ep)}</code> → <code>{_fmt_px(mp)}</code>) "
                f"| <code>{pnl_s}</code> | {sl_ok} {tp_ok} [<code>{_esc(bot)}</code>]"
            )
    if spot_bals:
        spot_items = [
            f"{b.get('currency')}: {float(b.get('balance') or 0):.4g} (in orders: {float(b.get('holds_in_orders') or 0):.4g})"
            for b in spot_bals
        ]
        lines.append(f"• <b>Spot Balances:</b> <code>{_esc(' | '.join(spot_items))}</code>")
    cleaned = summary.get("orphan_stops_cleaned") or []
    if cleaned:
        lines.append(f"• <b>Orphan Stops Swept:</b> <code>{len(cleaned)} cleaned</code>")
    lines.extend([
        DIVIDER,
        f"⏱ <i>{now_toronto_str()}</i>",
    ])
    return "\n".join(lines)


def upgrade_raw_alert(text: str, default_desk: str = "SOVEREIGN AI • QUANTITATIVE DESK") -> str:
    """
    Automatically upgrade any single-line or legacy plain-text alert from
    `sovereign_engine.py` or `kucoin_github_cycle.py` into a structured,
    institutional Telegram HTML executive card.
    """
    if not text or not str(text).strip():
        return ""
    raw = str(text).strip()
    if "<b>" in raw and DIVIDER in raw:
        return raw

    # 1. Momentum / Wraith Order Execution:
    m_exec = re.match(
        r"^🚀\s*\[(?P<mode>[^\]]+)\]\s*(?P<side>BUY|SELL|LONG|SHORT)\s+(?P<rest>.+)$",
        raw,
        re.IGNORECASE,
    )
    if m_exec:
        mode = m_exec.group("mode").strip()
        side = m_exec.group("side").upper()
        rest = m_exec.group("rest").strip()
        parts = [p.strip() for p in rest.split("|")]
        head_tokens = parts[0].split()
        if len(head_tokens) >= 2:
            if _is_num(head_tokens[0]):
                size, sym = head_tokens[0], head_tokens[1]
            else:
                sym, size = head_tokens[0], head_tokens[1]
        else:
            sym, size = head_tokens[0], "1"
        kv: Dict[str, str] = {}
        for seg in parts[1:]:
            for tok in seg.split():
                if "~" in tok:
                    k, v = tok.split("~", 1)
                    kv[k.strip()] = v.strip()
                elif "=" in tok:
                    k, v = tok.split("=", 1)
                    kv[k.strip()] = v.strip()
        return format_trade_entry_card(
            desk="SOVEREIGN AI • PROPR EXECUTION DESK",
            mode=mode,
            symbol=sym,
            side=side,
            size=size,
            entry_px=kv.get("entry", "N/A"),
            stop_px=kv.get("sl", "N/A"),
            tp_px=kv.get("tp", "N/A"),
            risk_usd=kv.get("risk", "").lstrip("$") or None,
            multiplier=kv.get("mult") or None,
            fee_usd=kv.get("fee", "").lstrip("$") or None,
            regime=kv.get("regime"),
            confidence=kv.get("conf"),
            coin_bot_id=f"coin_{sym.lower()}",
        )

    # 2. Adversarial Doubt Veto:
    m_doubt = re.match(r"^🛑\s*\[Doubt\]\s*VETO\s+(?P<sym>[A-Za-z0-9_-]+):\s*(?P<why>.+)$", raw)
    if m_doubt:
        sym = m_doubt.group("sym").upper()
        why = m_doubt.group("why").replace("Why this could fail:", "").strip()
        reasons = [x.strip() for x in re.split(r"[;|]", why) if x.strip()]
        return format_veto_card(
            desk="SOVEREIGN AI • ADVERSARIAL DOUBT ENGINE",
            gate_layer="Adversarial Pre-Trade Falsification Gate",
            symbol=sym,
            decision="HARD VETO",
            reasons=reasons,
        )

    # 3. 103-Seat Bot Committee Veto:
    m_bots = re.match(
        r"^🛑\s*\[Bots\]\s*VETO\s+(?P<sym>[A-Za-z0-9_-]+)\s*\[(?P<dec>[^\]]+)\]:\s*(?P<why>.+)$",
        raw,
    )
    if m_bots:
        sym = m_bots.group("sym").upper()
        dec = m_bots.group("dec").upper()
        reasons = [x.strip() for x in m_bots.group("why").split(";") if x.strip()]
        return format_veto_card(
            desk="SOVEREIGN AI • 103-SEAT + 1,492-COIN COMMITTEE",
            gate_layer=f"50-Seat Crypto + Coin Specialist Committee (coin_{sym.lower()})",
            symbol=sym,
            decision=dec,
            reasons=reasons,
        )

    # 4. Execution / Gate Veto:
    m_gate = re.match(
        r"^\[Momentum\]\s*(?P<kind>EXEC VETO|GATE VETO)\s+(?P<sym>[A-Za-z0-9_-]+):\s*(?P<why>.+)$",
        raw,
    )
    if m_gate:
        sym = m_gate.group("sym").upper()
        kind = m_gate.group("kind").upper()
        reasons = [x.strip() for x in re.split(r"[;,]", m_gate.group("why")) if x.strip()]
        return format_veto_card(
            desk="SOVEREIGN AI • MICROSTRUCTURE & FEE GATE",
            gate_layer="L2 Depth & Net R:R Admission Gate" if "GATE" in kind else "L2 Orderbook Slippage Gate",
            symbol=sym,
            decision=kind,
            reasons=reasons,
        )

    # 5. Bracket Ratchet / Adjust / Protect:
    m_br = re.match(
        r"^🎯\s*\[Bracket\]\s*(?P<sym>[A-Za-z0-9_-]+)\s+(?P<act>[a-zA-Z_]+):\s*sl=(?P<sl>[^\s]+)\s+tp=(?P<tp>[^\s]+)\s*(?:\((?P<reasons>[^)]*)\))?",
        raw,
    )
    if m_br:
        r_list = [x.strip() for x in (m_br.group("reasons") or "").split(",") if x.strip()]
        return format_bracket_card(
            symbol=m_br.group("sym").upper(),
            action=m_br.group("act").upper(),
            sl=m_br.group("sl"),
            tp=m_br.group("tp"),
            reasons=r_list,
        )

    # 6. Take Profit / Time Stop:
    if raw.startswith("✅ TAKE PROFIT:") or raw.startswith("⏱ TIME STOP:"):
        kind = "TAKE PROFIT LOCKED" if raw.startswith("✅") else "TIME-DECAY STOP EXIT"
        detail = raw.split(":", 1)[1].strip() if ":" in raw else raw
        return format_bracket_card(
            symbol="OPEN POSITION",
            action=kind,
            extra=detail,
        )

    # 7. Postmortem & Adaptive Learning:
    if raw.startswith("🔎 [Postmortem]") or raw.startswith("🧠 [Learn]") or raw.startswith("💡 [Learn]"):
        body = re.sub(r"^(🔎\s*\[Postmortem\]|🧠\s*\[Learn\]|💡\s*\[Learn\])\s*", "", raw).strip()
        segs = [s.strip() for s in body.split("|") if s.strip()]
        metrics: Dict[str, Any] = {"Summary": segs[0] if segs else body}
        for seg in segs[1:]:
            for tok in seg.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    metrics[k.strip()] = v.strip()
        return format_postmortem_card(
            headline="POST-TRADE MFE/MAE AUDIT & BAYESIAN TUNING",
            metrics=metrics,
        )

    # 8. Kill Switch / Circuit Breaker / Protection Failure / Order Failure:
    if any(raw.startswith(p) for p in ("🛑", "❌", "⚠️", "⛔")):
        icon = raw[0]
        lines = [
            f"🏛 <b>{_esc(default_desk)}</b>",
            f"{icon} <b>RISK &amp; EXECUTION ALERT</b>",
            DIVIDER,
            f"• <b>Event:</b> <code>{_esc(raw)}</code>",
            DIVIDER,
            f"⏱ <i>{now_toronto_str()}</i>",
        ]
        return "\n".join(lines)

    # 9. Default clean institutional wrapper for status/wait/info messages:
    lines = [
        f"🏛 <b>{_esc(default_desk)}</b>",
        "📡 <b>DESK TELEMETRY UPDATE</b>",
        DIVIDER,
        f"• <b>Detail:</b> <code>{_esc(raw)}</code>",
        DIVIDER,
        f"⏱ <i>{now_toronto_str()}</i>",
    ]
    return "\n".join(lines)


def send_institutional_telegram(
    text: str,
    *,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    desk: str = "SOVEREIGN AI • QUANTITATIVE DESK",
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Format `text` as an institutional HTML card and deliver it via the Telegram Bot API.
    Automatically falls back to plain text if Telegram rejects any HTML entity.
    Never raises or leaks credentials.
    """
    tok = (token or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    cid = (chat_id or os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT") or "").strip()
    formatted = upgrade_raw_alert(text, default_desk=desk)

    if not tok or not cid:
        print(f"[Telegram-Fallback]\n{formatted}")
        return {"ok": False, "simulated": True, "reason": "missing_telegram_credentials", "formatted": formatted}

    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": cid,
        "text": formatted,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
            return {"ok": resp.status == 200 and bool(body.get("ok")), "status": resp.status, "formatted": formatted}
    except Exception as first_err:
        try:
            plain_payload = urllib.parse.urlencode({
                "chat_id": cid,
                "text": text,
                "disable_web_page_preview": "true",
            }).encode("utf-8")
            req2 = urllib.request.Request(url, data=plain_payload, method="POST")
            with urllib.request.urlopen(req2, timeout=timeout) as resp2:
                return {"ok": resp2.status == 200, "status": resp2.status, "fallback_plain": True}
        except Exception as second_err:
            print(f"[Telegram-Fallback] {text} (send failed: {type(first_err).__name__}/{type(second_err).__name__})")
            return {"ok": False, "error": type(second_err).__name__, "formatted": formatted}
