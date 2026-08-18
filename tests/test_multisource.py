"""소스 여럿 (gh #5 B, implementation.md §7).

`cool0c` 는 이제 `(ptr, len)` 쌍 여러 개를 받는다. 정의는 한 줄이다:

    compile_n([a, b, c])  ==  compile(a + "\\n" + b + "\\n" + c)

그래서 줄 번호가 버퍼를 넘어 이어지고, 진단은 여전히 `L:C: 문구` 이며, 토큰에도
노드에도 버퍼 번호가 실리지 않는다. cool0 에는 찍을 파일 이름이 없으므로 어느
파일인지는 호스트가 되짚는다.

여기서 보는 것은 그 등식이 **바이트까지** 성립하는가다. 성립하면 다중 소스는
새 의미론이 아니라 같은 의미론의 다른 입력 통로이고, 기존 시험 전부가 그대로
다중 소스를 덮는다.
"""

from __future__ import annotations

import pathlib

import pytest

from conftest import run_compiler, run_compiler_n
from cool0.cool0 import STATUS_OK, compile as reference_compile, compile_many

SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "cool0"
COOL0C = SRC_DIR / "cool0c.cool0"

NEWLINE = chr(10).encode()


@pytest.fixture(scope="module")
def cc() -> bytes:
    """오라클이 만든 cool0c. WAT 가 뒤처져 있어도 이건 돈다."""
    status, wasm = reference_compile(COOL0C.read_bytes())
    assert status == STATUS_OK, wasm.decode("ascii", "replace")
    return wasm


SPLITS = [
    ["fn f() -> u32 { return g() + 1; }", "fn g() -> u32 { return 41; }"],
    ["struct P { x: u32, y: u32 }",
     "fn area(p: &P) -> u32 { return p.^.x * p.^.y; }",
     "fn f() -> u32 { let p: P = P{ x: 6, y: 7 }; return area(&p); }"],
    ["enum E { A, B }", "fn f(e: E) -> u32 { match e { A => 1, B => 2, } }"],
    ["const N: u32 = 10;", "", "fn f() -> u32 { return N; }"],
    ["fn f() -> u32 {", "    return 3;", "}"],
]


@pytest.mark.parametrize("parts", SPLITS, ids=range(len(SPLITS)))
def test_the_oracle_treats_many_buffers_as_one_joined_source(parts):
    """오라클 쪽 정의. 이건 동어반복에 가깝지만, 정의를 코드로 못박아 둔다."""
    srcs = [p.encode("ascii") for p in parts]
    assert compile_many(srcs) == reference_compile(NEWLINE.join(srcs))


@pytest.mark.parametrize("parts", SPLITS, ids=range(len(SPLITS)))
def test_cool0c_compiles_many_buffers_to_the_same_bytes(cc, parts):
    """진짜 주장. 버퍼를 나눠 넣어도 이어 붙인 것과 같은 바이트가 나온다."""
    srcs = [p.encode("ascii") for p in parts]
    assert run_compiler_n(cc, srcs) == reference_compile(NEWLINE.join(srcs))


@pytest.mark.parametrize("parts", SPLITS, ids=range(len(SPLITS)))
def test_one_buffer_through_compile_n_matches_the_old_entry_point(cc, parts):
    """`compile(len)` 은 한 칸짜리 표를 쓰는 껍데기다 -- 경로가 하나여야 한다."""
    joined = NEWLINE.join(p.encode("ascii") for p in parts)
    assert run_compiler_n(cc, [joined]) == run_compiler(cc, joined)


def test_a_diagnostic_in_a_later_buffer_counts_lines_across_the_split(cc):
    """줄 번호는 버퍼를 넘어 이어진다. 두 번째 버퍼의 오류가 그것을 보여 준다."""
    parts = [b"fn a() { }", b"fn b() { let x: u32 = zz; }"]
    status, out = run_compiler_n(cc, parts)
    assert status != STATUS_OK
    assert out == b"2:23: unknown name `zz`" + NEWLINE
    assert (status, out) == reference_compile(NEWLINE.join(parts))


def test_buffers_do_not_have_to_be_adjacent(cc):
    """호스트가 버퍼를 붙여 놓을 의무는 없다.

    `run_compiler_n` 이 일부러 사이를 띄워 놓는다. 힙은 가장 높은 끝 뒤에서
    시작하므로, 띄워 놓아도 아레나가 소스를 덮지 않는다.
    """
    parts = [b"fn f() -> u32 { return g(); }", b"fn g() -> u32 { return 5; }"]
    assert run_compiler_n(cc, parts) == reference_compile(NEWLINE.join(parts))


def test_the_compiler_splits_into_buffers_and_still_reproduces_itself(cc):
    """cool0c.cool0 자신을 여러 버퍼로 잘라 넣어도 같은 컴파일러가 나온다.

    이게 gh #5 B 가 결국 하려는 일이다 -- cool1 컴파일러를 파일 여러 개로 나눠
    쓰는 것. 자를 자리는 최상위 선언 경계여야 하므로 `\\nfn ` 에서 자른다.
    """
    whole = COOL0C.read_bytes()
    marker = NEWLINE + b"fn "
    pieces = whole.split(marker)
    # 다시 이어 붙였을 때 원본이 되도록 표식을 되살린다
    parts = [pieces[0]] + [b"fn " + p for p in pieces[1:]]
    assert NEWLINE.join(parts) == whole, "자르기가 되돌릴 수 있어야 한다"
    assert len(parts) > 100, len(parts)

    status, out = run_compiler_n(cc, parts)
    assert status == STATUS_OK, out.decode("ascii", "replace")
    assert out == cc, "여러 버퍼로 넣어도 같은 컴파일러 바이트가 나와야 한다"
