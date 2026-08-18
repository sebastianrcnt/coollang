"""두 구현이 같은 만큼의 메모리를 잡는가 (implementation.md §7, §8).

메모리 한계는 언어의 것이 아니라 대상의 것이지만, **두 구현이 같은 지점에서
거절해야 한다.** 안 그러면 큰 소스에서만 갈리고 그것이 §8 이 금지하는 상황이다.
`cool0.py` 는 그래서 자기 방식으로 할당하지 않고 `cool0c` 의 범프 순서를 산술로
흉내 낸다.

그 흉내가 어긋난 적이 있다. 다섯 군데였다:

- 토큰 폭 32 -> 36 (Kw 필드)
- 전체 프로그램 토큰 아레나 제거와 S1 선언 작업장 재사용
- 이름 아레나 셋 (gh #5 A)
- 노드 아레나가 전체가 아니라 선언부 + 가장 큰 본문 (gh #5 C)
- `NameI` 아레나가 둘이 아니라 셋 (스칼라 enum 때 생긴 것, 그 전부터 있었다)

다섯 다 안 빨개졌다. 경계를 견주는 시험이 통째로 `@needs_current_wat` 뒤에 있어서
전사가 뒤처진 동안 꺼져 있었기 때문이다.

그래서 여기서는 경계를 재지 않는다. **힙 끝을 직접 견준다** -- 아레나 하나가
어긋나면 여덟 줄짜리 프로그램에서도 즉시 드러나고, 6 MB 짜리 소스를 만들 필요가
없다. WAT 도 필요 없다.
"""

from __future__ import annotations

import pathlib

import pytest

from conftest import ENGINE, run_compiler
from cool0.cool0 import STATUS_OK, bootstrap_heap_end, compile as reference_compile
from cool0.cool0 import lex

import wasmtime

SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "cool0"
COOL0C = SRC_DIR / "cool0c.cool0"
G_HEAP = 0x0050


@pytest.fixture(scope="module")
def cc():
    """오라클이 만든 cool0c 인스턴스. 전사가 뒤처져 있어도 돈다."""
    status, wasm = reference_compile(COOL0C.read_bytes())
    assert status == STATUS_OK, wasm.decode("ascii", "replace")
    store = wasmtime.Store(ENGINE)
    inst = wasmtime.Instance(store, wasmtime.Module(ENGINE, wasm), [])
    return store, inst.exports(store)


def cool0c_heap_end(cc, src: bytes) -> int:
    from cool0.cool0 import SRC_ADDR

    store, ex = cc
    mem = ex["memory"]
    mem.write(store, src, SRC_ADDR)
    status = ex["compile"](store, len(src))
    assert status == STATUS_OK, "이 소스는 컴파일에 성공해야 한다"
    return int.from_bytes(bytes(mem.read(store, G_HEAP, G_HEAP + 4)), "little")


def oracle_heap_end(src: bytes) -> int:
    from cool0.cool0 import _parse_declarations

    decls, ntok, nb, nid, nmax, str_bytes = _parse_declarations(src)
    return bootstrap_heap_end(
        len(src), ntok, decls, nb, nid, nmax, str_body_bytes=str_bytes,
    )


PROGRAMS = [
    "fn f() { }",
    "fn f(a: i32, b: i32) -> i32 { return a + b; }",
    "struct S { a: u32, b: u32 }\nfn f(p: &S) -> u32 { return p.^.a + p.^.b; }",
    "enum E { A, B, C }\nfn f(e: E) -> u32 { return match e { A | B => 1, C => 2, }; }",
    "enum T { End, W([]u8, u32) }\nfn f() -> u32 {\n"
    '    let t: T = T.W("abcd", 7);\n'
    "    match t { End => { return 0; } W(s, n) => { return s.len + n; } }\n}",
    "const N: u32 = 3;\nconst M: u32 = N * 4;\nfn f() -> u32 { return M; }",
    "fn f() -> u32 {\n    let mut n: u32 = 0;\n"
    "    for let mut i: u32 = 0; i < 10; i += 1 { n += i; }\n    return n;\n}",
    'fn f() -> u32 { let s: []u8 = "hello"; return s.len; }',
]


@pytest.mark.parametrize("src", PROGRAMS, ids=range(len(PROGRAMS)))
def test_both_implementations_reserve_the_same_heap(cc, src):
    """오라클의 산술이 cool0c 의 실제 범프 끝과 바이트까지 같아야 한다."""
    b = src.encode("ascii")
    got, want = cool0c_heap_end(cc, b), oracle_heap_end(b)
    assert got == want, (
        f"cool0c 은 {got:,} 까지 잡았는데 오라클은 {want:,} 로 셈했다 "
        f"({got - want:+,} 바이트). implementation.md §7 의 범프 순서 하나가 "
        f"두 구현에서 다르다"
    )


def test_the_compiler_itself_reserves_what_the_oracle_says(cc):
    """가장 큰 입력, 즉 컴파일러 자신으로도 같아야 한다."""
    src = COOL0C.read_bytes()
    got, want = cool0c_heap_end(cc, src), oracle_heap_end(src)
    assert got == want, f"{got:,} != {want:,} ({got - want:+,})"
