"""NavigatorMia_bot, етап 1: кнопки, режими, скріни, матриця часу, щоденник пари."""
import asyncio
import logging
import os
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PersistenceInput,
    PicklePersistence,
    filters,
)

import memory
from ai import MODEL, AIError, ask_model
from matrix import matrix_snapshot, now_platform, upcoming_junctions
from prompts import SYSTEM_PROMPT

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
# httpx пише в лог адреси запитів, а в них токен бота. Вимикаємо.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED = {
    int(x) for x in re.split(r"[,\s;]+", os.getenv("ALLOWED_USER_IDS", "")) if x.strip().isdigit()
}
EXTRA_PROMPT = os.getenv("EXTRA_PROMPT", "").strip()
SYSTEM = SYSTEM_PROMPT + (
    f"\nДОДАТКОВІ ПРАВИЛА ВІД КОРИСТУВАЧКИ:\n{EXTRA_PROMPT}\n" if EXTRA_PROMPT else ""
)

BTN_START = "▶️ Початок роботи"
BTN_END = "⏹ Кінець роботи"
BTN_SWITCH = "🔄 Змінити пару/режим"
MAIN_KB = ReplyKeyboardMarkup([[BTN_START, BTN_END], [BTN_SWITCH]], resize_keyboard=True)


def ikb(rows):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in rows]
    )


MODE_KB = ikb([[("🎯 Мій напрямок", "mode:mine")], [("🤖 Аналіз від бота", "mode:bot")]])
PAIR_KB = ikb([[("🆕 Нова пара", "pair:new"), ("↩️ Продовжити пару", "pair:cont")]])
DIR_KB = ikb([[("⬆️ Вгору", "dir:UP"), ("⬇️ Вниз", "dir:DOWN")]])
END_KB = ikb([[("Так, завершити", "end:yes"), ("Ні", "end:no")]])

MODE_NAMES = {"mine": "Мій напрямок", "bot": "Аналіз від бота"}
DIR_NAMES = {"UP": "ВГОРУ", "DOWN": "ВНИЗ"}

ALBUMS = {}  # media_group_id -> {"images": [...], "caption": str}
WARNED = set()


def allowed(update):
    user = update.effective_user
    return bool(user and user.id in ALLOWED)


def is_ready(st):
    if not st.get("working") or not st.get("mode") or not st.get("pair"):
        return False
    if st["mode"] == "mine" and not st.get("direction"):
        return False
    return True


def ready_text(st):
    s = f"Готово ✅ Режим: {MODE_NAMES.get(st.get('mode'), '?')}, пара: {st.get('pair')}"
    if st.get("mode") == "mine":
        s += f", напрямок: {DIR_NAMES.get(st.get('direction'), '?')}"
    return s + ".\nНадсилай скріни (можна кілька одним альбомом) або пиши питання."


async def ask_next_step(message, st):
    """Показує наступний крок налаштування."""
    if not st.get("mode"):
        await message.reply_text("Обери режим:", reply_markup=MODE_KB)
    elif not st.get("pair"):
        await message.reply_text("Яка пара?", reply_markup=PAIR_KB)
    elif st["mode"] == "mine" and not st.get("direction"):
        await message.reply_text(
            f"Пара: {st['pair']}. Який напрямок ти бачиш?", reply_markup=DIR_KB
        )
    else:
        await message.reply_text(ready_text(st))


# ---------- команди й кнопки меню ----------

async def on_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Твій Telegram ID: {update.effective_user.id}")


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    st = context.user_data
    st.clear()
    st["working"] = True
    await update.message.reply_text("Привіт! Починаємо роботу ✅", reply_markup=MAIN_KB)
    await update.message.reply_text("Обери режим:", reply_markup=MODE_KB)


async def on_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    st = context.user_data
    for key in ("mode", "pair", "direction", "awaiting_pair", "pair_choices"):
        st.pop(key, None)
    st["working"] = True
    await update.message.reply_text("Обери режим:", reply_markup=MODE_KB)


async def on_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        "Завершити роботу й очистити історію пар за сьогодні?", reply_markup=END_KB
    )


