"""명세에 적힌 숫자가 코드와 같은지.

`implementation.md` 는 주소와 한계를 본문에 **숫자로** 적는다. 그 숫자들이 코드에서
따로 살고 있어서, 한 번은 `S1` 을 `0x0080_0000` 에서 `0x00E0_0000` 으로 옮기고도
문서를 안 고쳤다. 그 뒤 문서를 읽고 쓴 이슈가 "천장 289 KB" 라는 틀린 전제 위에
서게 됐다 -- 실제로는 434 KB 였다. 아무 시험도 안 빨개졌다.

여기서 대조하는 것은 **문서에 실제로 적힌 문자열**이다. 상수를 문서에서 읽어와
비교하는 게 아니라, 문서에 그 숫자가 그렇게 쓰여 있는지를 본다. 그래야 상수를
바꾸면 이 시험이 빨개지고, 고치는 방법이 문서를 여는 것밖에 없다.

파일 크기처럼 커밋마다 흔들리는 숫자는 여기 넣지 않는다. 그런 건 문서에도
숫자로 안 적혀 있어야 한다.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from cool0.cool0 import (
    BOOTSTRAP_SCRATCH,
    MEM_PAGES,
    SHADOW_FLOOR,
    SHADOW_TOP,
    SRC_ADDR,
    STATUS_OK,
    compile as reference_compile,
)
from test_limits import COLLISION, padded

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "src" / "cool0" / "spec" / "implementation.md"
COOL0C = ROOT / "src" / "cool0" / "cool0c.cool0"

TEXT = SPEC.read_text("utf-8")
# 문서는 80칸에서 접히므로 인용된 문구가 줄바꿈을 품는다. 찾을 때는 편다.
FLAT = re.sub(r"\s+", " ", TEXT)


def hexes(n: int) -> list[str]:
    """cool0 와 wat 과 문서가 주소를 적는 방식들."""
    return [f"0x{n:08X}", f"0x{n:08x}", f"0x{n >> 16:04X}_{n & 0xFFFF:04X}",
            f"0x{n >> 16:04x}_{n & 0xFFFF:04x}", hex(n), f"0x{n:X}"]


def spec_mentions(n: int) -> bool:
    return any(h in TEXT for h in hexes(n))


@pytest.mark.parametrize(
    "name, value",
    [
        ("SRC_ADDR", SRC_ADDR),
        ("S1 (BOOTSTRAP_SCRATCH)", BOOTSTRAP_SCRATCH),
        ("SHADOW_FLOOR", SHADOW_FLOOR),
        ("SHADOW_TOP", SHADOW_TOP),
    ],
)
def test_the_spec_prints_the_address_the_code_uses(name, value):
    """§7 메모리 지도의 주소는 코드의 상수와 같아야 한다."""
    assert spec_mentions(value), (
        f"{name} = {value:#010x} 인데 implementation.md 에 그 주소가 없다. "
        f"상수를 옮겼으면 §7 의 지도와 '크기 한계' 절을 같이 고쳐라"
    )


def test_the_spec_does_not_still_print_the_old_scratch_address():
    """`S1` 이 8 MiB 에 있던 시절의 주소가 남아 있으면 안 된다.

    이 한 줄이 이슈 하나를 틀린 전제 위에 세웠다.
    """
    assert not spec_mentions(0x0080_0000), (
        "implementation.md 에 옛 S1 주소 0x0080_0000 이 남아 있다"
    )


def test_the_spec_prints_the_page_count_the_code_emits():
    """`min = max = 512` -- 내보내는 모듈의 메모리 크기."""
    assert re.search(rf"min = max = {MEM_PAGES}\b", TEXT), (
        f"MEM_PAGES = {MEM_PAGES} 인데 §4 표가 그렇게 안 적고 있다"
    )
    assert f"{MEM_PAGES * 64 // 1024} MiB" in TEXT


def test_the_spec_prints_the_size_limit_that_the_oracle_actually_enforces():
    """§7 은 소스 길이 하나를 토큰 한계라고 적지 않아야 한다."""
    assert "프로그램 전체 토큰 아레나는 없다" in TEXT
    assert "선언 하나의 토큰 작업장" in TEXT


def test_the_spec_is_right_that_exactly_one_function_needs_a_frame():
    """§6 의 주장: `cool0c.cool0` 에서 프레임을 쓰는 함수는 `compile_n` 뿐이다.

    이건 숫자가 아니라 **구조에 대한 주장**이고, 그래서 조용히 틀려질 수 있다.
    집합체 지역변수 하나나 `&x` 하나가 새로 생기면 프레임이 하나 더 는다. 그때
    §6 의 '재귀는 깊이 한계가 지킨다' 는 설명도 같이 흔들린다.
    """
    from cool0 import cool0 as c0

    framed: list[str] = []
    original = c0.Emitter.assign_storage

    def watched(self, fb):
        original(self, fb)
        if fb.frame_size:
            framed.append(fb.info.name)

    c0.Emitter.assign_storage = watched
    try:
        status, out = c0.compile(COOL0C.read_bytes())
    finally:
        c0.Emitter.assign_storage = original

    assert status == STATUS_OK, out.decode("ascii", "replace")
    assert framed == ["compile_n"], (
        f"프레임을 쓰는 함수가 {framed} 다. §6 은 `compile_n` 하나뿐이라고 적고 있다"
    )


@pytest.mark.parametrize(
    "quoted",
    [
        "program is too large for the compiler's memory",
        "string literals do not fit below the shadow stack",
        "expression nests too deeply",
        "block nests too deeply",
        "type nests too deeply",
    ],
)
def test_every_diagnostic_the_spec_quotes_exists_in_the_oracle(quoted):
    """문서가 진단 문구를 인용하면 그 문구가 실제로 나와야 한다.

    한 번은 문서에 `expression is too deep` 이라고 적었는데 오라클은
    `expression nests too deeply` 를 냈다. 인용부호 안은 사양이지 요약이 아니다.
    """
    assert quoted in FLAT, f"이 시험이 문서에 없는 문구를 지키고 있다: {quoted}"
    source = (ROOT / "src" / "cool0" / "cool0.py").read_text("utf-8")
    assert quoted in source, f"문서가 인용한 진단이 cool0.py 에 없다: {quoted}"


# --- 메모리 지도가 서로 겹치지 않는가 (implementation.md §7) --------------------


def compiler_regions() -> list[tuple[str, int]]:
    """컴파일러가 자기 실행 중에 쓰는 영역들, 주소 순으로."""
    from cool0.cool0 import (BOOTSTRAP_SCRATCH, OUT_ADDR, RODATA_ADDR,
                             SHADOW_FLOOR, SHADOW_TOP, SRC_ADDR, TOKEN_SCRATCH_END)

    return [
        ("SRC", SRC_ADDR),
        ("S1", BOOTSTRAP_SCRATCH),
        ("S2", TOKEN_SCRATCH_END),
        ("OUT", OUT_ADDR),
        ("RODATA", RODATA_ADDR),
        ("SHADOW_FLOOR", SHADOW_FLOOR),
        ("SHADOW_TOP", SHADOW_TOP),
    ]


def test_the_memory_map_is_strictly_ordered():
    """§7 지도의 주소가 적힌 순서대로 올라가야 한다."""
    regions = compiler_regions()
    for (an, a), (bn, b) in zip(regions, regions[1:]):
        assert a < b, f"{an}({a:#x}) 가 {bn}({b:#x}) 보다 아래가 아니다"


def test_nothing_sits_inside_the_range_the_heap_grows_through():
    """힙은 소스 뒤에서 `S1` 까지 자란다. 그 사이에 다른 영역이 있으면 안 된다.

    한동안 있었다. `OUT` 이 `0x0101_0000`(16.8 MiB)에 있었는데, 그때 `S1` 은
    `0x0080_0000`(8.4 MiB)이라 `OUT` 이 힙 **위**였다. `S1` 을 16 배로 올리면서
    (gh #5 0단계) `OUT` 이 힙 한가운데로 들어왔고, 아무 시험도 그것을 보지 않았다.

    실제로 깨뜨려 보이지는 못했다 -- 힙이 32 MiB 까지 가는 프로그램으로도 출력이
    성했다. 그 프로그램이 겹치는 구간을 안 건드렸을 뿐이다. 겹치면 안 되는 두
    영역이 겹쳐 있다는 것만으로 결함이고, 그것을 여기서 막는다.
    """
    from cool0.cool0 import BOOTSTRAP_SCRATCH, SRC_ADDR

    inside = [
        f"{name}({addr:#x})"
        for name, addr in compiler_regions()
        if SRC_ADDR < addr < BOOTSTRAP_SCRATCH
    ]
    assert not inside, (
        f"힙은 {SRC_ADDR:#x} 부터 {BOOTSTRAP_SCRATCH:#x} 까지 자라는데 그 사이에 "
        f"{', '.join(inside)} 가 있다"
    )


def test_every_region_has_room_the_spec_can_state():
    """영역마다 다음 영역까지의 거리가 있어야 한다 -- 0 이면 쓸 자리가 없다."""
    regions = compiler_regions()
    for (an, a), (bn, b) in zip(regions, regions[1:]):
        assert b - a >= 1 << 20, f"{an}..{bn} 이 {b - a:,} 바이트뿐이다"
