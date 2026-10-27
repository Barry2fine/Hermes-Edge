import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor")
if os.path.exists(VENDOR) and VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sovereign_bots.core.telegram_alerts import (
    DIVIDER,
    format_bracket_card,
    format_kucoin_cycle_card,
    format_postmortem_card,
    format_trade_entry_card,
    format_veto_card,
    upgrade_raw_alert,
)


class TestInstitutionalTelegramAlerts(unittest.TestCase):
    def test_upgrade_live_trade_entry(self):
        raw = "🚀 [LIVE] BUY SOL 4.5 | entry~185.2 sl=181.0 tp=193.6 | risk=$18.90 mult=1.00 fee=$0.65"
        card = upgrade_raw_alert(raw)
        self.assertIn("<b>SOVEREIGN AI • PROPR EXECUTION DESK</b>", card)
        self.assertIn("🟢 LIVE ORDER EXECUTED", card)
        self.assertIn("<code>SOL</code>", card)
        self.assertIn("<code>2.00R</code>", card)
        self.assertIn("<code>coin_sol</code>", card)
        self.assertIn(DIVIDER, card)

    def test_upgrade_doubt_and_committee_veto(self):
        raw_doubt = "🛑 [Doubt] VETO INIT: Why this could fail: choppy_er:0.23; 24h $2.8M < $5M"
        card = upgrade_raw_alert(raw_doubt)
        self.assertIn("<b>SOVEREIGN AI • ADVERSARIAL DOUBT ENGINE</b>", card)
        self.assertIn("🛑 <b>CAPITAL PRESERVATION VETO</b> — <code>INIT</code>", card)
        self.assertIn("choppy_er:0.23", card)
        self.assertIn("24h $2.8M &lt; $5M", card)

        raw_bots = "🛑 [Bots] VETO ETH [VETO]: vpin_toxicity:0.74; funding_crowded"
        card_bots = upgrade_raw_alert(raw_bots)
        self.assertIn("50-Seat Crypto + Coin Specialist Committee (coin_eth)", card_bots)
        self.assertIn("vpin_toxicity:0.74", card_bots)

    def test_upgrade_bracket_and_postmortem(self):
        raw_br = "🎯 [Bracket] SOL protect: sl=185.5 tp=194.0 (lock_be,trail_atr)"
        card_br = upgrade_raw_alert(raw_br)
        self.assertIn("POSITION MANAGEMENT: PROTECT", card_br)
        self.assertIn("lock_be, trail_atr", card_br)

        raw_pm = "🔎 [Postmortem] graded 3 close(s) | smart_rate=0.67 top=clean_trend_follow"
        card_pm = upgrade_raw_alert(raw_pm)
        self.assertIn("POST-TRADE MFE/MAE AUDIT", card_pm)
        self.assertIn("0.67", card_pm)

    def test_kucoin_cycle_card(self):
        summary = {
            "committee_crypto_seats": 50,
            "futures_equity_usdt": 194.62,
            "futures_unrealised_pnl_usdt": 0.7205,
            "futures_available_margin_usdt": 115.44,
            "active_limit_orders": 2,
            "active_stop_orders": 2,
            "open_futures_positions": [
                {
                    "symbol": "ETHUSDTM",
                    "coin_specialist_bot": "coin_eth",
                    "side": "SHORT",
                    "qty_lots": -5,
                    "entry_px": 2359.08,
                    "mark_px": 2350.10,
                    "unrealised_pnl_usdt": 0.449,
                    "has_server_mp_stop": True,
                    "has_maker_tp": True,
                }
            ],
            "spot_trade_balances": [
                {"currency": "SOL", "balance": 1.4037, "available": 0.0007, "holds_in_orders": 1.403}
            ],
            "orphan_stops_cleaned": [],
        }
        card = format_kucoin_cycle_card(summary)
        self.assertIn("HERMES-EDGE • KUCOIN MULTI-SERVICE DESK", card)
        self.assertIn("$194.62 USDT", card)
        self.assertIn("ETHUSDTM", card)
        self.assertIn("🛡SL✅ 🎯TP✅", card)
        self.assertIn("coin_eth", card)


if __name__ == "__main__":
    unittest.main()


def test_format_polymarket_and_sports_cards():
    from sovereign_bots.core.telegram_alerts import (
        format_polymarket_alert_card,
        format_sports_alert_card,
    )

    poly_msg = format_polymarket_alert_card(
        markets=[
            {
                "question": "Will Bitcoin reach $100k in 2026?",
                "yes_prob": 0.64,
                "volume_24h": 425000,
                "liquidity": 180000,
                "category_bot_id": "poly_cat_crypto_prices",
                "decision": "ALLOW",
                "edge_bps": 35.0,
            }
        ],
        total_scanned=40,
    )
    assert "POLYMARKET" in poly_msg
    assert "Will Bitcoin reach $100k" in poly_msg
    assert "poly_cat_crypto_prices" in poly_msg

    sports_msg = format_sports_alert_card(
        edges=[
            {
                "matchup": "[NBA] NYK @ BOS",
                "pick": "BOS -4.5 | O/U 224.5",
                "book": "bet365 ON",
                "decimal_odds": 1.91,
                "american_odds": "-110",
                "fair_prob": 0.545,
                "ev_pct": 4.10,
                "quarter_kelly_units": 0.55,
            }
        ],
        total_events_scanned=14,
        parlay_summary={
            "legs_count": 6,
            "combined_decimal_odds": 24.85,
            "combined_fair_prob": 0.048,
            "combined_ev_pct": 19.28,
        },
    )
    assert "BET365 ONTARIO" in sports_msg
    assert "NYK @ BOS" in sports_msg
    assert "Copula SGP / Parlay Ticket" in sports_msg
