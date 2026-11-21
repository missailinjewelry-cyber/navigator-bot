"""NavigatorMia_bot: режими, скріни, матриця часу, журнал без стирання, результати угод."""
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
from ai import MODELS, AIError, ask_model
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
BTN_SWITCH = "🔄 Змінити режим"
OLD_BTN_SWITCH = "🔄 Змінити пару/режим"  # стара назва кнопки, поки клавіатура не оновилась
BTN_STATS = "📊 Статистика"
MAIN_KB = ReplyKeyboardMarkup([[BTN_START, BTN_END], [BTN_SWITCH, BTN_STATS]], resize_keyboard=True)


def ikb(rows):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in rows]
    )


MODE_KB = ikb([[("🎯 Мій напрямок", "mode:mine")], [("🤖 Аналіз від бота", "mode:bot")]])
DIR_KB = ikb([[("⬆️ Вгору", "dir:UP"), ("⬇️ Вниз", "dir:DOWN")]])
END_KB = ikb([[("Так, пауза", "end:yes"), ("Ні", "end:no")]])
RESULT_LABELS = [("win", "✅ Зайшла"), ("loss", "❌ Не зайшла"), ("skip", "⏭ Пропустила")]

MODE_NAMES = {"mine": "Мій напрямок", "bot": "Аналіз від бота"}
DIR_NAMES = {"UP": "ВГОРУ", "DOWN": "ВНИЗ"}

ALBUMS = {}  # media_group_id -> {"images": [...], "caption": str}
WARNED = set()


def result_kb(entry_id, current=None):
    def button(key, label):
        mark = "✔ " if key == current else ""
        return InlineKeyboardButton(mark + label, callback_data=f"res:{entry_id}:{key}")

    return InlineKeyboardMarkup(
        [[button(*RESULT_LABELS[0]), button(*RESULT_LABELS[1])], [button(*RESULT_LABELS[2])]]
    )


def allowed(update):
    user = update.effective_user
    return bool(user and user.id in ALLOWED)


def is_ready(st):
    if not st.get("working") or not st.get("mode"):
        return False
    if st["mode"] == "mine" and not st.get("direction"):
        return False
    return True


def ready_text(st):
    s = f"Готово ✅ Режим: {MODE_NAMES.get(st.get('mode'), '?')}"
    if st.get("mode") == "mine":
        s += f", напрямок: {DIR_NAMES.get(st.get('direction'), '?')}"
    return s + ".\nНадсилай скріни (можна кілька одним альбомом) або пиши питання."


async def ask_next_step(message, st):
    """Показує наступний крок налаштування."""
    if not st.get("mode"):
        await message.reply_text("Обери режим:", reply_markup=MODE_KB)
    elif st["mode"] == "mine" and not st.get("direction"):
        await message.reply_text("Який напрямок ти бачиш?", reply_markup=DIR_KB)
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
    for key in ("mode", "direction", "direction_at"):
        st.pop(key, None)
    st["working"] = True
    await update.message.reply_text("Обери режим:", reply_markup=MODE_KB)


async def on_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        "Поставити бота на паузу? Записи й статистика збережуться.", reply_markup=END_KB
    )


async def on_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(memory.stats_text())


# ---------- inline-кнопки ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not allowed(update):
        return
    st = context.user_data
    data = q.data or ""

    if data.startswith("mode:"):
        mode = data.split(":", 1)[1]
        if mode not in MODE_NAMES:
            return
        st["mode"] = mode
        st["working"] = True
        st.pop("direction", None)
        st.pop("direction_at", None)
        await q.edit_message_text(f"Режим: {MODE_NAMES[mode]}")
        await ask_next_step(q.message, st)

    elif data.startswith("dir:"):
        st["direction"] = data.split(":", 1)[1]
        st["direction_at"] = now_platform().strftime("%H:%M:%S")
        await q.edit_message_text(f"Напрямок: {DIR_NAMES.get(st['direction'], '?')}")
        await ask_next_step(q.message, st)

    elif data.startswith("res:"):
        try:
            _, sid, key = data.split(":")
            saved = memory.set_result(int(sid), key)
        except ValueError:
            saved = False
        if not saved:
            await q.message.reply_text("Не вдалося записати результат.")
            return
        try:
            await q.edit_message_reply_markup(reply_markup=result_kb(int(sid), key))
        except Exception:
            pass  # те саме значення натиснули вдруге

    elif data == "end:yes":
        for key in ("working", "mode", "direction", "direction_at"):
            st.pop(key, None)
        await q.edit_message_text(
            "Пауза ⏸ Записи й статистика збережені.\nЩоб продовжити, натисни ▶️ Початок роботи."
        )

    elif data == "end:no":
        await q.edit_message_text("Продовжуємо.")


