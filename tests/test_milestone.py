"""최종 마일스톤. 등식 하나다.

    P = cool0.py(cool0c.cool0)          오라클이 낸 바이트
    B = A(cool0c.cool0)                 A = wat2wasm(cool0c.wat)
    C = B(cool0c.cool0)                 B 가 자기 자신을 다시 낸다

    B == C == P

이것 하나가 참이면 다음이 전부 참이다.

    B == C   전사가 충실하다. 손으로 옮겨 적은 WAT 가 cool0 로 쓴 원본과 같은
             코드를 낸다 (README 의 고정점)
    C == P   명세가 완전하다. 따로 쓴 두 구현이 바이트 하나까지 같은 답을 낸다.
             애매하게 남은 곳이 있으면 여기서 깨진다 (implementation.md §8)
    입력     cool0c.cool0 자신이다. 앞으로 존재할 가장 까다로운 cool0 프로그램이며
             struct, enum, match, 슬라이스, 대여, unsafe 를 전부 쓴다

신뢰 사슬은 `wat2wasm` 과 wasm 런타임뿐이다. `cool0.py` 는 사슬에 없다 -- 등식의
오른쪽에서 심판만 본다.

    uv run pytest tests/test_milestone.py
"""

from __future__ import annotations

import pathlib

import pytest
import wasmtime

from conftest import ENGINE, run_compiler, needs_current_wat
from cool0.cool0 import STATUS_OK, compile as reference_compile

SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "cool0"
COOL0C_COOL0 = SRC_DIR / "cool0c.cool0"
COOL0C_WAT = SRC_DIR / "cool0c.wat"


def source() -> bytes:
    return COOL0C_COOL0.read_bytes()


def stage_a() -> bytes:
    """A -- 손으로 쓴 WAT 를 wasm 으로. 신뢰 사슬의 시작이자 전부다."""
    return bytes(wasmtime.wat2wasm(COOL0C_WAT.read_text("ascii")))


def compiled(compiler: bytes, src: bytes) -> bytes:
    status, out = run_compiler(compiler, src)
    assert status == STATUS_OK, out.decode("ascii", "replace")
    return out


needs_cool0 = pytest.mark.skipif(
    not COOL0C_COOL0.exists(), reason="cool0c.cool0 이 아직 없다 (부트스트랩 2단계)"
)
needs_wat = pytest.mark.skipif(
    not COOL0C_WAT.exists(), reason="cool0c.wat 이 아직 없다 (부트스트랩 3단계)"
)


# ============================================================================
# 마일스톤
# ============================================================================


@needs_cool0
@needs_current_wat
def test_the_milestone():
    """B == C == P."""
    src = source()

    status, p = reference_compile(src)
    assert status == STATUS_OK, p.decode("ascii", "replace")
    b = compiled(stage_a(), src)
    c = compiled(b, src)

    assert b == c, "전사가 어긋났다 -- cool0c.wat 이 cool0c.cool0 과 다른 코드를 낸다"
    assert c == p, "명세에 구멍이 있다 -- 두 구현의 출력이 갈린다"


# ============================================================================
# 중간 관문. 순서대로 켜지며, 깨졌을 때 어디가 깨졌는지 말해 준다
# ============================================================================


@needs_cool0
def test_checkpoint_1_the_oracle_accepts_the_compiler_source():
    """cool0c.cool0 이 명세를 지키는 cool0 프로그램이다."""
    status, out = reference_compile(source())
    assert status == STATUS_OK, out.decode("ascii", "replace")


@needs_cool0
def test_checkpoint_2_self_reproduction_without_any_wat():
    """WAT 없이도 셀프 호스팅이 먼저 증명된다.

    `cool0.py` 로 컴파일한 cool0c 가 자기 소스를 다시 컴파일해서 자기 자신과 같은
    바이트를 낸다. 여기까지가 "cool0c 가 옳은가" 이고, 그다음이 "전사가 옳은가" 다.
    두 질문을 갈라 놓는 것이 이 관문의 값이다.
    """
    src = source()
    b_py = reference_compile(src)[1]
    assert compiled(b_py, src) == b_py


@needs_cool0
def test_checkpoint_3_the_compiler_agrees_with_the_oracle_on_the_corpus():
    """cool0c 가 코퍼스 전부에서 오라클과 바이트가 같다. 진단까지."""
    from corpus import DIAGNOSTICS, PROGRAMS

    b_py = reference_compile(source())[1]
    for name, text in PROGRAMS:
        src = text.encode("ascii")
        assert run_compiler(b_py, src) == reference_compile(src), name
    for name, src in DIAGNOSTICS:
        assert run_compiler(b_py, src) == reference_compile(src), name


@needs_current_wat
def test_checkpoint_4_the_transcription_is_faithful_on_the_corpus():
    """A 가 코퍼스에서 오라클과 일치한다. 여기가 통과하면 남은 것은 자기 자신뿐이다."""
    from corpus import DIAGNOSTICS, PROGRAMS

    a = stage_a()
    for name, text in PROGRAMS:
        src = text.encode("ascii")
        assert run_compiler(a, src) == reference_compile(src), name
    for name, src in DIAGNOSTICS:
        assert run_compiler(a, src) == reference_compile(src), name


# ============================================================================
# 마일스톤이 아닌 것 -- 여기 없는 것은 목표가 아니다
# ============================================================================
#
#   속도, 크기          최적화는 명세가 금지한다 (implementation.md)
#   cool0.py 의 퇴출    오라클로 남는다. 사슬에는 원래 없었다
#   cool1              cool0 이 얼어붙은 뒤의 일이다
#   좋은 오류 메시지     진단은 바이트가 같기만 하면 된다 (implementation.md §9)