# ---------- inline-кнопки ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not allowed(update):
        return
    st = context.user_data
    data = q.data or ""

    if data.startswith("mode:"):
        st["mode"] = data.split(":", 1)[1]
        st["working"] = True
        st.pop("direction", None)
        await q.edit_message_text(f"Режим: {MODE_NAMES.get(st['mode'], '?')}")
        await ask_next_step(q.message, st)

    elif data == "pair:new":
        st["awaiting_pair"] = True
        await q.edit_message_text("Нова пара")
        await q.message.reply_text("Напиши назву пари (наприклад CHF/JPY OTC).")

    elif data == "pair:cont":
        pairs = memory.list_pairs()
        if not pairs:
            st["awaiting_pair"] = True
            await q.edit_message_text("Продовжити пару")
            await q.message.reply_text(
                "Сьогодні ще немає збережених пар. Напиши назву нової пари."
            )
        else:
            st["pair_choices"] = pairs
            rows = [[(p, f"pairsel:{i}")] for i, p in enumerate(pairs)]
            await q.edit_message_text("Обери пару:", reply_markup=ikb(rows))

    elif data.startswith("pairsel:"):
        choices = st.get("pair_choices") or []
        try:
            st["pair"] = choices[int(data.split(":", 1)[1])]
        except (ValueError, IndexError):
            await q.edit_message_text("Не вдалося обрати пару, спробуй ще раз.")
            return
        st.pop("direction", None)
        await q.edit_message_text(f"Пара: {st['pair']} (історія за сьогодні збережена)")
        await ask_next_step(q.message, st)

    elif data.startswith("dir:"):
        st["direction"] = data.split(":", 1)[1]
        await q.edit_message_text(f"Напрямок: {DIR_NAMES.get(st['direction'], '?')}")
        await ask_next_step(q.message, st)

    elif data == "end:yes":
        memory.clear_all()
        st.clear()
        await q.edit_message_text(
            "Роботу завершено, історію пар очищено. Добраніч 🌙\nЩоб почати знову, натисни ▶️"
        )

    elif data == "end:no":
        await q.edit_message_text("Продовжуємо.")


# ---------- текст ----------

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    st = context.user_data
    text = (update.message.text or "").strip()

    if st.get("awaiting_pair"):
        st["awaiting_pair"] = False
        st["pair"] = memory.normalize_pair(text)
        st.pop("direction", None)
        await ask_next_step(update.message, st)
        return

    if not st.get("working"):
        await update.message.reply_text("Натисни ▶️ Початок роботи.", reply_markup=MAIN_KB)
        return
    if not is_ready(st):
        await ask_next_step(update.message, st)
        return

    context.application.create_task(
        run_analysis(context.application, update.effective_chat.id, st, [], text), update=update
    )


# ---------- скріни ----------

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    st = context.user_data
    msg = update.message
    caption = msg.caption or ""
    gid = msg.media_group_id

    # у режимі «мій напрямок» напрям можна змінити підписом до скріна
    if st.get("mode") == "mine":
        low = caption.lower()
        if "вгору" in low:
            st["direction"] = "UP"
        elif "вниз" in low:
            st["direction"] = "DOWN"

    if not st.get("working"):
        if gid is None or gid not in WARNED:
            await msg.reply_text("Натисни ▶️ Початок роботи.", reply_markup=MAIN_KB)
        if gid:
            WARNED.add(gid)
        return
    if not is_ready(st):
        if gid is None or gid not in WARNED:
            await ask_next_step(msg, st)
        if gid:
            WARNED.add(gid)
            if len(WARNED) > 100:
                WARNED.clear()
        return

    try:
        source = msg.photo[-1] if msg.photo else msg.document
        tg_file = await source.get_file()
        data = bytes(await tg_file.download_as_bytearray())
    except Exception:
        log.exception("Не вдалося завантажити фото")
        await msg.reply_text("Не вдалося завантажити фото, спробуй ще раз.")
        return

    app = context.application
    if gid:
        entry = ALBUMS.get(gid)
        if entry is None:
            entry = {"images": [], "caption": ""}
            ALBUMS[gid] = entry
            app.create_task(finish_album(app, gid, msg.chat_id, st), update=update)
        entry["images"].append(data)
        if caption and not entry["caption"]:
            entry["caption"] = caption
    else:
        app.create_task(run_analysis(app, msg.chat_id, st, [data], caption), update=update)


