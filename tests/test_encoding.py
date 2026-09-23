"""Every text file read or write names its encoding.

Without one, Python uses the locale's code page -- UTF-8 on Linux and macOS,
cp1252 on a US Windows machine -- so the same line that works everywhere else
raises UnicodeDecodeError on the one platform this app is installed on. The
Windows CI job caught 20+ of these in the test suite and two in scripts; this
keeps the next one from reaching a user.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARY_OK = {"os.devnull"}


def _python_files():
    for base in ("backend", "scripts", "tests"):
        yield from (ROOT / base).rglob("*.py")
    yield from ROOT.glob("*.py")


def _mode(call: ast.Call):
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        return call.args[1].value
    for k in call.keywords:
        if k.arg == "mode" and isinstance(k.value, ast.Constant):
            return k.value.value
    return "r"


def test_text_io_always_names_its_encoding():
    offenders = []
    for f in _python_files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call) or any(k.arg == "encoding" for k in n.keywords):
                continue
            func = n.func
            if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
                offenders.append(f"{f.relative_to(ROOT)}:{n.lineno} .{func.attr}()")
            elif isinstance(func, ast.Name) and func.id == "open":
                target = ast.unparse(n.args[0]) if n.args else ""
                if "b" not in str(_mode(n)) and target not in BINARY_OK:
                    offenders.append(f"{f.relative_to(ROOT)}:{n.lineno} open({target})")
    assert offenders == [], "\n".join(offenders)