# ---------- текст ----------

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    st = context.user_data
    text = (update.message.text or "").strip()

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
        new_dir = "UP" if "вгору" in low else "DOWN" if "вниз" in low else None
        if new_dir:
            st["direction"] = new_dir
            st["direction_at"] = now_platform().strftime("%H:%M:%S")

    if not st.get("working") or not is_ready(st):
        if gid is None or gid not in WARNED:
            if not st.get("working"):
                await msg.reply_text("Натисни ▶️ Початок роботи.", reply_markup=MAIN_KB)
            else:
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
    lines = []
    if st.get("mode") == "mine":
        lines.append("РЕЖИМ: МІЙ НАПРЯМОК")
        lines.append(
            f"НАПРЯМОК КОРИСТУВАЧКИ: {DIR_NAMES.get(st.get('direction'), 'не задано')} "
            f"(задано о {st.get('direction_at', '?')})"
        )
    else:
        lines.append("РЕЖИМ: АНАЛІЗ ВІД БОТА (напрям визначаєш ти)")
    lines += ["", matrix_snapshot(now), "", upcoming_junctions(now), ""]

    hist = memory.get_recent()
    if hist:
        lines.append(
            f"ЩОДЕННИК (останні {memory.RECENT_HOURS:g} год, усі пари; "
            "використовуй лише записи тієї самої пари, що на скріні):"
        )
        lines.extend(hist)
    else:
        lines.append("ЩОДЕННИК: за останні години записів немає.")
    lines.append("")

    if images:
        lines.append(
            f"СКРІНІВ У ЗАПИТІ: {len(images)}. Запит створено о {now:%H:%M:%S}, "
            "відповідь ШІ займе 10-40 с. Пару визнач зі скріна. "
            "Якщо скріни з різних таймфреймів, визнач, який на якому."
        )
        if caption:
            lines.append(f"ПІДПИС КОРИСТУВАЧКИ: {caption}")
    else:
        pair = memory.last_pair()
        if pair:
            lines.append(f"Остання пара в щоденнику: {pair}")
        lines.append(f"СКРІНА НЕМАЄ, це звичайне повідомлення: {caption}")
    return "\n".join(lines)


async def send_long(bot, chat_id, text, reply_markup=None):
    text = text.strip()
    chunks = []
    while text:
        chunk = text[:4000]
        if len(text) > 4000:
            cut = chunk.rfind("\n")
            if cut > 1000:
                chunk = chunk[:cut]
        chunks.append(chunk)
        text = text[len(chunk):].lstrip("\n")
    for i, chunk in enumerate(chunks):
        await bot.send_message(
            chat_id, chunk, reply_markup=reply_markup if i == len(chunks) - 1 else None
        )


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

        markup = None
        if images:
            pair = memory.parse_pair(answer) or memory.last_pair() or "НЕВІДОМА ПАРА"
            verdict = memory.parse_verdict(answer)
            entry_id = memory.add_entry(
                pair, st.get("mode"), st.get("direction"), verdict, memory.extract_summary(answer)
            )
            if verdict in ("UP", "DOWN"):
                markup = result_kb(entry_id)  # кнопки для відмітки результату угоди
        await send_long(app.bot, chat_id, answer, markup)
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
    app.add_handler(MessageHandler(filters.Text([BTN_SWITCH, OLD_BTN_SWITCH]), on_switch))
    app.add_handler(MessageHandler(filters.Text([BTN_STATS]), on_stats))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    log.info(
        "Бот стартує (AI: %s, дозволених користувачів: %d, сховище: %s)",
        ", ".join(MODELS), len(ALLOWED), memory.DATA_DIR,
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