async def finish_album(app, gid, chat_id, st):
    await asyncio.sleep(2.5)  # чекаємо, поки долетять усі скріни альбому
    entry = ALBUMS.pop(gid, None)
    if entry:
        await run_analysis(app, chat_id, st, entry["images"][:4], entry["caption"])


# ---------- аналіз ----------

def build_user_text(st, images, caption):
    now = now_platform()
    pair = st.get("pair", "?")
    lines = []
    if st.get("mode") == "mine":
        lines.append("РЕЖИМ: МІЙ НАПРЯМОК")
        lines.append(f"НАПРЯМОК КОРИСТУВАЧКИ: {DIR_NAMES.get(st.get('direction'), 'не задано')}")
    else:
        lines.append("РЕЖИМ: АНАЛІЗ ВІД БОТА (напрям визначаєш ти)")
    lines.append(f"ПАРА: {pair}")
    lines.append("")
    lines.append(matrix_snapshot(now))
    lines.append("")
    lines.append(upcoming_junctions(now))
    lines.append("")
    hist = memory.get_recent(pair, 6)
    if hist:
        lines.append("ЩОДЕННИК ПАРИ ЗА СЬОГОДНІ (від старого до нового):")
        lines.extend(hist)
    else:
        lines.append("ЩОДЕННИК ПАРИ: порожньо (це перший аналіз по цій парі сьогодні)")
    lines.append("")
    if images:
        lines.append(
            f"СКРІНІВ У ЗАПИТІ: {len(images)}. Запит створено о {now:%H:%M:%S}, "
            "відповідь ШІ займе 10-40 с. Якщо скріни з різних таймфреймів, визнач, який на якому."
        )
        if caption:
            lines.append(f"ПІДПИС КОРИСТУВАЧКИ: {caption}")
    else:
        lines.append(f"СКРІНА НЕМАЄ, це звичайне повідомлення: {caption}")
    return "\n".join(lines)


def extract_summary(text):
    for line in reversed(text.splitlines()):
        if line.strip().upper().startswith("ПІДСУМОК"):
            return (line.split(":", 1)[1] if ":" in line else line).strip()[:300]
    return " ".join(text.split())[:300]


async def send_long(bot, chat_id, text):
    text = text.strip()
    while text:
        chunk = text[:4000]
        if len(text) > 4000:
            cut = chunk.rfind("\n")
            if cut > 1000:
                chunk = chunk[:cut]
        await bot.send_message(chat_id, chunk)
        text = text[len(chunk):].lstrip("\n")


async def run_analysis(app, chat_id, st, images, caption):
    try:
        if images:
            await app.bot.send_message(chat_id, "⏳ Аналізую…")
        else:
            await app.bot.send_chat_action(chat_id, "typing")
        user_text = build_user_text(st, images, caption)
        try:
            answer = await ask_model(SYSTEM, user_text, images)
        except AIError as e:
            await app.bot.send_message(chat_id, str(e))
            return
        if images:
            memory.add_entry(
                st.get("pair", "?"), st.get("mode"), st.get("direction"), extract_summary(answer)
            )
        await send_long(app.bot, chat_id, answer)
    except Exception:
        log.exception("Помилка аналізу")
        try:
            await app.bot.send_message(chat_id, "Щось пішло не так. Спробуй ще раз.")
        except Exception:
            pass


async def on_error(update, context):
    log.error("Помилка в обробнику: %s", context.error, exc_info=context.error)


def main():
    if not TOKEN:
        raise SystemExit("Не задано TELEGRAM_BOT_TOKEN")
    if not ALLOWED:
        log.warning("ALLOWED_USER_IDS порожня: бот нікому не відповідатиме")

    persistence = PicklePersistence(
        filepath=os.path.join(memory.DATA_DIR, "state.pkl"),
        store_data=PersistenceInput(bot_data=False, chat_data=False, callback_data=False),
        update_interval=30,
    )
    app = Application.builder().token(TOKEN).persistence(persistence).build()

    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("id", on_id))
    app.add_handler(MessageHandler(filters.Text([BTN_START]), on_start))
    app.add_handler(MessageHandler(filters.Text([BTN_END]), on_end))
    app.add_handler(MessageHandler(filters.Text([BTN_SWITCH]), on_switch))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    log.info(
        "Бот стартує (AI: %s, дозволених користувачів: %d, сховище: %s)",
        MODEL, len(ALLOWED), memory.DATA_DIR,
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
