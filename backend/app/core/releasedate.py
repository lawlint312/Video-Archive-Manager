"""Release dates typed by hand: one shape, whatever was typed.

Stored as `YYYY-MM-DD`, the shape the wiki import already writes, because a
string sort is then a date sort (the 「發行日」 sort relies on it). A date only
known to the year or month stays `YYYY` / `YYYY-MM` (the user's choice): an
invented `-01-01` would look exactly like a real date and could never be told
apart again — IAFD already stores bare years for that reason. String order is
still right: "2014" < "2014-05" < "2014-05-03" < "2015".

What is accepted, all turned into the dash form:
    2026-10-22  2026/10/22  2026.10.22  2026 10 22  2026-1-5
    2026年10月22日  20261022  ２０２６／１０／２２ (full-width)
    22/10/2026 or 10/22/2026 — only when one of the two is over 12, so which
    is the day is not a guess
What is refused, with a message saying why: impossible dates (2026-02-30),
years outside 1900–2100, 6-digit runs (YYMMDD or YYYYMM — no way to tell),
and day/month pairs that could be either.
"""
from __future__ import annotations

import datetime
import re
import unicodedata

MIN_YEAR, MAX_YEAR = 1900, 2100
EXAMPLE = "請用 年-月-日，例如 2026-10-22"


def normalize(text: str) -> str:
    """The stored form of a typed date; "" for empty. ValueError if unusable."""
    s = unicodedata.normalize("NFKC", text or "").strip()
    if not s:
        return ""
    s = re.sub(r"[年月/.\s_]+", "-", s.replace("日", "")).strip("-")

    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        y, mo, d = m.groups()
        return _build(y, mo, d)
    if re.fullmatch(r"\d{5,7}|\d{9,}", s):
        raise ValueError(f"看不出「{text.strip()}」是哪一天。{EXAMPLE}")

    parts = s.split("-")
    if not all(p.isdigit() for p in parts) or not 1 <= len(parts) <= 3:
        raise ValueError(f"「{text.strip()}」不是日期。{EXAMPLE}")
    if len(parts[0]) == 4:
        return _build(*parts)
    if len(parts) == 3 and len(parts[2]) == 4:
        # day and month in front, year last: only safe when one is over 12
        a, b, y = int(parts[0]), int(parts[1]), parts[2]
        if a > 12 >= b:
            return _build(y, b, a)
        if b > 12 >= a:
            return _build(y, a, b)
        raise ValueError(f"「{text.strip()}」分不出哪個是月、哪個是日。{EXAMPLE}")
    raise ValueError(f"「{text.strip()}」缺少四位數的年份。{EXAMPLE}")


def _build(y, mo=None, d=None) -> str:
    y = int(y)
    if not MIN_YEAR <= y <= MAX_YEAR:
        raise ValueError(f"年份 {y} 不合理。{EXAMPLE}")
    if mo is None:
        return f"{y:04d}"
    mo = int(mo)
    if not 1 <= mo <= 12:
        raise ValueError(f"沒有 {mo} 月。{EXAMPLE}")
    if d is None:
        return f"{y:04d}-{mo:02d}"
    d = int(d)
    try:
        datetime.date(y, mo, d)
    except ValueError:
        raise ValueError(f"{y} 年 {mo} 月沒有 {d} 日。{EXAMPLE}") from None
    return f"{y:04d}-{mo:02d}-{d:02d}"
