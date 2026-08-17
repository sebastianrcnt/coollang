"""fixtures/cool0/ 아래 파일들을 읽어 파이썬 테스트로 넘겨준다.

파일 하나가 사례 하나다. 첫 줄은 언제나 `// expect: ...` 이고, 그 줄부터
파일 전체가 그대로 cool0 소스다 -- `//` 는 유효한 주석이라 컴파일러에 그대로
넘겨도 된다. 세 폴더의 형식:

    valid/*.cool0        // expect: ok
    invalid/*.cool0      // expect: error L:C: 문구      (implementation.md §9)
    semantics/*.cool0    // expect: fn(args) == value

파일 이름은 테스트 id 로 쓰인다 (확장자를 뗀 것).
"""

from __future__ import annotations

import pathlib
import re

FIXTURES_ROOT = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "cool0"

_EXPECT_PREFIX = b"// expect:"


def _read(path: pathlib.Path) -> bytes:
    return path.read_bytes()


def _expect_text(path: pathlib.Path, data: bytes) -> str:
    first_line = data.split(b"\n", 1)[0]
    assert first_line.startswith(_EXPECT_PREFIX), (
        f"{path}: first line must start with '// expect:'"
    )
    # ascii 디코드 -- 첫 줄 자체는 언제나 ascii 다. 그 뒤의 소스는 아닐 수도 있다
    # (예: non-ascii 바이트를 거부하는지 보는 사례)
    return first_line[len(_EXPECT_PREFIX):].decode("ascii").strip()


def load_valid() -> list[tuple[str, bytes]]:
    """(이름, 소스) 목록. fixtures/cool0/valid/ -- 전부 컴파일에 성공해야 한다."""
    out = []
    for path in sorted((FIXTURES_ROOT / "valid").glob("*.cool0")):
        data = _read(path)
        expect = _expect_text(path, data)
        assert expect == "ok", f"{path}: expected '// expect: ok', got {expect!r}"
        out.append((path.stem, data))
    return out


def load_invalid() -> list[tuple[str, bytes, bytes]]:
    """(이름, 소스, 기대하는 진단 한 줄) 목록. fixtures/cool0/invalid/.

    기대 진단은 `\\n` 으로 끝나는 바이트열이다 -- compile() 이 돌려주는 것과 같은
    모양이라 그대로 비교할 수 있다.
    """
    out = []
    for path in sorted((FIXTURES_ROOT / "invalid").glob("*.cool0")):
        data = _read(path)
        expect = _expect_text(path, data)
        prefix = "error "
        assert expect.startswith(prefix), (
            f"{path}: expected '// expect: error L:C: ...', got {expect!r}"
        )
        diagnostic = (expect[len(prefix):] + "\n").encode("ascii")
        out.append((path.stem, data, diagnostic))
    return out


_SEMANTICS_RE = re.compile(r"^(\w+)\(([^)]*)\)\s*==\s*(-?\d+)$")


def load_semantics() -> list[tuple[str, bytes, str, tuple[int, ...], int]]:
    """(이름, 소스, 함수 이름, 인자, 기대값) 목록. fixtures/cool0/semantics/."""
    out = []
    for path in sorted((FIXTURES_ROOT / "semantics").glob("*.cool0")):
        data = _read(path)
        expect = _expect_text(path, data)
        m = _SEMANTICS_RE.match(expect)
        assert m, f"{path}: cannot parse '// expect: {expect}' as `fn(args) == value`"
        fn_name, argstr, want = m.groups()
        args = tuple(int(a.strip()) for a in argstr.split(",") if a.strip())
        out.append((path.stem, data, fn_name, args, int(want)))
    return out
