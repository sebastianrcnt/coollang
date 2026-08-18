"""부트스트랩 대상의 메모리 한계 (implementation.md §7, gh #4, #11).

이 한계는 언어의 것이 아니라 **대상의 것**이다. 자기 호스팅 컴파일러는 32 MiB
안에서 살고 아레나는 소스 뒤에서 위로 자란다. 그 위에 고정된 스크래치 영역이
있으므로 힙이 거기 닿으면 컴파일러가 자기 메모리를 덮는다.

그래서 두 구현이 **같은 산술로** 같은 지점에서 거절해야 한다. 안 그러면 큰
소스에서 갈리고, 그것이 implementation.md §8 이 금지하는 상황이다.

여기 있는 것은 픽스처로 못 쓴다 -- 소스가 수십만 바이트라 파일로 두면 저장소가
그만큼 무거워진다. 그래서 시험이 직접 만든다.
"""

from __future__ import annotations

import pathlib

import pytest
import wasmtime

from conftest import run_compiler
from cool0.cool0 import BOOTSTRAP_SCRATCH, STATUS_OK, compile as reference_compile

SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "cool0"
COOL0C_WAT = SRC_DIR / "cool0c.wat"

TOO_LARGE = b"1:1: program is too large for the compiler's memory\n"

needs_wat = pytest.mark.skipif(not COOL0C_WAT.exists(), reason="cool0c.wat 이 아직 없다")


def _wat() -> bytes:
    return bytes(wasmtime.wat2wasm(COOL0C_WAT.read_text("ascii")))


def padded(n: int) -> bytes:
    """길이가 정확히 n 인 유효한 프로그램."""
    prog = b"fn f(n: u32) -> u32 { return n + 1; }"
    assert n >= len(prog)
    return b" " * (n - len(prog)) + prog


# 토큰 아레나가 S1 에 정확히 닿는 지점. 이슈가 계산해 온 값이다
COLLISION = 289120


def test_the_collision_point_is_where_the_issue_said():
    """heap0 + tok_bytes 가 정확히 S1 이 되는 소스 길이."""
    n = COLLISION
    heap0 = ((0x1000 + n + 3) // 4 * 4) + 4
    assert heap0 + (n + 1) * 28 == BOOTSTRAP_SCRATCH


@pytest.mark.parametrize("n", [COLLISION - 1, COLLISION, COLLISION + 1])
def test_the_oracle_rejects_at_the_boundary(n):
    assert reference_compile(padded(n)) == (1, TOO_LARGE)


def test_the_oracle_still_takes_a_source_well_under_the_limit():
    status, out = reference_compile(padded(200_000))
    assert status == STATUS_OK, out.decode("ascii", "replace")


@needs_wat
@pytest.mark.parametrize("n", [COLLISION - 1, COLLISION, COLLISION + 1])
def test_both_implementations_agree_at_the_boundary(n):
    src = padded(n)
    assert run_compiler(_wat(), src) == reference_compile(src)


@needs_wat
def test_both_agree_where_they_used_to_diverge():
    """전에는 오라클이 컴파일하고 자기 호스팅이 트랩했다."""
    unit = b"fn f%d(n: u32) -> u32 { let a: u32 = n + %d; return a * 3; }\n"
    body = b"".join(unit % (i, i) for i in range(3000))
    for total in (COLLISION, COLLISION + 40_000):
        src = (body + b" " * max(0, total - len(body)))[:total]
        assert run_compiler(_wat(), src) == reference_compile(src)


@needs_wat
def test_a_real_program_well_inside_the_limit_still_agrees():
    """cool0c 자신이 145 KB 다. 한계까지 2배쯤 여유가 있다."""
    src = (SRC_DIR / "cool0c.cool0").read_bytes()
    assert len(src) < COLLISION
    assert run_compiler(_wat(), src) == reference_compile(src)
