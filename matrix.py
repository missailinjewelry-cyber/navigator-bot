"""Матриця часу. Усе рахується кодом, а не ШІ, щоб час ніколи не плутався."""
import os
from datetime import datetime, timedelta, timezone


def _offset_hours():
    try:
        return float(os.getenv("PLATFORM_UTC_OFFSET", "3"))
    except ValueError:
        return 3.0


OFFSET = _offset_hours()
TZ = timezone(timedelta(hours=OFFSET))
TZ_LABEL = f"UTC{OFFSET:+g}"

# Таймфрейми з хвостами, як у твоїй таблиці (рахуються всередині години)
TAIL_TFS = [("M2", 2), ("M3", 3), ("M5", 5), ("M10", 10), ("M15", 15), ("M30", 30)]
ALL_TFS = TAIL_TFS + [("H1", 60), ("H4", 240)]


def now_platform():
    return datetime.now(TZ)


def _left(seconds):
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}г{m:02d}хв"
    return f"{m}хв{s:02d}с"


def matrix_snapshot(now=None):
    """Знімок матриці для поточної секунди."""
    now = now or now_platform()
    total = now.hour * 60 + now.minute
    lines = [f"ЧАС ПЛАТФОРМИ ({TZ_LABEL}): {now:%H:%M:%S}"]

    closed = [name for name, tf in ALL_TFS if total % tf == 0]
    if closed:
        lines.append(
            f"Щойно закрилися (о {now:%H:%M}:00, {now.second}с тому): {', '.join(closed)}"
        )
    else:
        lines.append("Цієї хвилини жоден таймфрейм від M2 не закривався (лише M1)")

    tails = [f"{name}: {now.minute // tf}+{now.minute % tf}м" for name, tf in TAIL_TFS]
    lines.append("Хвости (цілі свічки + хвіст): " + " | ".join(tails))

    h4_tail = (now.hour % 4) * 60 + now.minute
    lines.append(
        f"H1 відкрита {now.minute}хв тому | "
        f"H4 (умовно, від 00:00 платформи) відкрита {h4_tail // 60}г{h4_tail % 60:02d}хв тому"
    )

    lefts = []
    for name, tf in ALL_TFS:
        elapsed = (total % tf) * 60 + now.second
        lefts.append(f"{name} {_left(tf * 60 - elapsed)}")
    lines.append("До закриття поточної свічки: " + " | ".join(lefts))
    return "\n".join(lines)


def upcoming_junctions(now=None, horizon_min=60, min_tfs=3, limit=10):
    """Найближчі стики: моменти, коли одночасно закриваються кілька таймфреймів."""
    now = now or now_platform()
    base = now.replace(second=0, microsecond=0)
    rows = []
    for k in range(1, horizon_min + 1):
        t = base + timedelta(minutes=k)
        total = t.hour * 60 + t.minute
        names = [name for name, tf in ALL_TFS if total % tf == 0]
        if len(names) >= min_tfs:
            until = _left((t - now).total_seconds())
            rows.append(f"{t:%H:%M} (через {until}): {', '.join(names)}")
        if len(rows) >= limit:
            break
    if not rows:
        return f"НАЙБЛИЖЧІ СТИКИ: у наступні {horizon_min} хв немає закриття {min_tfs}+ таймфреймів одночасно."
    return (
        f"НАЙБЛИЖЧІ СТИКИ (закриття {min_tfs}+ таймфреймів одночасно, наступні {horizon_min} хв):\n"
        + "\n".join(rows)
    )
