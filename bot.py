import asyncio
import os
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from handlers.voice import handle_voice
from handlers.callbacks import handle_callback

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Telegram Expense Bot is running!")

    def log_message(self, format, *args):
        pass  # Suppress HTTP access logging


def start_dummy_health_server():
    """Starts a lightweight HTTP server if PORT environment variable is present (for Render Web Services)."""
    port_str = os.getenv("PORT")
    if port_str:
        port = int(port_str)
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"🌐 Health check HTTP server started on port {port}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привіт! Я бот для обліку витрат.\n\n"
        "Надішли мені 🎙️ *голосове повідомлення* з описом витрати, наприклад:\n"
        "_«Витратив 250 гривень на каву з клієнтом»_\n\n"
        "Я розпізнаю текст, визначу суму та категорію, і запитаю підтвердження перед записом у таблицю.",
        parse_mode="Markdown",
    )


async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Привітання та інструкція"),
    ])


async def run_bot() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не знайдено у .env файлі")

    # Start health check server if PORT is defined (e.g. Render Web Service)
    start_dummy_health_server()

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("🤖 Бот запущено. Очікую повідомлення...")

    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        # Run until interrupted
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Бот зупинено.")
