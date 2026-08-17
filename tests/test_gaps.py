"""한 번도 실행된 적이 없던 경로들 (implementation.md §8).

참조 구현에 커버리지를 걸어 보면 어떤 줄은 어떤 테스트에도 걸리지 않았다. 그런 줄은
"두 구현이 일치한다"가 **아직 증명되지 않은** 곳이다 -- 특히 주소를 취한 매개변수의
프롤로그(implementation.md §5)는 방출기의 열여섯 줄이 통째로 죽어 있었다.

여기서는 그 자리를 하나씩 겨냥해서 세 구현을 모두 맞춰 본다:

    P = cool0.py      A = wat2wasm(cool0c.wat)      B = A(cool0c.cool0)
"""

from __future__ import annotations

import pathlib

import pytest
import wasmtime

from conftest import ENGINE, run_compiler
from gaps import GAP_PROGRAMS, GAPS
from cool0.cool0 import STATUS_OK, compile as reference_compile

SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "cool0"
COOL0C_WAT = SRC_DIR / "cool0c.wat"
COOL0C = SRC_DIR / "cool0c.cool0"

needs_wat = pytest.mark.skipif(not COOL0C_WAT.exists(), reason="cool0c.wat 이 아직 없다")


@pytest.fixture(scope="module")
def three():
    """(A, B) -- WAT 에서 나온 컴파일러와 그것이 만든 컴파일러."""
    a = bytes(wasmtime.wat2wasm(COOL0C_WAT.read_text("ascii")))
    status, b = run_compiler(a, COOL0C.read_bytes())
    assert status == STATUS_OK
    return a, b


def agree(three, src: bytes):
    a, b = three
    p = reference_compile(src)
    assert run_compiler(a, src) == p, "A disagrees with the oracle"
    assert run_compiler(b, src) == p, "B disagrees with the oracle"
    return p


# --- 한 번도 나온 적 없던 진단들 ---------------------------------------------


@needs_wat
@pytest.mark.parametrize("src,why", GAPS, ids=[w for _, w in GAPS])
def test_unseen_diagnostics_agree(three, src, why):
    status, out = agree(three, src)
    assert status != STATUS_OK, "이건 거절당해야 하는 소스다"
    assert out.endswith(b"\n")


# --- 한 번도 컴파일된 적 없던 프로그램들 ---------------------------------------


@needs_wat
@pytest.mark.parametrize("src,why", GAP_PROGRAMS, ids=[w for _, w in GAP_PROGRAMS])
def test_unseen_programs_agree(three, src, why):
    status, out = agree(three, src.encode("ascii"))
    assert status == STATUS_OK, out.decode("ascii", "replace")
    wasmtime.Module(ENGINE, out)


# --- 그 코드가 실제로 옳은가 (일치한다고 맞는 것은 아니다) ----------------------


def run(src: str, fn: str, *args):
    status, wasm = reference_compile(src.encode("ascii"))
    assert status == STATUS_OK, wasm.decode("ascii", "replace")
    store = wasmtime.Store(ENGINE)
    inst = wasmtime.Instance(store, wasmtime.Module(ENGINE, wasm), [])
    return inst.exports(store)[fn](store, *args)


def test_address_taken_scalar_parameter():
    """implementation.md §6 -- 주소를 취한 매개변수는 프롤로그에서 프레임으로 복사된다."""
    src = "fn peek(x: &u32) -> u32 { return x.^; } fn f(n: u32) -> u32 { return peek(&n); }"
    assert run(src, "f", 12345) == 12345


def test_address_taken_bool_parameter():
    src = ("fn peek(x: &bool) -> bool { return x.^; } "
           "fn f(c: bool) -> bool { return peek(&c); }")
    assert [run(src, "f", v) for v in (0, 1)] == [0, 1]


def test_address_taken_slice_parameter():
    src = ("fn slen(s: &[]u8) -> u32 { return s.^.len; } "
           "fn f(a: []u8) -> u32 { return slen(&a); }")
    assert run(src, "f", 0x1000, 42) == 42


def test_two_address_taken_parameters():
    src = ("fn two(x: &u32, y: &u32) -> u32 { return x.^ * 10 + y.^; } "
           "fn f(a: u32, b: u32) -> u32 { return two(&a, &b); }")
    assert run(src, "f", 3, 4) == 34


def test_break_inside_a_match_inside_a_loop():
    src = """
enum E { A, B }
fn f() -> u32 {
    let e: E = E.A;
    let mut n: u32 = 0;
    for { n += 1; match e { A => { break; } B => { } } }
    return n;
}
"""
    assert run(src, "f") == 1


def test_nested_match():
    src = """
enum E { A, B(u32) }
fn g(x: &E, y: &E) -> u32 {
    match x.^ {
        A => { match y.^ { A => { return 1; } B(v) => { return v; } } }
        B(v) => { match y.^ { A => { return v; } B(w) => { return v + w; } } }
    }
}
fn f() -> u32 { let a: E = E.B(20); let b: E = E.B(3); return g(&a, &b); }
"""
    assert run(src, "f") == 23


def test_enum_with_a_slice_payload_reads_back():
    src = """
enum T { End, W([]u8, u32) }
fn f() -> u32 {
    let t: T = T.W("abcd", 7);
    match t { End => { return 0; } W(s, n) => { return s.len * 100 + n * 10 + s[0]; } }
}
"""
    assert run(src, "f") == 400 + 70 + ord("a")


def test_empty_struct_local():
    assert run("struct S { } fn f() -> u32 { let s: S = S{ }; return 9; }", "f") == 9


