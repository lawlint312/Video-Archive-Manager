"""External subtitle files, converted to WebVTT for the browser.

A `<track>` element only accepts WebVTT. Every subtitle in this library is
`.ass` (31 files, all under J:\\動畫), which the browser cannot read at all —
so something has to convert, and ffmpeg is already a dependency.

Three things this file exists for, all of them measured rather than assumed:

**Encoding.** The 31 files are 17 UTF-16 and 14 UTF-8 *in the same folder, for
the same series*. Reading them all as UTF-8 produces mojibake silently — no
exception, just a screen of replacement characters. ffmpeg's ASS demuxer sniffs
the BOM and gets both right, which is the main reason to shell out rather than
parse the format here.

**Karaoke duplicates.** ASS karaoke is written as many Dialogue lines for one
sung line: one per syllable, each starting a little later and all ending
together. ffmpeg faithfully emits every one as its own cue, and a browser stacks
overlapping cues — so a song line came out printed up to twenty times.

They are NOT byte-identical (the start times differ), which is what an earlier
version of this got wrong: dropping only exact repeats fixed the first line of
episode 1 and nothing else. The pattern that actually identifies them is *same
text, same end time*, so that is the key, and the earliest start survives.

Measured over all 31 files: the busiest instant in an episode drops from 20
simultaneous cues to 3, and 2,068 of 15,331 cues turn out to be karaoke
repeats. Dialogue is untouched — it is already one cue at a time.

One case stays ugly and cannot be fixed here: the film's ending credits are
ASS vertical text (one character per line), eleven columns at once. WebVTT has
no way to express that.

**Bilingual lines are merged into one cue.** A translated line arrives as two
cues covering the same moment, and the browser stacks them — correctly, but as
two separate boxes, and the gap between two boxes is a whole line of the cue
grid. It looked like a canyon, and it cannot be closed with CSS: `line-height`
shrinks the box, which on auto placement drags the text DOWN the screen faster
than it tightens the gap (measured: 1.2 bought 9px of gap and cost 25px of
height). Merging makes the spacing ordinary in-box line spacing instead.

"Same moment" is a proportion, not an exact match, and that distinction is the
whole feature. An earlier version required both timestamps to be equal and so
merged the first few lines of an episode and **nothing in the ending theme** —
where the pairs look like this:

    1400.490 --> 1402.670   だからカッコつけないで
    1400.490 --> 1402.630   所以不要逞強了

40 milliseconds apart. The rule is instead: merge when the overlap is at least
`MERGE_RATIO` of the longer cue.

The threshold is measured, not chosen. Over every overlapping pair in the
library the distribution is sharply bimodal — 840 of 858 pairs sit at 0.95 or
above (a line and its translation), the rest trail off below 0.80 (two
different characters talking over each other, e.g. 「跟我來 西蒙!」against
「等等我 大哥!」). The 0.80–0.85 band is **empty**, so the threshold sits in
the gap rather than on top of either group.

The merged lines are in REVERSE document order, because that is what the
browser was already drawing: it stacks the first cue at the bottom and later
ones above it, so the translation was on top.

**No disk cache.** Conversion is 111 ms median over the 31 files (~50 KB each),
once when a video is opened, against a video that takes longer than that to
start playing. Caching it would buy nothing measurable and cost an eviction
policy, an invalidation key and a cache-limit setting.

What is lost, and it is not nothing: WebVTT cannot express karaoke colouring
(`\\K`, 18,768 uses) or positioning (`\\pos`, 2,827). Signs and captions that
ASS pins near the top of the frame land at the bottom with the dialogue. The
alternative was a 1–2 MB libass wasm build plus fonts, in a frontend that
currently has no dependency but Vue.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from .media import SUBPROCESS_FLAGS

log = logging.getLogger("vam")

# Generous: the largest file here is 143 KB of text. A timeout at all means
# ffmpeg is stuck, not that the file is big.
TIMEOUT = 30


_CUE = re.compile(r"^([0-9:.]+)\s+-->\s+([0-9:.]+)(.*)$")


def _dedup(vtt: str) -> str:
    """Drop karaoke repeats: same text and same end time, earliest start wins.

    Keyed on the end time and the payload rather than on the whole block,
    because the repeats differ in exactly one field — the start — and an
    exact-match rule therefore catches none of them.

    Two cues that overlap but say different things are a real bilingual
    subtitle and both survive; the browser stacks them, which is the right
    picture. Two cues with the same text at unrelated times also both survive,
    because a line genuinely said twice is not a duplicate.

    ffmpeg emits cues in start order, so keeping the first block of each group
    keeps the earliest start without re-sorting the file.
    """
    blocks = vtt.replace("\r\n", "\n").split("\n\n")
    seen = set()
    out = []
    for b in blocks:
        lines = b.strip().split("\n")
        m = _CUE.match(lines[0]) if lines else None
        if m is None:
            out.append(b)          # header, NOTE, STYLE — never deduplicated
            continue
        key = (m.group(2), "\n".join(lines[1:]))
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return "\n\n".join(out)


# Overlap, as a fraction of the longer cue, above which two cues are treated as
# one line in two languages. Sits in an empty band of the measured distribution
# — see the module docstring.
MERGE_RATIO = 0.85


def _secs(stamp: str) -> float:
    """VTT timestamp to seconds. `MM:SS.mmm` and `HH:MM:SS.mmm` both occur."""
    out = 0.0
    for part in stamp.split(":"):
        out = out * 60 + float(part)
    return out


def _stamp(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{sec:06.3f}"


def _merge_simultaneous(vtt: str) -> str:
    """Combine cues covering the same moment into one multi-line cue.

    Reverse order on purpose: with two separate cues the browser puts the first
    at the bottom and stacks the second above it, so the translation was the top
    line. Joining them in document order would silently swap the two languages.
    """
    blocks = vtt.replace("\r\n", "\n").split("\n\n")
    head, cues = [], []
    for b in blocks:
        lines = b.strip().split("\n")
        m = _CUE.match(lines[0]) if lines else None
        if m is None:
            head.append(b)
            continue
        cues.append({"start": _secs(m.group(1)), "end": _secs(m.group(2)),
                     "settings": m.group(3).strip(), "body": lines[1:],
                     "raw": lines[0].strip()})

    used = [False] * len(cues)
    out = list(head)
    for i, c in enumerate(cues):
        if used[i]:
            continue
        used[i] = True
        group = [c]
        for j in range(i + 1, len(cues)):
            if used[j]:
                continue
            d = cues[j]
            # Cues are in start order, so once one begins after this group's
            # current end, nothing later can overlap it either.
            if d["start"] >= c["end"]:
                break
            # Different cue settings mean the file placed them differently;
            # they are two slots on screen, not one line in two languages.
            if d["settings"] != c["settings"]:
                continue
            overlap = min(c["end"], d["end"]) - max(c["start"], d["start"])
            longest = max(c["end"] - c["start"], d["end"] - d["start"])
            if longest > 0 and overlap >= MERGE_RATIO * longest:
                group.append(d)
                used[j] = True
        body = []
        for g in reversed(group):
            body += g["body"]
        if len(group) == 1:
            # Nothing merged — keep the original timestamp line byte for byte
            # rather than reformatting 13,000 cues that did not need it.
            stamp = c["raw"]
        else:
            lo = min(g["start"] for g in group)
            hi = max(g["end"] for g in group)
            stamp = f"{_stamp(lo)} --> {_stamp(hi)}"
            if c["settings"]:
                stamp += " " + c["settings"]
        out.append(stamp + "\n" + "\n".join(body))
    return "\n\n".join(out)


def to_vtt(path: Path) -> Optional[str]:
    """Convert a subtitle file to WebVTT text. None if it cannot be read."""
    try:
        r = subprocess.run(
            # stdout, not a temp file: the output is tens of KB and writing it
            # anywhere means cleaning it up afterwards.
            ["ffmpeg", "-i", str(path), "-f", "webvtt", "-", "-loglevel", "error"],
            capture_output=True, timeout=TIMEOUT,
            creationflags=SUBPROCESS_FLAGS)
    except Exception as e:
        log.warning("subtitle conversion failed for %s: %s", path, e)
        return None
    if r.returncode != 0 or not r.stdout:
        # One line rather than a traceback: a broken subtitle file is an
        # expected condition, and the caller answers 404 for it.
        log.warning("subtitle conversion failed for %s: %s",
                    path, r.stderr.decode("utf-8", "replace")[:200])
        return None
    # ffmpeg writes UTF-8 whatever went in; `replace` so one bad byte cannot
    # lose an otherwise good subtitle track.
    return _merge_simultaneous(_dedup(r.stdout.decode("utf-8", "replace")))
