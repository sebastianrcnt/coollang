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

from conftest import ENGINE, run_compiler, needs_current_wat
from gaps import GAP_DIAGNOSTICS, GAP_PROGRAMS, GAPS
from cool0.cool0 import (
    SHADOW_FLOOR,
    SHADOW_TOP,
    STATUS_OK,
    compile as reference_compile,
)

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


# --- 오라클만으로 확인되는 것 (cool0c.wat 이 필요 없다) ------------------------
#
# 아래 세 구현 비교는 WAT 가 뒤처지면 통째로 스킵된다. 그런데 이 픽스처들이
# **오라클에서 유일하게 실행되는 자리**이기도 해서, 한동안 스킵되는 사이 참조
# 구현의 커버리지가 100% 에서 98% 로 조용히 내려앉아 있었다 -- 진단 37 줄이
# 아무 시험에도 안 걸리는 상태였다. `fail_under = 100` 이 그걸 잡아야 했는데,
# 잡은 뒤에도 원인이 "WAT 가 낡았다"로 보이지 않아 방치됐다.
#
# 그래서 나눈다. 오라클이 명세대로 거절하는지는 WAT 와 아무 상관이 없다.


@pytest.mark.parametrize(
    "why,src,diag", GAP_DIAGNOSTICS, ids=[w for w, _, _ in GAP_DIAGNOSTICS]
)
def test_the_oracle_gives_each_gap_its_documented_diagnostic(why, src, diag):
    """픽스처 첫 줄의 `// expect: error ...` 를 글자 그대로 낸다."""
    assert reference_compile(src) == (1, diag)


@pytest.mark.parametrize("src,why", GAP_PROGRAMS, ids=[w for _, w in GAP_PROGRAMS])
def test_the_oracle_compiles_each_gap_program(src, why):
    status, out = reference_compile(src.encode("ascii"))
    assert status == STATUS_OK, out.decode("ascii", "replace")
    wasmtime.Module(ENGINE, out)


# --- cool0c 와도 견준다 (WAT 없이) ------------------------------------------
#
# 위의 오라클 시험은 "명세대로 거절하는가" 만 본다. **두 구현이 같은 바이트를
# 내는가** 는 아래 세 구현 비교가 보는데, 그것이 WAT 게이트 뒤에 있다.
#
# 그 사이로 진짜 버그가 하나 빠져나갔다. `e_no_field_name` 이 안 쓰는 매개변수를
# 달고 있어서 호출부가 인자를 한 칸 밀어 넣었고, cool0c 는
#
#     `S` has no field ``          (오라클은 `zz` 라고 한다)
#
# 를 내고 있었다. 픽스처는 있었고, 오라클은 옳았고, 아무 시험도 안 빨개졌다.
#
# cool0c 는 오라클로 만들 수 있다. WAT 이 필요한 것은 A 와 B 지 B 하나가 아니다.


@pytest.fixture(scope="module")
def cc_wasm():
    """오라클이 만든 cool0c. 전사가 뒤처져 있어도 돈다."""
    status, wasm = reference_compile(COOL0C.read_bytes())
    assert status == STATUS_OK, wasm.decode("ascii", "replace")
    return wasm


@pytest.mark.parametrize(
    "why,src,diag", GAP_DIAGNOSTICS, ids=[w for w, _, _ in GAP_DIAGNOSTICS]
)
def test_cool0c_gives_each_gap_the_same_diagnostic_as_the_oracle(cc_wasm, why, src, diag):
    assert run_compiler(cc_wasm, src) == (1, diag)


@pytest.mark.parametrize("src,why", GAP_PROGRAMS, ids=[w for _, w in GAP_PROGRAMS])
def test_cool0c_emits_the_same_bytes_as_the_oracle_for_each_gap_program(cc_wasm, src, why):
    b = src.encode("ascii")
    assert run_compiler(cc_wasm, b) == reference_compile(b)


# --- 한 번도 나온 적 없던 진단들 ---------------------------------------------


@needs_current_wat
@pytest.mark.parametrize("src,why", GAPS, ids=[w for _, w in GAPS])
def test_unseen_diagnostics_agree(three, src, why):
    status, out = agree(three, src)
    assert status != STATUS_OK, "이건 거절당해야 하는 소스다"
    assert out.endswith(b"\n")


# --- 한 번도 컴파일된 적 없던 프로그램들 ---------------------------------------


@needs_current_wat
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


