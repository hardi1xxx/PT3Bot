"""
Telegram bot for updating PT3 status directly from chat.
Flow: /update -> ketik kata kunci IHLD/Lokasi -> pilih baris -> pilih status Z
      -> pilih sub status AA (opsional) -> ketik keterangan -> konfirmasi -> tersimpan.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import config
import sheets_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Conversation states
SEARCH, PICK_ROW, PICK_Z, PICK_AA, ENTER_NOTE, CONFIRM = range(6)

# How many buttons per row in inline keyboards
COLS = 1
Z_PAGE_SIZE = 13  # all Z options fit on one screen


def chunk(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! Bot update status PT3.\n\n"
        "Perintah:\n"
        "/update - cari site & update status/keterangan\n"
        "/cancel - batalkan proses yang sedang berjalan"
    )


async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ketik kata kunci IHLD atau Lokasi IHLD yang mau dicari:")
    return SEARCH


async def do_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    try:
        results = sheets_service.search_rows(query, limit=10)
    except Exception as e:
        await update.message.reply_text(f"Gagal mencari: {e}")
        return ConversationHandler.END

    if not results:
        await update.message.reply_text("Tidak ditemukan. Coba kata kunci lain, atau /cancel.")
        return SEARCH

    context.user_data["search_results"] = {str(r["row"]): r for r in results}
    buttons = [
        [InlineKeyboardButton(f"{r['ihld']} — {r['lokasi']} [{r['status_z'] or '-'}]", callback_data=str(r["row"]))]
        for r in results
    ]
    await update.message.reply_text(
        "Pilih baris yang mau diupdate:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return PICK_ROW


async def pick_row(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    row_num = int(query.data)
    context.user_data["row"] = row_num

    try:
        snapshot = sheets_service.get_row_snapshot(row_num)
    except Exception as e:
        await query.edit_message_text(f"Gagal ambil data baris: {e}")
        return ConversationHandler.END

    context.user_data["snapshot"] = snapshot
    preview = snapshot["note_preview"][:500] if snapshot["note_preview"] else "(belum ada catatan)"

    buttons = [[InlineKeyboardButton(z, callback_data=z)] for z in config.Z_OPTIONS]
    text = (
        f"Baris #{row_num}\n"
        f"Status saat ini: {snapshot['status_z'] or '-'}\n"
        f"Sub saat ini: {snapshot['status_aa'] or '-'}\n\n"
        f"Riwayat keterangan:\n{preview}\n\n"
        f"Pilih status baru (kolom Z):"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    return PICK_Z


async def pick_z(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["status_z"] = query.data

    buttons = [[InlineKeyboardButton(aa, callback_data=aa)] for aa in config.AA_OPTIONS]
    buttons.append([InlineKeyboardButton("(lewati, jangan ubah sub status)", callback_data="__skip__")])
    await query.edit_message_text(
        f"Status dipilih: {query.data}\n\nPilih sub status (kolom AA):",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return PICK_AA


async def pick_aa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["status_aa"] = "" if query.data == "__skip__" else query.data

    await query.edit_message_text(
        f"Sub status: {context.user_data['status_aa'] or '(tidak diubah)'}\n\n"
        f"Ketik keterangan updatenya (contoh: penarikan kabel 80%):"
    )
    return ENTER_NOTE


async def enter_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note_text = update.message.text.strip()
    context.user_data["note_text"] = note_text

    d = context.user_data
    summary = (
        f"Konfirmasi update:\n\n"
        f"Baris: #{d['row']}\n"
        f"Status (Z): {d['status_z']}\n"
        f"Sub (AA): {d['status_aa'] or '(tidak diubah)'}\n"
        f"Keterangan: {note_text}\n\n"
        f"Simpan?"
    )
    buttons = [[
        InlineKeyboardButton("Ya, simpan", callback_data="yes"),
        InlineKeyboardButton("Batal", callback_data="no"),
    ]]
    await update.message.reply_text(summary, reply_markup=InlineKeyboardMarkup(buttons))
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data != "yes":
        await query.edit_message_text("Dibatalkan.")
        context.user_data.clear()
        return ConversationHandler.END

    d = context.user_data
    try:
        date_col, note_col = sheets_service.update_status(
            d["row"], d["status_z"], d["status_aa"], d["note_text"]
        )
        await query.edit_message_text(
            f"Tersimpan. Kolom {note_col} diupdate, tanggal dicatat di {date_col}."
        )
    except Exception as e:
        await query.edit_message_text(f"Gagal menyimpan: {e}")

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Dibatalkan.")
    return ConversationHandler.END


def build_app() -> Application:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var is not set.")

    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("update", cmd_update)],
        states={
            SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, do_search)],
            PICK_ROW: [CallbackQueryHandler(pick_row)],
            PICK_Z: [CallbackQueryHandler(pick_z)],
            PICK_AA: [CallbackQueryHandler(pick_aa)],
            ENTER_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_note)],
            CONFIRM: [CallbackQueryHandler(confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(conv)
    return application


if __name__ == "__main__":
    app = build_app()
    logger.info("Bot starting (polling)...")
    app.run_polling()
