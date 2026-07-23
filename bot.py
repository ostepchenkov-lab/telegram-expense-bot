import asyncio
import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import gspread
from google.oauth2.service_account import Credentials
from groq import Groq
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ----------------------------------------------------
# 1. Configuration & Clients Setup
# ----------------------------------------------------
load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CATEGORIES = ["Ворк", "Реактор"]

SYSTEM_PROMPT = f"""Ти асистент для обліку витрат. Твоя задача — розпарсити текст і повернути JSON з такими полями:
- amount: число (float), сума витрат
- currency: рядок, валюта (за замовчуванням "UAH")
- description: рядок, коротка суть витрати (до 60 символів)
- category: одне з двох значень: {json.dumps(CATEGORIES, ensure_ascii=False)}

Правила вибору категорії:
- "Ворк" — все що стосується роботи, послуг, документів, зарплат, сервісів, логістики, допоміжних витрат, або коли явно вказано "ворк", "work", "робота".
- "Реактор" — все що стосується проектів чи об'єктів "Реактор", поповнення каси реактора, оренди реактора, або коли явно вказано "реактор", "reactor".

Якщо з тексту складно визначити категорію — обери найбільш відповідне із двох ("Ворк" або "Реактор").

Якщо не можеш визначити суму — поверни null для amount.

Поверни ТІЛЬКИ валідний JSON без додаткового тексту. Приклад:
{{"amount": 250.0, "currency": "UAH", "description": "Кава з клієнтом", "category": "Ворк"}}
"""

# ----------------------------------------------------
# 2. Services (Transcriber, Parser, Sheets)
# ----------------------------------------------------
def transcribe_audio(file_path: str) -> str:
    """Transcribes audio using Groq Whisper API."""
    with open(file_path, "rb") as audio_file:
        transcript = groq_client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=audio_file,
            language="uk",
            response_format="text",
        )
    return transcript


def parse_expense(text: str) -> dict:
    """Parses expense text into structured fields using Groq LLM."""
    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)
    data["date"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not data.get("currency"):
        data["currency"] = "UAH"

    if data.get("category") not in CATEGORIES:
        data["category"] = "Ворк"

    return data


def _get_gspread_client() -> gspread.Client:
    """Returns authenticated gspread client from env string or file."""
    creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json_str:
        info = json.loads(creds_json_str)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "groshi-test-503314-4826b51946cd.json")
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    return gspread.authorize(creds)


def append_expense(expense: dict) -> str:
    """Appends an expense row to the Google Spreadsheet."""
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    gc = _get_gspread_client()
    spreadsheet = gc.open_by_key(spreadsheet_id)
    sheet = spreadsheet.sheet1

    row = [
        expense.get("date", ""),
        expense.get("amount", ""),
        expense.get("currency", "UAH"),
        expense.get("description", ""),
        expense.get("category", "Ворк"),
    ]
    sheet.append_row(row, value_input_option="USER_ENTERED")
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


# ----------------------------------------------------
# 3. Telegram Handlers
# ----------------------------------------------------
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming voice messages."""
    message = update.message
    await message.reply_text("🎙️ Отримав голосове. Розпізнаю...")

    voice = message.voice
    voice_file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await voice_file.download_to_drive(tmp_path)

        await message.reply_text("📝 Транскрибую...")
        text = transcribe_audio(tmp_path)
        logger.info(f"Transcribed: {text}")

        expense = parse_expense(text)
        logger.info(f"Parsed expense: {expense}")

        if expense.get("amount") is None:
            await message.reply_text(
                "❌ Не вдалося визначити суму витрати. Спробуй ще раз, назвавши конкретну суму."
            )
            return

        context.user_data["pending_expense"] = expense
        context.user_data["original_text"] = text

        category_emoji = {
            "Ворк": "💼",
            "Реактор": "⚛️",
        }
        emoji = category_emoji.get(expense["category"], "📌")

        confirmation_text = (
            f"📋 *Розпізнані дані:*\n\n"
            f"📅 Дата: `{expense['date']}`\n"
            f"💰 Сума: `{expense['amount']} {expense['currency']}`\n"
            f"📝 Опис: `{expense['description']}`\n"
            f"{emoji} Категорія: `{expense['category']}`\n\n"
            f"_Текст: «{text}»_\n\n"
            f"Зберегти цю витрату?"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Підтвердити", callback_data="confirm_expense"),
                InlineKeyboardButton("❌ Скасувати", callback_data="cancel_expense"),
            ]
        ])

        await message.reply_text(
            confirmation_text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(f"Error processing voice: {e}", exc_info=True)
        await message.reply_text(f"⚠️ Помилка при обробці: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles confirm/cancel button presses."""
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_expense":
        expense = context.user_data.get("pending_expense")
        if not expense:
            await query.edit_message_text("⚠️ Дані витрати не знайдено. Спробуй знову.")
            return

        try:
            await query.edit_message_text(
                query.message.text + "\n\n⏳ Записую у таблицю...",
                parse_mode="Markdown",
                reply_markup=None,
            )

            sheet_url = append_expense(expense)

            await query.edit_message_text(
                query.message.text.replace("⏳ Записую у таблицю...", "").rstrip()
                + f"\n\n✅ *Записано!* [Відкрити таблицю]({sheet_url})",
                parse_mode="Markdown",
                reply_markup=None,
            )

            context.user_data.pop("pending_expense", None)
            context.user_data.pop("original_text", None)

        except Exception as e:
            logger.error(f"Error writing to sheets: {e}", exc_info=True)
            await query.edit_message_text(
                f"⚠️ Помилка запису у таблицю: {e}",
                reply_markup=None,
            )

    elif query.data == "cancel_expense":
        context.user_data.pop("pending_expense", None)
        context.user_data.pop("original_text", None)

        await query.edit_message_text(
            query.message.text + "\n\n❌ *Скасовано.* Витрата не записана.",
            parse_mode="Markdown",
            reply_markup=None,
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привіт! Я бот для обліку витрат.\n\n"
        "Надішли мені 🎙️ *голосове повідомлення* з описом витрати, наприклад:\n"
        "_«Витратив 250 гривень на каву з клієнтом»_\n\n"
        "Я розпізнаю текст, визначу суму та категорію, і запитаю підтвердження перед записом у таблицю.",
        parse_mode="Markdown",
    )


# ----------------------------------------------------
# 4. HTTP Health Server (for Render Web Services)
# ----------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Telegram Expense Bot is running!")

    def log_message(self, format, *args):
        pass


def start_dummy_health_server():
    port_str = os.getenv("PORT")
    if port_str:
        port = int(port_str)
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"🌐 Health check HTTP server running on port {port}")


# ----------------------------------------------------
# 5. Main Entrypoint
# ----------------------------------------------------
async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Привітання та інструкція"),
    ])


async def run_bot() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не знайдено у .env файлі")

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
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Бот зупинено.")
