"""Catalogue-ID detection.

Every case below came from the real library. The detector is deliberately
conservative: a wrong ID files a video under the wrong performer, which is
worse than not recognising one at all — so the "must not match" list matters
more than the "must match" one.

Run:  python -m pytest tests/ -q        (from backend/)
      python tests/test_detect_video_id.py   (no pytest needed)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.media import detect_video_id      # noqa: E402

SHOULD_MATCH = [
    # plain catalogue numbers
    ("STARS-786.mp4", "STARS-786"),
    ("REBD-882.mp4", "REBD-882"),
    ("Mida-493 井上桜 Miracle Gem Av出道.mp4", "MIDA-493"),
    ("HND-604 菜鳥 18 歲.mp4", "HND-604"),
    ("Hnds-034 您可以隨時隨地.mp4", "HNDS-034"),
    ("Watch SM-034 Sensitive Amateur Office Lady.mp4", "SM-034"),
    # zero padding is normalised
    ("ARA-48.mp4", "ARA-048"),
    # FC2 wins even inside a noisy name, and part numbers do not confuse it
    ("hhd800.com@FC2-PPV-4510833_3.mp4", "FC2-PPV-4510833"),
    ("FC2PPV 4821059 今天是我的危險日.ts", "FC2-PPV-4821059"),
    ("HEYZO-1234 something.mp4", "HEYZO-1234"),
    # amateur labels: MMDDYY_NNN, either separator
    ("PONDO-080525_001 水手服Cosplay.ts", "080525-001"),
    ("caribbeancom-090124-001.mp4", "090124-001"),
]

SHOULD_NOT_MATCH = [
    # social-media exports: handle + unix timestamp + media id
    "user_c__1750853197_3600000000000000003_70000000003.mp4",
    "user_a.__1733923195_3500000000000000001_60000000001.mp4",
    "user_b_1777394916_3800000000000000002_30000000002.mp4",
    # a handle that merely looks like LABEL_NN
    "[2026-06-06] HARU_LOVE_86 - group show.mkv",
    "EW_CSI_03-vepbsQNY.mp4",
    "CB_jenny_lii_26-06-13_16-44.mp4",
    "lee_chang_2026-06-19_00-21.mp4",
    # resolution / field order, not a number
    "FellatioJapan_643_Erika_2160p.mp4",
    "FellatioJapan_644_Rio_2160p.mp4",
    "clip_1080i.mkv",
    # camera filenames
    "IMG_6126.MOV",
    # stream recordings: the HHMMSS-ms tail has the amateur shape
    "stripchat-model_a-2026-03-19-113020-429.mp4",
    "stripchat-model_b-2026-03-22-194327-306.mp4",
    "stripchat-model_a-2026-03-17-130002-875.mp4",
    # impossible month/day
    "something-463735-130.mp4",
    # UUID / content-hash filenames from download tools
    "7e97dfa4-eefe-4782-8ae6-52aa04e61032.mp4",
    "08628607-2683-47cd-84d9-cb29866546c2.mp4",
    "6d37abf54552f1c323e6ed22923d1c9db5a8303fc96b5fa037a0dcf2c2664dbf.mp4",
]


def run():
    bad = []
    for name, want in SHOULD_MATCH:
        got = detect_video_id(name)
        if got != want:
            bad.append(f"  {name}\n      got {got!r}, want {want!r}")
    for name in SHOULD_NOT_MATCH:
        got = detect_video_id(name)
        if got is not None:
            bad.append(f"  {name}\n      got {got!r}, want None")
    return bad


def test_detect_video_id():
    assert not run(), "\n" + "\n".join(run())


if __name__ == "__main__":
    problems = run()
    total = len(SHOULD_MATCH) + len(SHOULD_NOT_MATCH)
    if problems:
        print(f"{len(problems)}/{total} FAILED:")
        print("\n".join(problems))
        sys.exit(1)
    print(f"all {total} cases pass")
