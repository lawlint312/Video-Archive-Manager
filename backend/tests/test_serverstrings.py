"""The server's words the frontend translates must still be the server's words.

`frontend/src/serverstrings.js` lists the fixed text the backend sends (role
and permission names, security events, sign-in reasons, job names) so the
interface can show it in English or Simplified Chinese. It matches by the
exact Traditional Chinese, so rewording one here would quietly drop its
translation. This reads both sides and checks every listed string is still
written somewhere in the backend, and that the role and alert tables are
covered completely.

Reads source files only — no database, no data paths.

Run:  python tests/test_serverstrings.py        (from backend/)
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import roles, security                       # noqa: E402
from app.core import alerts                           # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def frontend_lists():
    js = (ROOT.parent / "frontend" / "src" / "serverstrings.js").read_text(encoding="utf-8")
    fixed = js.split("export const FIXED", 1)[1].split("export const TEMPLATES", 1)[0]
    templ = js.split("export const TEMPLATES", 1)[1].split("const FIXED_SET", 1)[0]
    grab = lambda s: re.findall(r"N_\('((?:[^'\\]|\\.)*)'\)", s)
    return grab(fixed), grab(templ)


def backend_source():
    text = "\n".join(p.read_text(encoding="utf-8")
                     for p in (ROOT / "app").rglob("*.py"))
    # implicit concatenation across lines: "…次" "又被中斷…" is one string
    text = re.sub(r'"\s*\n\s*f?"', "", text)
    # the sign-in log writes the constant, not the word
    return text.replace("{security.BLOCKED}", security.BLOCKED)


# Built from parts in code, so no single literal holds the whole template;
# the parts are what can drift.
ASSEMBLED = {
    "轉檔掃描（{list}）": ['"轉檔掃描"', '"（%s）"'],
    "請先修改密碼：{reason}": ['"請先修改密碼："'],
    "密碼還缺少：{list}": ['"密碼還缺少："'],
    # convert._run_checks / the decode pass: f"{label}：{path.name}（{k}/{len(todo)}{tail}）"
    "{check}：{name}（{k}/{total}）": ['：{path.name}（{k}/{len(todo)}{tail}）'],
    "{check}：{name}（{k}/{total}，另 {n} 支沿用上次結果）":
        ['：{path.name}（{k}/{len(todo)}{tail}）', '"，另 {reused} 支沿用上次結果"'],
    # repair.py: label = "找出壞掉的片段" + "（第 %d 輪）" % n
    "找出壞掉的片段（第 {n} 輪）": ['"找出壞掉的片段"', '"（第 %d 輪）"'],
}


def _message(node):
    """The text of a message node, f-string holes as {}; None if not text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}"
                       for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _message(node.left)
        if left is not None:
            right = _message(node.right)
            return left + (right if right is not None else "{}")
    return None


def backend_errors():
    """Every Chinese error message: HTTPException details and raised errors."""
    out = {}
    for path in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fn = ast.unparse(node.func)
            msg = None
            if fn.endswith("HTTPException"):
                for kw in node.keywords:
                    if kw.arg == "detail":
                        msg = _message(kw.value)
            elif re.search(r"(Error|Exception)$", fn) and node.args:
                msg = _message(node.args[0])
            if msg and re.search(r"[一-鿿]", msg):
                out.setdefault(msg, f"{path.relative_to(ROOT)}:{node.lineno}")
    return out


def main():
    fixed, templates = frontend_lists()
    src = backend_source()

    print("固定字串：每一條都還在後端")
    for s in fixed:
        # a part appended to a convert reason is written with its ；
        check(f'"{s}"' in src or f'"；{s}"' in src, s)

    print("\n帶數值的字串：去掉 {…} 之後的每一段都還連在一起")
    for k in templates:
        if k in ASSEMBLED:
            check(all(p in src for p in ASSEMBLED[k]), f"{k}（由幾段組成）")
            continue
        parts = re.split(r"\{\w+\}", k)
        pattern = r"\{[^{}\n]+\}".join(re.escape(p) for p in parts)
        check(re.search(pattern, src) is not None, k)

    print("\n整張表都有列到")
    listed = set(fixed)
    perms = {w for p in roles.PERMS for w in (p[1], p[2], p[3])}
    check(not (perms - listed), f"權限目錄 {sorted(perms - listed)}")
    names = {v[0] for v in roles.BUILTIN.values()} | {"管理員"}
    check(not (names - listed), f"內建權限組 {sorted(names - listed)}")
    labels = set(alerts.LABELS.values())
    check(not (labels - listed), f"安全事件名稱 {sorted(labels - listed)}")

    # A new error message has to be added to serverstrings.js, or English
    # readers get it in Chinese. Compared with the holes as wildcards.
    shapes = {re.sub(r"\{\w+\}", "{}", k) for k in fixed + templates}
    shapes |= {re.sub(r"\{\w+\}", "{}", k) for k in ASSEMBLED}
    shapes.add("請先修改密碼：{}")
    missing = {m: w for m, w in backend_errors().items() if m not in shapes}
    check(not missing, "後端每一個中文錯誤訊息都有列進 serverstrings.js"
          + "".join(f"\n         {w}  {m!r}" for m, w in sorted(missing.items())))

    print("\n" + (f"{len(FAIL)} 項失敗" if FAIL else "全部通過"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
