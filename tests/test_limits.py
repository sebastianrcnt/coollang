"""부트스트랩 대상의 메모리 한계 (implementation.md §7, gh #4, #11).

이 한계는 언어의 것이 아니라 **대상의 것**이다. 자기 호스팅 컴파일러는 32 MiB
안에서 살고 아레나는 소스 뒤에서 위로 자란다. 그 위에 고정된 스크래치 영역이
있으므로 힙이 거기 닿으면 컴파일러가 자기 메모리를 덮는다.

그래서 두 구현이 **같은 산술로** 같은 지점에서 거절해야 한다. 안 그러면 큰
소스에서 갈리고, 그것이 implementation.md §8 이 금지하는 상황이다.

경계값을 적어 두지 않는다. `BOOTSTRAP_SCRATCH` 에서 유도한다 -- 한 번 상수로
박아 뒀다가 스크래치 영역을 옮기고 나서 시험 넷이 한꺼번에 빨개졌다.
"""

from __future__ import annotations

import pathlib

import pytest
import wasmtime

from conftest import run_compiler
from cool0.cool0 import BOOTSTRAP_SCRATCH, SRC_ADDR, STATUS_OK
from cool0.cool0 import align_up, compile as reference_compile

SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "cool0"
COOL0C_WAT = SRC_DIR / "cool0c.wat"

TOO_LARGE = b"1:1: program is too large for the compiler's memory\n"

needs_wat = pytest.mark.skipif(not COOL0C_WAT.exists(), reason="cool0c.wat 이 아직 없다")


def token_arena_end(n: int) -> int:
    """소스 n 바이트일 때 토큰 아레나가 끝나는 주소. cool0c 의 compile() 과 같다."""
    return align_up(SRC_ADDR + n, 4) + 4 + (n + 1) * 28


def first_collision() -> int:
    """토큰 아레나만으로 스크래치에 닿는 가장 작은 소스 길이."""
    lo, hi = 1, BOOTSTRAP_SCRATCH
    while lo < hi:
        mid = (lo + hi) // 2
        if token_arena_end(mid) >= BOOTSTRAP_SCRATCH:
            hi = mid
        else:
            lo = mid + 1
    return lo


COLLISION = first_collision()


def padded(n: int) -> bytes:
    """길이가 정확히 n 인 유효한 프로그램."""
    prog = b"fn f(n: u32) -> u32 { return n + 1; }"
    assert n >= len(prog)
    return b" " * (n - len(prog)) + prog


def _wat() -> bytes:
    return bytes(wasmtime.wat2wasm(COOL0C_WAT.read_text("ascii")))


def test_the_collision_point_is_a_real_boundary():
    """한 바이트 아래는 닿지 않고, 그 지점부터 닿는다."""
    assert token_arena_end(COLLISION - 1) < BOOTSTRAP_SCRATCH
    assert token_arena_end(COLLISION) >= BOOTSTRAP_SCRATCH


@pytest.mark.parametrize("d", [-1, 0, 1])
def test_the_oracle_rejects_at_the_boundary(d):
    """경계 바로 아래도 거절된다 -- 노드와 표가 토큰 뒤에 더 붙기 때문이다."""
    assert reference_compile(padded(COLLISION + d)) == (1, TOO_LARGE)


def test_the_oracle_takes_a_source_at_half_the_limit():
    status, out = reference_compile(padded(COLLISION // 2))
    assert status == STATUS_OK, out.decode("ascii", "replace")


@needs_wat
@pytest.mark.parametrize("d", [-1, 0, 1])
def test_both_implementations_agree_at_the_boundary(d):
    src = padded(COLLISION + d)
    assert run_compiler(_wat(), src) == reference_compile(src)


@needs_wat
def test_both_agree_on_a_dense_source_that_crosses_the_limit():
    """공백이 아니라 진짜 코드로 넘긴다 -- 토큰과 노드가 훨씬 빨리 자란다."""
    unit = b"fn f%d(n: u32) -> u32 { let a: u32 = n + %d; return a * 3; }\n"
    body = b"".join(unit % (i, i) for i in range(20000))
    assert reference_compile(body)[0] != STATUS_OK, "이 크기는 거절돼야 한다"
    assert run_compiler(_wat(), body) == reference_compile(body)


def test_the_compiler_has_room_to_grow():
    """cool0c.cool0 자신이 한계에서 얼마나 떨어져 있는가.

    한때 0.7% 였고 아무도 몰랐다 -- 리팩터링 한 번이면 컴파일러가 자기 자신을
    거절했을 것이다. 이 시험은 그 여유를 눈에 보이게 둔다.
    """
    src = (SRC_DIR / "cool0c.cool0").read_bytes()
    assert reference_compile(src)[0] == STATUS_OK
    used = token_arena_end(len(src))
    headroom = 1.0 - used / BOOTSTRAP_SCRATCH
    assert headroom > 0.25, (
        f"cool0c.cool0 이 한계의 {100 * (1 - headroom):.0f}% 를 쓰고 있다. "
        f"스크래치 영역을 올리거나 소스를 줄여야 한다"
    )