# --- 섀도 스택의 바닥 (implementation.md §6, §7) -----------------------------------
#
# 섀도 스택은 이제 0x0180_0000..0x0200_0000 (8 MiB) 이고, 프레임을 쓰는 함수의
# 프롤로그는 $sp 가 SHADOW_FLOOR 아래로 내려가면 unreachable 로 트랩한다
# (implementation.md §6). 그래서 넘치면 "언젠가 트랩"이 아니라 "ABI 슬롯을
# 건드리기 전에 깨끗하게 트랩"이어야 한다.

DEEP = """
struct Frame { a: u32, b: u32 }
fn depth(n: u32) -> u32 {
    let mut fr: Frame = Frame{ a: n, b: 7 };
    if n == 0 { return fr.a; }
    return depth(n - 1) + fr.b;
}
"""

# 우리 바닥 검사가 wasmtime 자신의 호출 스택 한계보다 **먼저** 걸리게 하려면
# 프레임이 충분히 커야 한다. 필요한 크기는 섀도 영역 크기에서 나온다 --
# 영역이 8 MiB 에서 128 MiB 로 커졌을 때 이 상수가 손으로 박혀 있어서 시험이
# 깨졌다. 이제는 유도한다.
_NL = chr(10)
_SHADOW_RANGE = SHADOW_TOP - SHADOW_FLOOR

# wasmtime 이 이 함수로 넉넉히 들어갈 수 있는 깊이. 위의 작은 프레임 시험이
# 15,000 까지 성한 것을 확인해 두었으니 그 아래로 잡는다.
_REACHABLE_DEPTH = 11000
_BIG_NFIELDS = -(-_SHADOW_RANGE // (_REACHABLE_DEPTH * 4))  # u32 필드 개수
_BIG_FRAME_SIZE = _BIG_NFIELDS * 4
DEEP_BIG = (
    "struct Frame { " + ", ".join(f"f{i}: u32" for i in range(_BIG_NFIELDS)) + " }" + _NL
    + "fn depth(n: u32) -> u32 {" + _NL
    + "    let mut fr: Frame = Frame{ " + ", ".join(f"f{i}: n" for i in range(_BIG_NFIELDS)) + " };" + _NL
    + "    if n == 0 { return fr.f0; }" + _NL
    + "    return depth(n - 1) + 1;" + _NL
    + "}" + _NL
)

# depth(n) 은 n+1 번 프레임을 민다 (n, n-1, ..., 0). 처음으로 $sp 가
# SHADOW_FLOOR 아래로 내려가는 n 이 트랩하는 첫 깊이다.
_BIG_TRIP_N = (_SHADOW_RANGE + _BIG_FRAME_SIZE - 1) // _BIG_FRAME_SIZE - 1


def deep_call(src: str, n: int):
    """depth(n) 을 부르고, 호스트 ABI 슬롯이 성했는지 함께 돌려준다."""
    status, wasm = reference_compile(src.encode("ascii"))
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
    """8 MiB 짜리 영역이니, 작은 프레임(8바이트)으로는 wasmtime 의 고유 호출
    스택 한계가 먼저 온다. 그 한계 안에서는 훨씬 깊이 들어가도 성해야 한다."""
    for n in (1000, 8000, 15000):
        outcome, abi = deep_call(DEEP, n)
        assert outcome == "ok", n
        assert abi == bytes([0xAA] * 8), n


def test_shadow_stack_overflow_traps_cleanly_without_corrupting_the_abi():
    """implementation.md §6 -- 섀도 스택을 메모리 위쪽(8 MiB)으로 옮기고 프롤로그에
    바닥 검사를 두었다. 큰 프레임을 써서 wasmtime 의 고유 호출 스택 한계보다 훨씬
    앞에서 우리 검사가 걸리게 만들면, 넘칠 때 ABI 슬롯이 성한 채로 깨끗하게
    트랩하는 것을 볼 수 있다 -- implementation.md §7 이 개정 전에 기록했던
    "조용히 out_ptr/out_len 을 덮는다"는 더 이상 일어나지 않는다.
    """
    outcome, abi = deep_call(DEEP_BIG, _BIG_TRIP_N - 1)
    assert outcome == "ok"
    assert abi == bytes([0xAA] * 8), "아직 ABI 슬롯은 성하다"

    for n in (_BIG_TRIP_N, _BIG_TRIP_N + 1, _BIG_TRIP_N + 100):
        outcome, abi = deep_call(DEEP_BIG, n)
        assert outcome == "trap", n
        assert abi == bytes([0xAA] * 8), "ABI 슬롯이 깨지지 않은 채로 트랩해야 한다"


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
