"""Пам'ять бота: журнал записів (нічого не стирається) і статистика результатів угод."""
import json
import logging
import os
from datetime import datetime, timedelta

from matrix import now_platform

log = logging.getLogger("bot")


def _resolve_data_dir():
    d = os.getenv("DATA_DIR")
    if not d:
        d = "/data" if os.path.isdir("/data") else "./data"
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = "./data"
        os.makedirs(d, exist_ok=True)
    return d


def _env_number(name, default, cast):
    try:
        return cast(os.getenv(name, "") or default)
    except ValueError:
        return default


DATA_DIR = _resolve_data_dir()
JOURNAL_PATH = os.path.join(DATA_DIR, "journal.json")
MAX_ENTRIES = 3000  # старіші записи зсуваються, щоб файл не розростався
RECENT_HOURS = _env_number("JOURNAL_HOURS", 3.0, float)  # скільки годин історії бачить ШІ
RECENT_LIMIT = _env_number("JOURNAL_LIMIT", 20, int)  # і не більше стількох записів
RESULT_MARK = {"win": " → ✅ зайшла", "loss": " → ❌ не зайшла", "skip": " → ⏭ пропущено"}


# ---------- розбір відповіді ШІ ----------

def normalize_pair(name):
    s = name.upper().replace("(", " ").replace(")", " ").replace("*", " ")
    s = " ".join(s.split()).strip(" .,;:")
    return s[:40] or "НЕВІДОМА ПАРА"


def parse_pair(text):
    for line in text.splitlines():
        s = line.strip()
        if s.upper().startswith("ПАРА") and ":" in s:
            value = s.split(":", 1)[1].strip()
            if value and "НЕВІДОМ" not in value.upper():
                return normalize_pair(value)
            return None
    return None


def parse_verdict(text):
    """Повертає UP, DOWN, WAIT або None."""
    for line in text.splitlines():
        s = line.strip().upper()
        if s.startswith("ВЕРДИКТ"):
            if "НЕ ВХІД" in s or "ЧЕКАЄМО" in s:
                return "WAIT"
            if "ВГОРУ" in s:
                return "UP"
            if "ВНИЗ" in s:
                return "DOWN"
            return None
    return None


def extract_summary(text):
    for line in reversed(text.splitlines()):
        if line.strip().upper().startswith("ПІДСУМОК"):
            return (line.split(":", 1)[1] if ":" in line else line).strip()[:300]
    return " ".join(text.split())[:300]


# ---------- журнал ----------

def _load():
    try:
        with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        data = {"entries": [], "next_id": 1}
    data.setdefault("next_id", 1)
    return data


def _save(data):
    try:
        tmp = JOURNAL_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, JOURNAL_PATH)
    except OSError:
        log.exception("Не вдалося зберегти журнал")


def add_entry(pair, mode, direction, verdict, summary):
    """Додає запис і повертає його номер."""
    data = _load()
    entry_id = data["next_id"]
    data["next_id"] = entry_id + 1
    data["entries"].append(
        {
            "id": entry_id,
            "ts": now_platform().isoformat(timespec="seconds"),
            "pair": normalize_pair(pair),
            "mode": mode,
            "dir": direction,
            "verdict": verdict,
            "s": summary,
            "result": None,
        }
    )
    data["entries"] = data["entries"][-MAX_ENTRIES:]
    _save(data)
    return entry_id


def set_result(entry_id, result):
    """result: win / loss / skip. Повертає True, якщо запис знайдено."""
    if result not in ("win", "loss", "skip"):
        return False
    data = _load()
    for e in data["entries"]:
        if e.get("id") == entry_id:
            e["result"] = result
            _save(data)
            return True
    return False


def get_recent(hours=None, limit=None):
    """Свіжі записи з усіх пар, у вигляді готових рядків для ШІ."""
    hours = RECENT_HOURS if hours is None else hours
    limit = RECENT_LIMIT if limit is None else limit
    cutoff = now_platform() - timedelta(hours=hours)
    picked = []
    for e in _load()["entries"]:
        try:
            ts = datetime.fromisoformat(e["ts"])
            if ts >= cutoff:
                picked.append((ts, e))
        except (KeyError, ValueError, TypeError):
            continue
    lines = []
    for ts, e in picked[-limit:]:
        who = "мій напрям" if e.get("mode") == "mine" else "від бота"
        mark = RESULT_MARK.get(e.get("result"), "")
        lines.append(f"{ts:%d.%m %H:%M:%S} [{e.get('pair')}] [{who}] {e.get('s')}{mark}")
    return lines


def last_pair():
    for e in reversed(_load()["entries"]):
        if e.get("pair"):
            return e["pair"]
    return None


# ---------- статистика ----------

def _counts(items):
    trades = [e for e in items if e.get("verdict") in ("UP", "DOWN")]
    c = {"win": 0, "loss": 0, "skip": 0}
    for e in trades:
        if e.get("result") in c:
            c[e["result"]] += 1
    pending = len(trades) - sum(c.values())
    return trades, c, pending


def _block_lines(items):
    trades, c, pending = _counts(items)
    if not trades:
        return [f"Аналізів: {len(items)}, входів не було."]
    lines = [
        f"Аналізів: {len(items)}, входів: {len(trades)}",
        f"✅ {c['win']}   ❌ {c['loss']}   ⏭ {c['skip']}   без відмітки {pending}",
    ]
    decided = c["win"] + c["loss"]
    if decided:
        line = f"Влучність: {100.0 * c['win'] / decided:.0f}% ({c['win']} з {decided})"
        if decided < 20:
            line += ", угод ще замало для висновків"
        lines.append(line)
    for mode, icon, name in (("mine", "🎯", "мій напрям"), ("bot", "🤖", "від бота")):
        _, mc, _ = _counts([e for e in items if e.get("mode") == mode])
        if mc["win"] + mc["loss"]:
            lines.append(f"{icon} {name}: ✅ {mc['win']}  ❌ {mc['loss']}")
    return lines


def stats_text():
    entries = _load()["entries"]
    if not entries:
        return "📊 Поки що записів немає. Вони з'являться після першого аналізу скрінів."
    today = now_platform().strftime("%Y-%m-%d")
    today_items = [e for e in entries if str(e.get("ts", "")).startswith(today)]
    parts = ["📊 Статистика", "", "Сьогодні:"]
    parts += _block_lines(today_items)
    parts += ["", "За весь час:"]
    parts += _block_lines(entries)
    parts += [
        "",
        "Для виплати 92% беззбитковість це приблизно 52% влучності "
        "(виплата на платформі може змінюватись).",
    ]
    return "\n".join(parts)
