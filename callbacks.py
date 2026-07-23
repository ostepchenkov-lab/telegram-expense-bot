import logging

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.sheets import append_expense

logger = logging.getLogger(__name__)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline button presses: confirm or cancel expense."""
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

            # Clear pending data
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
