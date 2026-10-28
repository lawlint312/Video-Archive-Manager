"""Merging one character folder into another.

The bugs these cover all cost real data or left real mess:
  * only videos were moved, so photos / notes / person_info.json were orphaned
  * subfolders were flattened, which would drop IG videos into the video list
  * person_info.json was renamed to person_info_1.json instead of merged,
    losing the aliases the catalogue lookup matches on
  * the source folder was removed while a rglob handle was still open

Run:  python -m pytest tests/ -q        (from backend/)
      python tests/test_merge.py
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import dedup            # noqa: E402


def build():
    tmp = Path(tempfile.mkdtemp())
    src, dst = tmp / "src", tmp / "dst"
    (src / "IG").mkdir(parents=True)
    (dst / "IG").mkdir(parents=True)

    (src / "only_in_src.mp4").write_bytes(b"a" * 1000)
    (src / "clash.mp4").write_bytes(b"b" * 1000)        # same name, other content
    (dst / "clash.mp4").write_bytes(b"c" * 1000)
    (src / "same.mp4").write_bytes(b"d" * 1000)         # byte-identical copy
    (dst / "same.mp4").write_bytes(b"d" * 1000)
    (src / "photo.jpg").write_bytes(b"e" * 100)         # not a video
    (src / "note.txt").write_text("keep me", encoding="utf-8")
    (src / "IG" / "ig_clip.mp4").write_bytes(b"f" * 1000)
    (src / "IG" / "ig_photo.jpg").write_bytes(b"g" * 100)

    (src / "person_info.json").write_text(json.dumps(
        {"aliases": ["來源別名", "Shared"], "tags": ["t-src"],
         "notes": "來源備註", "profile_photo": "photo.jpg"}), encoding="utf-8")
    (dst / "person_info.json").write_text(json.dumps(
        {"aliases": ["目標別名", "shared"], "tags": ["t-dst"],
         "notes": "目標備註", "profile_photo": "kept.jpg"}), encoding="utf-8")
    return tmp, src, dst


def run():
    tmp, src, dst = build()
    bad = []
    try:
        plan = dedup.build_merge_plan(src, dst)
        by = {p["name"].replace("\\", "/"): p["action"] for p in plan}

        expect = {
            "only_in_src.mp4": "move",
            "clash.mp4": "rename",
            "same.mp4": "delete",
            "photo.jpg": "move",            # non-videos must be planned too
            "note.txt": "move",
            "IG/ig_clip.mp4": "move",       # and keep their subfolder
            "IG/ig_photo.jpg": "move",
            "person_info.json": "merge_info",
        }
        for name, action in expect.items():
            if by.get(name) != action:
                bad.append(f"  plan[{name}] = {by.get(name)!r}, want {action!r}")

        dedup.run_merge(plan, src, dst)

        for rel in ("only_in_src.mp4", "clash_1.mp4", "photo.jpg", "note.txt",
                    "IG/ig_clip.mp4", "IG/ig_photo.jpg"):
            if not (dst / rel).exists():
                bad.append(f"  missing at destination: {rel}")
        if (dst / "person_info_1.json").exists():
            bad.append("  person_info.json was renamed instead of merged")

        info = json.loads((dst / "person_info.json").read_text(encoding="utf-8"))
        if sorted(a.lower() for a in info["aliases"]) != \
                sorted(["來源別名", "目標別名", "shared"]):
            bad.append(f"  aliases not unioned: {info['aliases']}")
        if info["profile_photo"] != "kept.jpg":
            bad.append("  destination's own profile_photo was overwritten")
        if "來源備註" not in info["notes"] or "目標備註" not in info["notes"]:
            bad.append(f"  notes lost: {info['notes']!r}")

        if src.exists():
            bad.append(f"  source folder not removed: {sorted(p.name for p in src.rglob('*'))}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return bad


def test_merge():
    assert not run(), "\n" + "\n".join(run())


if __name__ == "__main__":
    problems = run()
    if problems:
        print("FAILED:")
        print("\n".join(problems))
        sys.exit(1)
    print("merge: all checks pass")
