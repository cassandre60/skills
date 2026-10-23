"""Property harness (repo-supplied): idempotence of src/text_ops.normalize.

The fuzz tool pipes the candidate input on stdin and expects rc 1 plus a
one-line violation report when the property fails.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from text_ops import normalize  # noqa: E402


def main() -> int:
    text = sys.stdin.read()
    once = normalize(text)
    try:
        twice = normalize(once)
    except Exception as exc:  # noqa: BLE001 - crash is a violation too
        print(f"property crashed on {text!r}: {exc}")
        return 1
    if twice != once:
        print(f"property violated: normalize({text!r}) = {once!r} but again = {twice!r}")
        return 1
    print(f"ok: idempotence holds for {text!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
