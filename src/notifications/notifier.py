import os
import sys
import asyncio
from typing import Optional, Callable, Any
import threading

try:
    from telegram import Bot, Update
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
except ImportError:
    Bot = None  # type: ignore[assignment]
    Update = Any  # type: ignore[assignment]
    ApplicationBuilder = None  # type: ignore[assignment]
    CommandHandler = None  # type: ignore[assignment]
    MessageHandler = None  # type: ignore[assignment]
    filters = None  # type: ignore[assignment]

    class ContextTypes:  # type: ignore[no-redef]
        DEFAULT_TYPE = Any

_VENDOR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "vendor"))
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from sovereign_bots.core.telegram_alerts import (
    upgrade_raw_alert,
    send_institutional_telegram,
)


class TelegramNotifier:
    """
    The TelegramNotifier provides bi-directional institutional communication
    between the Sovereign AI Hive and the user's mobile device.
    """
    def __init__(self, engine_callback: Optional[Callable] = None):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")
        self.bot: Optional[Any] = None
        self.app = None
        self.engine_callback = engine_callback

        if self.token and Bot is not None:
            self.bot = Bot(token=self.token)
        elif not self.token:
            print("[Notifier-Error] TELEGRAM_BOT_TOKEN not found in environment.")

    async def send_message(self, text: str):
        """Sends an asynchronous institutional HTML message to the user."""
        formatted = upgrade_raw_alert(text)
        if not self.bot or not self.chat_id:
            print(f"[Telegram-Fallback]\n{formatted}")
            return

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=formatted,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            try:
                await self.bot.send_message(chat_id=self.chat_id, text=text)
            except Exception as e:
                print(f"[Notifier-Error] Failed to send Telegram message: {e}")

    def send_sync(self, text: str):
        """Send an institutional HTML card from sync code via direct HTTPS Bot API."""
        token = os.getenv("TELEGRAM_BOT_TOKEN") or self.token or ""
        chat = os.getenv("TELEGRAM_CHAT_ID") or str(self.chat_id or "")
        send_institutional_telegram(
            text,
            token=token,
            chat_id=chat,
            desk="SOVEREIGN AI • PROPR & QUANT DESK",
        )

    # --- C2 Command Handlers ---

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.engine_callback:
            status = self.engine_callback("status")
            await update.message.reply_text(
                upgrade_raw_alert(f"📊 Sovereign AI Status | {status}", default_desk="SOVEREIGN AI • C2 COMMAND"),
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("Engine not connected.")

    async def handle_close_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.engine_callback:
            res = self.engine_callback("close_all")
            await update.message.reply_text(
                upgrade_raw_alert(f"🛑 Emergency Action | {res}", default_desk="SOVEREIGN AI • C2 COMMAND"),
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("Engine not connected.")

    async def handle_bias(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.engine_callback:
            bias = self.engine_callback("bias")
            await update.message.reply_text(
                upgrade_raw_alert(f"🎯 Current Market Bias | {bias}", default_desk="SOVEREIGN AI • C2 COMMAND"),
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("Engine not connected.")

    async def handle_generic_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles all other messages to ensure the bot is responsive."""
        user_text = update.message.text
        if self.engine_callback:
            response = self.engine_callback(f"chat:{user_text}")
            await update.message.reply_text(
                upgrade_raw_alert(str(response), default_desk="SOVEREIGN AI • C2 COMMAND"),
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("I'm online, but the trading engine is currently disconnected.")

    def start_listening(self):
        """Starts the Telegram bot poller in a separate thread with its own loop."""
        if not self.token or ApplicationBuilder is None:
            return

        def run_bot():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                self.app = ApplicationBuilder().token(self.token).build()
                
                self.app.add_handler(CommandHandler("status", self.handle_status))
                self.app.add_handler(CommandHandler("close_all", self.handle_close_all))
                self.app.add_handler(CommandHandler("bias", self.handle_bias))
                self.app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.handle_generic_message))
                
                print("### Telegram C2 Listener Active and Listening for Chat ###")
                self.app.run_polling()
            except Exception as e:
                print(f"[Notifier-Fatal] Bot poller crashed: {e}")

        thread = threading.Thread(target=run_bot, daemon=True)
        thread.start()
