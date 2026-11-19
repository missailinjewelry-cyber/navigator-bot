"""Пам'ять бота. Щоденник пар живе один день і чиститься на новий день або при завершенні роботи."""
import json
import logging
import os

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


DATA_DIR = _resolve_data_dir()
JOURNAL_PATH = os.path.join(DATA_DIR, "journal.json")
MAX_ENTRIES_PER_PAIR = 40


def normalize_pair(name):
    return " ".join(name.strip().upper().split())[:40] or "ПАРА"


def _today():
    return now_platform().strftime("%Y-%m-%d")


def _load():
    try:
        with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("date") != _today():
        data = {"date": _today(), "pairs": {}}  # новий день: чиста історія
    data.setdefault("pairs", {})
    return data


def _save(data):
    try:
        tmp = JOURNAL_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, JOURNAL_PATH)
    except OSError:
        log.exception("Не вдалося зберегти щоденник")


def add_entry(pair, mode, direction, summary):
    data = _load()
    key = normalize_pair(pair)
    entries = data["pairs"].setdefault(key, [])
    entries.append(
        {
            "t": now_platform().strftime("%H:%M:%S"),
            "mode": mode,
            "dir": direction,
            "s": summary,
        }
    )
    data["pairs"][key] = entries[-MAX_ENTRIES_PER_PAIR:]
    _save(data)


def get_recent(pair, n=6):
    data = _load()
    entries = data["pairs"].get(normalize_pair(pair), [])[-n:]
    out = []
    for e in entries:
        who = "мій напрям" if e.get("mode") == "mine" else "від бота"
        out.append(f"{e.get('t')} [{who}] {e.get('s')}")
    return out


def list_pairs():
    return list(_load()["pairs"].keys())


def clear_all():
    _save({"date": _today(), "pairs": {}})
