import asyncio
import json
import logging
import os
import re
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

CATEGORIES = ["в", "р"]

SYSTEM_PROMPT = f"""Ти асистент для обліку фінансів. Розпарси текст та поверни JSON:
- amount: float, чисельне значення суми.
- transaction_type: "income" (якщо дохід/прихід/заробив/отримав) або "expense" (якщо витрата/витратив/оплатив/купив/віддав).
- description: рядок, ТІЛЬКИ назва товару, послуги чи адресата (без слів "витратив", "сума", "гривень", без чисел та без вказівки категорій!).
- category: "в" або "р".

Приклади:
Текст: "Витрати в 2500 гривень. Каса Наташа."
JSON: {{"amount": 2500.0, "transaction_type": "expense", "description": "Каса Наташа", "category": "в"}}

Текст: "Отримав 5000 грн за проект категорія р"
JSON: {{"amount": 5000.0, "transaction_type": "income", "description": "За проект", "category": "р"}}
"""

def clean_description_text(desc: str) -> str:
    """Python regex cleaner that guarantees removal of unwanted words (amounts, currency, category words, 'витрати в')."""
    if not desc:
        return ""
    
    cleaned = desc
    # Remove category markers
    cleaned = re.sub(r"категорі[яі]\s*«?[врвР]»?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"категорі[яі]\s*«?(ворк|реактор)»?", "", cleaned, flags=re.IGNORECASE)
    
    # Remove expense/income prefix words
    cleaned = re.sub(r"^(витрати|витратив|витратила|оплатив|оплачено|отримав|заробив|прихід)\s*(в|на|за)?\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(витрати|витратив|витратила|оплатив|оплачено)\b", "", cleaned, flags=re.IGNORECASE)
    
    # Remove numbers and currency
    cleaned = re.sub(r"\b\d+([.,]\d+)?\b", "", cleaned)
    cleaned = re.sub(r"\b(гривень|гривні|грн|доларів|дол|usd|uah|євро)\b", "", cleaned, flags=re.IGNORECASE)
    
    # Remove leading/trailing punctuation and space
    cleaned = re.sub(r"^[.,\s\-–—:]+", "", cleaned)
    cleaned = re.sub(r"[.,\s\-–—:]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned if cleaned else desc

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
    """Parses finance text into structured fields using Groq LLM."""
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
    data["date"] = datetime.now().strftime("%d.%m.%Y")

    # Clean description with python regex safety net
    raw_desc = data.get("description", "")
    data["description"] = clean_description_text(raw_desc)

    if data.get("category") not in CATEGORIES:
        data["category"] = "в"

    if data.get("transaction_type") not in ["income", "expense"]:
        data["transaction_type"] = "expense"

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
    """Appends an income/expense row to the Google Spreadsheet without currency column."""
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    gc = _get_gspread_client()
    spreadsheet = gc.open_by_key(spreadsheet_id)
    sheet = spreadsheet.sheet1

    existing_records = sheet.get_all_values()
    next_row = len(existing_records) + 1

    amount = expense.get("amount", 0)
    is_income = expense.get("transaction_type") == "income"

    income_val = amount if is_income else ""
    expense_val = amount if not is_income else ""

    # Balance formula (Column D)
    if next_row == 2:
        balance_formula = "=B2-C2"
    else:
        balance_formula = f"=D{next_row-1}+B{next_row}-C{next_row}"

    # Row format: [Дата, Дохід, Витрати, Баланс, Опис, Категорія]
    row = [
        expense.get("date", datetime.now().strftime("%d.%m.%Y")),
        income_val,
        expense_val,
        balance_formula,
        expense.get("description", ""),
        expense.get("category", "в"),
    ]
    sheet.append_row(row, value_input_option="USER_ENTERED")
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def parse_expense_from_message_text(msg_text: str) -> dict:
    """Fallback parser: extracts expense data directly from the Telegram confirmation message text if memory is cleared after bot restart."""
    expense = {
        "date": datetime.now().strftime("%d.%m.%Y"),
        "amount": 0.0,
        "transaction_type": "expense",
        "description": "",
        "category": "в",
    }
    try:
        # Date
        date_match = re.search(r"Дата:\s*`?([\d\.]+)`?", msg_text)
        if date_match:
            expense["date"] = date_match.group(1)

        # Amount and Type
        if "Дохід:" in msg_text:
            expense["transaction_type"] = "income"
            amt_match = re.search(r"Дохід:\s*`?([\d\.]+)`?", msg_text)
            if amt_match:
                expense["amount"] = float(amt_match.group(1))
        else:
            expense["transaction_type"] = "expense"
            amt_match = re.search(r"Витрати:\s*`?([\d\.]+)`?", msg_text)
            if amt_match:
                expense["amount"] = float(amt_match.group(1))

        # Description
        desc_match = re.search(r"Опис:\s*`?([^`\n]+)`?", msg_text)
        if desc_match:
            expense["description"] = clean_description_text(desc_match.group(1).strip())

        # Category
        cat_match = re.search(r"Категорія:\s*`?([^`\n]+)`?", msg_text)
        if cat_match:
            cat_val = cat_match.group(1).strip()
            expense["category"] = "р" if cat_val in ["р", "Реактор"] else "в"

    except Exception as e:
        logger.error(f"Error fallback parsing message text: {e}")

    return expense


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
        logger.info(f"Parsed finance: {expense}")

        if expense.get("amount") is None:
            await message.reply_text(
                "❌ Не вдалося визначити суму. Спробуй ще раз, назвавши конкретну суму."
            )
            return

        context.user_data["pending_expense"] = expense
        context.user_data["original_text"] = text

        type_label = "📈 Дохід" if expense["transaction_type"] == "income" else "📉 Витрати"
        category_emoji = {"в": "💼", "р": "⚛️"}
        emoji = category_emoji.get(expense["category"], "📌")

        confirmation_text = (
            f"📋 *Розпізнані дані:*\n\n"
            f"📅 Дата: `{expense['date']}`\n"
            f"{type_label}: `{expense['amount']}`\n"
            f"📝 Опис: `{expense['description']}`\n"
            f"{emoji} Категорія: `{expense['category']}`\n\n"
            f"_Текст: «{text}»_\n\n"
            f"Зберегти цю операцію?"
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
        # 1. Try memory
        expense = context.user_data.get("pending_expense")

        # 2. Fallback: Parse directly from the message text if bot was restarted
        if not expense and query.message and query.message.text:
            logger.info("Memory empty. Falling back to parsing message text...")
            expense = parse_expense_from_message_text(query.message.text)

        if not expense or not expense.get("amount"):
            await query.edit_message_text("⚠️ Дані не знайдено. Надішли голосове ще раз.")
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
            query.message.text + "\n\n❌ *Скасовано.* Операція не записана.",
            parse_mode="Markdown",
            reply_markup=None,
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привіт! Я бот для обліку доходів та витрат.\n\n"
        "Надішли мені 🎙️ *голосове повідомлення*, наприклад:\n"
        "_«Отримав 5000 грн за проект, категорія в»_\n"
        "_«Витратив 250 грн на каву, категорія р»_\n\n"
        "Я визначу дохід чи витрату, розпізнаю суму, опис і категорію.",
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
