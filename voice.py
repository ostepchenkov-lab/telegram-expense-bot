import os
import json
import logging
import tempfile

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.transcriber import transcribe_audio
from services.parser import parse_expense

logger = logging.getLogger(__name__)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming voice messages: transcribes, parses, and shows confirmation."""
    message = update.message
    await message.reply_text("🎙️ Отримав голосове. Розпізнаю...")

    # Download voice file
    voice = message.voice
    voice_file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await voice_file.download_to_drive(tmp_path)

        # Transcribe
        await message.reply_text("📝 Транскрибую...")
        text = transcribe_audio(tmp_path)
        logger.info(f"Transcribed: {text}")

        # Parse
        expense = parse_expense(text)
        logger.info(f"Parsed expense: {expense}")

        if expense.get("amount") is None:
            await message.reply_text(
                "❌ Не вдалося визначити суму витрати. Спробуй ще раз, назвавши конкретну суму."
            )
            return

        # Store expense in context for later confirmation
        context.user_data["pending_expense"] = expense
        context.user_data["original_text"] = text

        # Build confirmation message
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
