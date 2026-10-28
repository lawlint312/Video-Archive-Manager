"""Volume detection for merges.

Getting this wrong is expensive in both directions: calling a same-volume move
"cross-volume" makes the UI demand free space that is not needed, and the
reverse skips the space check before a copy that can fill the disk.

Run:  python -m pytest tests/ -q        (from backend/)
      python tests/test_same_volume.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.media import same_volume       # noqa: E402

CASES = [
    # two folders on one NAS share: a merge is a rename, no space needed.
    # This is the real case that prompted the check — the two archive roots
    # 亞洲/歐美 live on the same share and used to render as a cross-drive split.
    (r"\\192.168.1.10\share\Social Media_亞洲\X",
     r"\\192.168.1.10\share\Social Media_歐美\X", True),
    (r"C:\Users\me\archive\A", r"C:\Users\me\Downloads\B", True),
    (r"c:\a", r"C:\b", True),                       # case must not matter
    (r"C:\a", r"\\host\share\b", False),            # local vs UNC
    (r"\\host\share-a\a", r"\\host\share-b\b", False),   # same server, 2 shares
    (r"\\host-a\share\a", r"\\host-b\share\b", False),   # different servers
    (r"C:\a", r"D:\b", False),
]


def run():
    return [f"  {a} vs {b}: got {same_volume(Path(a), Path(b))}, want {want}"
            for a, b, want in CASES
            if same_volume(Path(a), Path(b)) != want]


def test_same_volume():
    assert not run(), "\n" + "\n".join(run())


if __name__ == "__main__":
    problems = run()
    if problems:
        print(f"{len(problems)}/{len(CASES)} FAILED:")
        print("\n".join(problems))
        sys.exit(1)
    print(f"all {len(CASES)} cases pass")
