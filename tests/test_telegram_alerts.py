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