def test_slice_of_structs_and_borrowed_element():
    src = """
struct R { id: u32, n: i32 }
fn take(p: &mut R) { p.^.id += 1; }
fn f(a: []mut R) -> u32 { take(&mut a[0]); return a[0].id; }
"""
    status, wasm = reference_compile(src.encode("ascii"))
    assert status == STATUS_OK
    store = wasmtime.Store(ENGINE)
    inst = wasmtime.Instance(store, wasmtime.Module(ENGINE, wasm), [])
    ex = inst.exports(store)
    ex["memory"].write(store, b"\x00" * 16, 0x7000)
    assert ex["f"](store, 0x7000, 2) == 1


# --- 섀도 스택의 바닥 (implementation.md §7) ---------------------------------------

DEEP = """
struct Frame { a: u32, b: u32 }
fn depth(n: u32) -> u32 {
    let mut fr: Frame = Frame{ a: n, b: 7 };
    if n == 0 { return fr.a; }
    return depth(n - 1) + fr.b;
}
"""


def deep_call(n: int):
    """depth(n) 을 부르고, 호스트 ABI 슬롯이 성했는지 함께 돌려준다."""
    status, wasm = reference_compile(DEEP.encode("ascii"))
    assert status == STATUS_OK
    store = wasmtime.Store(ENGINE)
    inst = wasmtime.Instance(store, wasmtime.Module(ENGINE, wasm), [])
    ex = inst.exports(store)
    mem = ex["memory"]
    mem.write(store, bytes([0xAA] * 16), 0)
    try:
        ex["depth"](store, n)
    except wasmtime.Trap:
        return "trap", bytes(mem.read(store, 0, 8))
    return "ok", bytes(mem.read(store, 0, 8))


def test_shadow_stack_holds_to_the_documented_floor():
    """프레임 8바이트 * 510 = 4080 = 0x1000 - 0x10. 거기까지는 성해야 한다."""
    for n in (100, 400, 509):
        outcome, abi = deep_call(n)
        assert outcome == "ok", n
        assert abi == bytes([0xAA] * 8), n


def test_shadow_stack_overflow_corrupts_the_abi_before_it_traps():
    """implementation.md §6 은 "넘치면 결국 트랩"이라 하지만, 그 전에 out_ptr/out_len 을 덮는다.

    이것은 문서화된 동작이 아니라 문서가 부정확했던 것이다. 여기서 그 창을 못박아
    둔다 -- 고치든 명세를 고치든, 먼저 재현이 있어야 한다.
    """
    # 511 프레임: sp 가 8 까지 내려가 예약 슬롯(0x08)을 덮는다. 조용하다
    outcome, abi = deep_call(510)
    assert outcome == "ok"
    assert abi == bytes([0xAA] * 8), "아직 ABI 슬롯은 성하다"

    # 512 프레임: sp 가 0 이 되어 out_ptr 과 out_len 을 덮어쓴다. 그래도 "성공"이다
    outcome, abi = deep_call(511)
    assert outcome == "ok"
    assert abi != bytes([0xAA] * 8), "여기서 호스트 ABI 가 조용히 깨진다"

    # 513 프레임부터는 주소가 음수가 되어 트랩한다
    for n in (512, 600, 1000):
        outcome, _ = deep_call(n)
        assert outcome == "trap", n


def test_compound_shift_assignment_runs():
    src = ("fn f(a: u32, n: u32) -> u32 { let mut x: u32 = a; x <<= n; x >>= 1; return x; }"
           "fn g(a: i32, n: u32) -> i32 { let mut x: i32 = a; x >>= n; return x; }")
    assert run(src, "f", 1, 5) == 16
    assert run(src, "g", -16, 2) == -4          # i32 는 산술 시프트다 (language.md §5)
    src2 = ("fn f(a: u32, n: u32) -> u32 { let mut x: u32 = a; x >>= n; return x; }")
    assert run(src2, "f", -1, 4) & 0xFFFFFFFF == 0x0FFFFFFF   # u32 는 논리 시프트


def test_compound_shift_on_a_struct_field():
    src = """
struct S { a: u32, b: i32 }
fn f() -> u32 { let mut s: S = S{ a: 8, b: 0 - 16 }; s.a <<= 2; return s.a; }
fn g() -> i32 { let mut s: S = S{ a: 0, b: 0 - 16 }; s.b >>= 2; return s.b; }
"""
    assert run(src, "f") == 32
    assert run(src, "g") == -4


def test_the_command_line_host(tmp_path):
    """__main__ 은 컴파일러가 아니라 호스트다. 그래도 도는지는 봐야 한다."""
    import subprocess
    import sys

    good = tmp_path / "a.cool0"
    good.write_text("fn add(a: i32, b: i32) -> i32 { return a + b; }", encoding="ascii")
    out = tmp_path / "a.wasm"
    r = subprocess.run([sys.executable, str(SRC_DIR / "cool0.py"), str(good), str(out)],
                       capture_output=True)
    assert r.returncode == 0, r.stderr
    assert out.read_bytes() == reference_compile(good.read_bytes())[1]

    bad = tmp_path / "b.cool0"
    bad.write_text("fn f() -> i32 { }", encoding="ascii")
    r = subprocess.run([sys.executable, str(SRC_DIR / "cool0.py"), str(bad),
                        str(tmp_path / "b.wasm")], capture_output=True)
    assert r.returncode == 1
    assert b"must return a value" in r.stderr

    r = subprocess.run([sys.executable, str(SRC_DIR / "cool0.py")], capture_output=True)
    assert r.returncode == 2
    assert b"usage:" in r.stderr
