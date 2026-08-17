"""실행 의미 (language.md §3, §5, §6, §7).

wasmtime 으로 실제로 돌린다. 컴파일이 되는 것과 옳은 것은 다르다.
"""

import pytest

from conftest import instantiate, run, traps

U32_MAX = 0xFFFFFFFF


def i32(v: int) -> int:
    """wasmtime 은 i32 를 부호 있는 것으로 돌려준다."""
    v &= U32_MAX
    return v - 0x1_00000000 if v >= 0x80000000 else v


# --- 산술 (language.md §3) -------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,a,b,want",
    [
        ("a + b", 2, 3, 5),
        ("a - b", 2, 3, -1),
        ("a * b", 6, 7, 42),
        ("a / b", 7, 2, 3),
        ("a / b", -7, 2, -3),  # 0 방향 절단
        ("a % b", 7, 2, 1),
        ("a % b", -7, 2, -1),  # 나머지는 피제수의 부호
        ("a & b", 0xF0, 0x3C, 0x30),
        ("a | b", 0xF0, 0x0F, 0xFF),
        ("a ^ b", 0xFF, 0x0F, 0xF0),
    ],
)
def test_signed_arithmetic(expr, a, b, want):
    src = f"fn f(a: i32, b: i32) -> i32 {{ return {expr}; }}"
    assert i32(run(src, "f", a, b)) == want


def test_wraparound():
    src = "fn f(a: i32, b: i32) -> i32 { return a + b; }"
    assert i32(run(src, "f", 0x7FFFFFFF, 1)) == i32(0x80000000)


def test_negation_wraps():
    src = "fn f(a: i32) -> i32 { return -a; }"
    assert i32(run(src, "f", i32(0x80000000))) == i32(0x80000000)


def test_unsigned_division():
    src = "fn f(a: u32, b: u32) -> u32 { return a / b; }"
    assert run(src, "f", i32(0xFFFFFFFF), 2) & U32_MAX == 0x7FFFFFFF


def test_signed_division_of_the_same_bits_differs():
    su = "fn f(a: u32, b: u32) -> u32 { return a / b; }"
    ss = "fn f(a: i32, b: i32) -> i32 { return a / b; }"
    assert run(su, "f", -2, 2) & U32_MAX == 0x7FFFFFFF
    assert i32(run(ss, "f", -2, 2)) == -1


def test_unsigned_comparison():
    src = "fn f(a: u32, b: u32) -> bool { return a < b; }"
    assert run(src, "f", 1, i32(0xFFFFFFFF)) == 1
    src = "fn f(a: i32, b: i32) -> bool { return a < b; }"
    assert run(src, "f", 1, -1) == 0


def test_shift_right_is_arithmetic_for_i32():
    src = "fn f(a: i32, n: u32) -> i32 { return a >> n; }"
    assert i32(run(src, "f", -16, 2)) == -4


def test_shift_right_is_logical_for_u32():
    src = "fn f(a: u32, n: u32) -> u32 { return a >> n; }"
    assert run(src, "f", i32(0x80000000), 4) & U32_MAX == 0x08000000


def test_shift_left():
    src = "fn f(a: u32, n: u32) -> u32 { return a << n; }"
    assert run(src, "f", 1, 31) & U32_MAX == 0x80000000


def test_as_is_a_bit_reinterpretation():
    src = "fn f(a: i32) -> u32 { return a as u32; }"
    assert run(src, "f", -1) & U32_MAX == U32_MAX


# --- 트랩 (language.md §3, §5) ----------------------------------------------------------


def test_division_by_zero_traps():
    inst = instantiate("fn f(a: i32, b: i32) -> i32 { return a / b; }")
    assert traps(inst, "f", 1, 0)
    assert not traps(inst, "f", 1, 1)


def test_remainder_by_zero_traps():
    assert traps(instantiate("fn f(a: u32, b: u32) -> u32 { return a % b; }"), "f", 1, 0)


def test_signed_division_overflow_traps():
    src = "fn f(a: i32, b: i32) -> i32 { return a / b; }"
    assert traps(instantiate(src), "f", i32(0x80000000), -1)


def test_index_out_of_bounds_traps():
    src = 'fn f(i: u32) -> u32 { let s: []u8 = "abc"; return s[i]; }'
    inst = instantiate(src)
    assert not traps(inst, "f", 2)
    assert traps(inst, "f", 3)
    assert traps(inst, "f", i32(0xFFFFFFFF))


def test_bounds_check_uses_element_size():
    src = "fn f(a: []mut u32, i: u32) { a[i] = 1; }"
    inst = instantiate(src)
    # 슬라이스는 (ptr, len) 둘로 펼쳐진다. 길이 0 이면 어떤 첨자로도 트랩한다
    assert traps(inst, "f", 0x3000, 0, 0)
    assert not traps(inst, "f", 0x3000, 1, 0)


# --- bool 과 단축 평가 (language.md §5) --------------------------------------------------


def test_bool_operations():
    src = "fn f(a: bool, b: bool) -> bool { return a && b; }"
    assert [run(src, "f", x, y) for x in (0, 1) for y in (0, 1)] == [0, 0, 0, 1]
    src = "fn f(a: bool, b: bool) -> bool { return a || b; }"
    assert [run(src, "f", x, y) for x in (0, 1) for y in (0, 1)] == [0, 1, 1, 1]
    src = "fn f(a: bool) -> bool { return !a; }"
    assert [run(src, "f", x) for x in (0, 1)] == [1, 0]


SHORT_CIRCUIT = """
fn bump(c: []mut u32) -> bool { c[0] += 1; return true; }
fn and_(c: []mut u32, a: bool) -> bool { return a && bump(c); }
fn or_(c: []mut u32, a: bool) -> bool { return a || bump(c); }
fn count(c: []u32) -> u32 { return c[0]; }
"""


def test_and_does_not_evaluate_the_right_side_when_false():
    inst = instantiate(SHORT_CIRCUIT)
    inst.write(0x2000, b"\x00" * 4)
    inst.call("and_", 0x2000, 1, 0)  # a = false
    assert int.from_bytes(inst.read(0x2000, 4), "little") == 0
    inst.call("and_", 0x2000, 1, 1)  # a = true
    assert int.from_bytes(inst.read(0x2000, 4), "little") == 1


def test_or_does_not_evaluate_the_right_side_when_true():
    inst = instantiate(SHORT_CIRCUIT)
    inst.write(0x2000, b"\x00" * 4)
    inst.call("or_", 0x2000, 1, 1)  # a = true
    assert int.from_bytes(inst.read(0x2000, 4), "little") == 0
    inst.call("or_", 0x2000, 1, 0)  # a = false
    assert int.from_bytes(inst.read(0x2000, 4), "little") == 1


# --- 흐름 (language.md §6) -------------------------------------------------------------


def test_infinite_for_with_break():
    src = """
fn f(n: u32) -> u32 {
    let mut i: u32 = 0;
    for { if i == n { break; } i += 1; }
    return i;
}
"""
    assert run(src, "f", 7) == 7


def test_conditional_for():
    src = """
fn f(n: u32) -> u32 {
    let mut acc: u32 = 0;
    let mut i: u32 = 0;
    for i < n { acc += i; i += 1; }
    return acc;
}
"""
    assert run(src, "f", 5) == 10


def test_three_clause_for():
    src = """
fn f(n: u32) -> u32 {
    let mut acc: u32 = 0;
    for let mut i: u32 = 0; i < n; i += 1 { acc += i; }
    return acc;
}
"""
    assert run(src, "f", 5) == 10


def test_continue_runs_the_post_statement():
    src = """
fn f(n: u32) -> u32 {
    let mut acc: u32 = 0;
    for let mut i: u32 = 0; i < n; i += 1 {
        if i % 2 == 0 { continue; }
        acc += i;
    }
    return acc;
}
"""
    assert run(src, "f", 10) == 1 + 3 + 5 + 7 + 9


def test_break_applies_to_the_innermost_loop():
    src = """
fn f() -> u32 {
    let mut n: u32 = 0;
    for let mut i: u32 = 0; i < 3; i += 1 {
        for let mut j: u32 = 0; j < 10; j += 1 {
            if j == 2 { break; }
            n += 1;
        }
    }
    return n;
}
"""
    assert run(src, "f") == 6


def test_continue_applies_to_the_innermost_loop():
    src = """
fn f() -> u32 {
    let mut n: u32 = 0;
    for let mut i: u32 = 0; i < 2; i += 1 {
        for let mut j: u32 = 0; j < 4; j += 1 {
            if j == 0 { continue; }
            n += 1;
        }
        n += 100;
    }
    return n;
}
"""
    assert run(src, "f") == 206


def test_if_else_chain():
    src = """
fn f(n: i32) -> i32 {
    if n < 0 { return 0 - 1; } else if n == 0 { return 0; } else { return 1; }
}
"""
    assert [i32(run(src, "f", n)) for n in (-5, 0, 5)] == [-1, 0, 1]


def test_recursion():
    src = """
fn fib(n: u32) -> u32 {
    if n < 2 { return n; }
    return fib(n - 1) + fib(n - 2);
}
"""
    assert [run(src, "fib", n) for n in range(10)] == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]


def test_mutual_recursion():
    src = """
fn even(n: u32) -> bool { if n == 0 { return true; } return odd(n - 1); }
fn odd(n: u32) -> bool { if n == 0 { return false; } return even(n - 1); }
"""
    assert [run(src, "even", n) for n in range(4)] == [1, 0, 1, 0]


# --- struct 와 대여 (language.md §4, §7) ------------------------------------------------

POINT = """
struct Point { x: i32, y: i32 }
fn move_by(p: &mut Point, dx: i32, dy: i32) { p.^.x += dx; p.^.y += dy; }
fn dot(a: &Point, b: &Point) -> i32 { return a.^.x * b.^.x + a.^.y * b.^.y; }
"""


def test_struct_literal_and_field_access():
    src = POINT + "fn f() -> i32 { let p: Point = Point{ x: 3, y: 4 }; return p.x * 10 + p.y; }"
    assert run(src, "f") == 34


def test_mutation_through_a_mutable_borrow():
    src = POINT + """
fn f() -> i32 {
    let mut p: Point = Point{ x: 1, y: 2 };
    move_by(&mut p, 10, 20);
    return p.x * 100 + p.y;
}
"""
    assert run(src, "f") == 1122


def test_two_shared_borrows_of_the_same_variable():
    src = POINT + """
fn f() -> i32 { let p: Point = Point{ x: 3, y: 4 }; return dot(&p, &p); }
"""
    assert run(src, "f") == 25


def test_distinct_aggregates_do_not_overlap():
    src = POINT + """
fn f() -> i32 {
    let mut a: Point = Point{ x: 1, y: 2 };
    let mut b: Point = Point{ x: 3, y: 4 };
    move_by(&mut a, 100, 0);
    return a.x * 1000 + b.x;
}
"""
    assert run(src, "f") == 101003


def test_recursion_with_aggregate_locals_uses_the_shadow_stack():
    src = POINT + """
fn depth(n: u32) -> i32 {
    let mut p: Point = Point{ x: 0, y: 0 };
    move_by(&mut p, 1, 0);
    if n == 0 { return p.x; }
    let inner: i32 = depth(n - 1);
    return p.x + inner;
}
"""
    assert run(src, "depth", 20) == 21


def test_shadow_stack_is_restored():
    src = POINT + """
fn scratch() { let mut p: Point = Point{ x: 1, y: 2 }; move_by(&mut p, 1, 1); }
fn f() -> i32 {
    let mut a: Point = Point{ x: 7, y: 8 };
    scratch();
    scratch();
    return a.x * 10 + a.y;
}
"""
    assert run(src, "f") == 78


def test_borrowing_a_scalar_local():
    src = """
fn incr(n: &mut u32) { n.^ += 1; }
fn f() -> u32 { let mut x: u32 = 5; incr(&mut x); incr(&mut x); return x; }
"""
    assert run(src, "f") == 7


def test_borrowing_a_parameter():
    src = """
fn incr(n: &mut u32) { n.^ += 1; }
fn g(x: u32) -> u32 { incr(&mut x); return x; }
"""
    # 매개변수는 불변이다 -- 이건 컴파일되지 않아야 한다
    from conftest import compile_err

    assert "immutable place" in compile_err(src)


def test_field_of_a_byte_truncates_on_store():
    src = """
struct B { lo: u8, hi: u8 }
fn f() -> u32 { let mut b: B = B{ lo: 0, hi: 0 }; b.lo = 0x1FF; b.hi = 2; return b.lo * 1000 + b.hi; }
"""
    assert run(src, "f") == 0xFF * 1000 + 2


def test_bool_field_is_one_byte_and_does_not_clobber():
    src = """
struct S { a: bool, b: bool, n: i32 }
fn f() -> i32 {
    let mut s: S = S{ a: false, b: false, n: 7 };
    s.a = true;
    if s.b { return 0 - 1; }
    return s.n;
}
"""
    assert i32(run(src, "f")) == 7


# --- enum 과 match (language.md §4, §6) -------------------------------------------------

SHAPE = """
enum Shape { Dot, Line(i32), Rect(i32, i32) }
fn area(s: &Shape) -> i32 {
    match s.^ {
        Dot => { return 0; }
        Line(n) => { return n; }
        Rect(w, h) => { return w * h; }
    }
}
"""


def test_match_dispatches_on_the_tag():
    src = SHAPE + """
fn f(k: u32) -> i32 {
    let mut s: Shape = Shape.Dot;
    if k == 1 { s = Shape.Line(5); }
    if k == 2 { s = Shape.Rect(3, 4); }
    return area(&s);
}
"""
    assert [i32(run(src, "f", k)) for k in (0, 1, 2)] == [0, 5, 12]


def test_wildcard_arm():
    src = SHAPE + """
fn f(k: u32) -> i32 {
    let mut s: Shape = Shape.Dot;
    if k == 1 { s = Shape.Rect(3, 4); }
    match s { Dot => { return 100; } _ => { return 200; } }
}
"""
    assert [run(src, "f", k) for k in (0, 1)] == [100, 200]


def test_payload_bindings_read_the_right_slots():
    src = """
enum E { P(u32, u32) }
fn f() -> u32 {
    let e: E = E.P(11, 22);
    match e { P(a, b) => { return a * 100 + b; } }
}
"""
    assert run(src, "f") == 1122


def test_overwriting_a_variant_replaces_the_tag():
    src = SHAPE + """
fn f() -> i32 {
    let mut s: Shape = Shape.Rect(3, 4);
    s = Shape.Dot;
    return area(&s);
}
"""
    assert run(src, "f") == 0


def test_match_arms_are_tried_in_written_order():
    src = """
enum E { A, B, C }
fn f(k: u32) -> u32 {
    let mut e: E = E.A;
    if k == 1 { e = E.B; }
    if k == 2 { e = E.C; }
    match e { C => { return 3; } A => { return 1; } _ => { return 2; } }
}
"""
    assert [run(src, "f", k) for k in (0, 1, 2)] == [1, 2, 3]


# --- 슬라이스 (language.md §3) ----------------------------------------------------------


def test_string_literal_contents():
    src = 'fn f(i: u32) -> u32 { let s: []u8 = "cool0"; return s[i]; }'
    assert [run(src, "f", i) for i in range(5)] == [ord(c) for c in "cool0"]


def test_string_literal_length():
    assert run('fn f() -> u32 { let s: []u8 = "hello"; return s.len; }', "f") == 5


def test_escapes_in_string_literals():
    src = r'fn f(i: u32) -> u32 { let s: []u8 = "a\nb\t\\"; return s[i]; }'
    assert [run(src, "f", i) for i in range(5)] == [97, 10, 98, 9, 92]


def test_identical_strings_share_storage():
    src = """
fn a() -> u32 { let s: []u8 = "same"; return s[0]; }
fn b() -> u32 { let s: []u8 = "same"; return s[0]; }
"""
    inst = instantiate(src)
    assert inst.call("a") == inst.call("b") == ord("s")


def test_writing_through_a_mutable_slice():
    src = """
fn fill(a: []mut u8, v: u32) {
    for let mut i: u32 = 0; i < a.len; i += 1 { a[i] = v; }
}
fn get(a: []u8, i: u32) -> u32 { return a[i]; }
"""
    inst = instantiate(src)
    inst.write(0x3000, b"\x00" * 4)
    inst.call("fill", 0x3000, 4, 0x41)
    assert inst.read(0x3000, 4) == b"AAAA"


def test_slice_of_u32_uses_element_size():
    src = """
fn set(a: []mut u32, i: u32, v: u32) { a[i] = v; }
"""
    inst = instantiate(src)
    inst.write(0x3000, b"\x00" * 12)
    inst.call("set", 0x3000, 3, 2, 0xDEADBEEF)
    assert inst.read(0x3000, 12) == b"\x00" * 8 + (0xDEADBEEF).to_bytes(4, "little")


def test_slice_locals_round_trip():
    src = """
fn f() -> u32 {
    let mut s: []u8 = "abc";
    let t: []u8 = "de";
    s = t;
    return s.len * 1000 + s[0];
}
"""
    assert run(src, "f") == 2 * 1000 + ord("d")


# --- 생 포인터와 unsafe (language.md §6) ------------------------------------------------


def test_raw_pointer_round_trip():
    src = """
fn f(addr: u32, v: u32) -> u32 {
    unsafe {
        let p: *u32 = addr as *u32;
        p.^ = v;
        return p.^;
    }
}
"""
    inst = instantiate(src)
    assert inst.call("f", 0x4000, 12345) == 12345
    assert int.from_bytes(inst.read(0x4000, 4), "little") == 12345


def test_pointer_arithmetic_through_u32():
    src = """
fn f(base: u32) -> u32 {
    unsafe {
        let a: *u32 = base as *u32;
        let b: *u32 = (base + 4) as *u32;
        a.^ = 1;
        b.^ = 2;
        return a.^ * 10 + b.^;
    }
}
"""
    assert run(src, "f", 0x4000) == 12


def test_null_pointer_comparison():
    src = """
fn f(a: u32) -> bool { let p: *u8 = a as *u8; return p == 0 as *u8; }
"""
    assert [run(src, "f", a) for a in (0, 16)] == [1, 0]


# --- 호스트 ABI (implementation.md §7) --------------------------------------------------------


def test_memory_is_32_mib():
    inst = instantiate("fn f() { }")
    assert inst.memory.size(inst.store) == 512
    assert inst.memory.data_len(inst.store) == 512 * 65536


def test_a_program_can_satisfy_the_abi():
    """implementation.md §7 의 모양을 그대로 흉내낸다 -- 결과를 out_ptr/out_len 에 남기고 0 을 돌려준다."""
    src = """
const OUT_PTR: u32 = 0;
const OUT_LEN: u32 = 4;
const SRC: u32 = 0x1000;

fn compile(src_len: u32) -> i32 {
    unsafe {
        let p: *u32 = OUT_PTR as *u32;
        let l: *u32 = OUT_LEN as *u32;
        p.^ = SRC;
        l.^ = src_len;
    }
    return 0;
}
"""
    inst = instantiate(src)
    inst.write(0x1000, b"fn f() { }")
    assert inst.call("compile", 10) == 0
    assert int.from_bytes(inst.read(0, 4), "little") == 0x1000
    assert int.from_bytes(inst.read(4, 4), "little") == 10
    assert inst.read(0x1000, 10) == b"fn f() { }"


# --- 큰 프로그램 -----------------------------------------------------------


def test_a_small_real_program():
    """cool0 로 쓴 십진 파서. cool0c 가 할 일의 축소판이다."""
    src = """
fn is_digit(c: u32) -> bool { return c >= '0' && c <= '9'; }

fn parse_u32(s: []u8, from: u32) -> u32 {
    let mut acc: u32 = 0;
    for let mut i: u32 = from; i < s.len; i += 1 {
        let c: u32 = s[i];
        if !is_digit(c) { return acc; }
        acc = acc * 10 + (c - '0');
    }
    return acc;
}

fn parse_at(a: []u8, from: u32) -> u32 { return parse_u32(a, from); }

fn demo() -> u32 { let s: []u8 = "40275x"; return parse_u32(s, 0); }
"""
    inst = instantiate(src)
    assert inst.call("demo") == 40275
    inst.write(0x5000, b"1234")
    assert inst.call("parse_at", 0x5000, 4, 0) == 1234
    assert inst.call("parse_at", 0x5000, 4, 2) == 34


# --- 겹치는 경우들 ---------------------------------------------------------


def test_slice_of_structs():
    src = """
struct Rec { id: u32, n: i32 }
fn set(a: []mut Rec, i: u32, id: u32, n: i32) { a[i].id = id; a[i].n = n; }
fn get_n(a: []Rec, i: u32) -> i32 { return a[i].n; }
"""
    inst = instantiate(src)
    inst.write(0x6000, b"\x00" * 24)
    inst.call("set", 0x6000, 3, 2, 77, -5)
    assert inst.call("get_n", 0x6000, 3, 2) == -5
    assert int.from_bytes(inst.read(0x6000 + 16, 4), "little") == 77


def test_match_on_an_indexed_enum():
    src = """
enum Cell { Empty, Full(u32) }
fn put(a: []mut Cell, i: u32, v: u32) { a[i] = Cell.Full(v); }
fn read(a: []Cell, i: u32) -> u32 {
    match a[i] { Empty => { return 0; } Full(v) => { return v; } }
}
"""
    inst = instantiate(src)
    inst.write(0x6100, b"\x00" * 24)
    assert inst.call("read", 0x6100, 3, 1) == 0
    inst.call("put", 0x6100, 3, 1, 42)
    assert inst.call("read", 0x6100, 3, 1) == 42
    assert inst.call("read", 0x6100, 3, 0) == 0  # 이웃을 건드리지 않았다


def test_assigning_a_literal_through_a_mutable_borrow():
    src = """
struct P { x: i32, y: i32 }
fn reset(p: &mut P) { p.^ = P{ x: 9, y: 8 }; }
fn f() -> i32 {
    let mut p: P = P{ x: 1, y: 2 };
    reset(&mut p);
    return p.x * 10 + p.y;
}
"""
    assert run(src, "f") == 98


def test_slice_field_in_a_struct():
    src = """
struct Tok { kind: u32, text: []u8 }
fn f() -> u32 {
    let mut t: Tok = Tok{ kind: 1, text: "hello" };
    t.text = "hi";
    return t.kind * 1000 + t.text.len * 10 + t.text[1];
}
"""
    assert run(src, "f") == 1000 + 20 + ord("i")


def test_borrowing_a_slice_local():
    src = """
fn grow(s: &mut []mut u8) { s.^[0] = 90; }
fn f(a: []mut u8) -> u32 { let mut s: []mut u8 = a; grow(&mut s); return a[0]; }
"""
    inst = instantiate(src)
    inst.write(0x6200, b"\x00")
    assert inst.call("f", 0x6200, 1) == 90


def test_nested_match_inside_a_loop():
    src = """
enum Op { Add(i32), Mul(i32), Stop }
fn run_ops(a: []Op) -> i32 {
    let mut acc: i32 = 1;
    for let mut i: u32 = 0; i < a.len; i += 1 {
        match a[i] {
            Add(n) => { acc += n; }
            Mul(n) => { acc *= n; }
            Stop => { break; }
        }
    }
    return acc;
}
"""
    inst = instantiate(src)
    ops = b""
    for tag, val in [(0, 4), (1, 3), (2, 0), (0, 100)]:
        ops += tag.to_bytes(4, "little") + val.to_bytes(4, "little", signed=True)
    inst.write(0x6300, ops)
    assert i32(inst.call("run_ops", 0x6300, 4)) == (1 + 4) * 3


def test_deeply_recursive_calls_do_not_corrupt_the_frame():
    src = """
struct Acc { total: u32, depth: u32 }
fn step(a: &mut Acc, n: u32) {
    a.^.depth += 1;
    a.^.total += n;
    if n > 0 { step(a, n - 1); }
}
fn f(n: u32) -> u32 {
    let mut a: Acc = Acc{ total: 0, depth: 0 };
    step(&mut a, n);
    return a.total * 1000 + a.depth;
}
"""
    n = 20
    assert run(src, "f", n) == sum(range(n + 1)) * 1000 + (n + 1)


def test_string_literal_as_a_call_argument():
    src = """
fn sum(a: []u8) -> u32 {
    let mut t: u32 = 0;
    for let mut i: u32 = 0; i < a.len; i += 1 { t += a[i]; }
    return t;
}
fn f() -> u32 { return sum("abc"); }
"""
    assert run(src, "f") == ord("a") + ord("b") + ord("c")


def test_parameters_are_immutable():
    """매개변수는 `mut` 로 선언되지 않았으므로 가변 장소가 아니다 (language.md §6)."""
    from conftest import compile_err

    src = """
fn poke(s: &mut []mut u8) { s.^[0] = 7; }
fn f(a: []mut u8) { poke(&mut a); }
"""
    assert "cannot borrow an immutable place as `&mut`" in compile_err(src)


def test_borrowing_a_slice_local():
    src = """
fn poke(s: &mut []mut u8) { s.^[0] = 7; }
fn f(a: []mut u8) -> u32 { let mut s: []mut u8 = a; poke(&mut s); return a[0]; }
"""
    inst = instantiate(src)
    inst.write(0x6400, b"\x00")
    assert inst.call("f", 0x6400, 1) == 7


def test_enum_with_a_slice_payload():
    src = """
enum Tok { End, Word([]u8) }
fn f() -> u32 {
    let t: Tok = Tok.Word("abcd");
    match t { End => { return 0; } Word(s) => { return s.len * 100 + s[3]; } }
}
"""
    assert run(src, "f") == 400 + ord("d")


def test_struct_literal_fields_can_call_functions():
    src = """
struct S { a: i32, b: i32 }
fn one() -> i32 { return 1; }
fn two() -> i32 { return 2; }
fn f() -> i32 { let s: S = S{ a: one(), b: two() }; return s.a * 10 + s.b; }
"""
    assert run(src, "f") == 12


def test_a_call_may_be_the_for_post_statement():
    src = """
fn step(a: []mut u32) { a[0] += 1; }
fn f(a: []mut u32) -> u32 {
    for let mut i: u32 = 0; a[0] < 3; step(a) { i += 1; }
    return a[0] * 10 + i;
}
"""
    from conftest import compile_err

    # `i` 는 for 의 스코프 안에서만 산다
    assert "unknown name `i`" in compile_err(src)

    ok = """
fn step(a: []mut u32) { a[0] += 1; }
fn f(a: []mut u32) -> u32 {
    for let mut i: u32 = 0; a[0] < 3; step(a) { i += 1; }
    return a[0];
}
"""
    inst = instantiate(ok)
    inst.write(0x6500, b"\x00\x00\x00\x00")
    assert inst.call("f", 0x6500, 1) == 3


def test_the_for_header_scope_ends_with_the_loop():
    src = """
fn f() -> u32 {
    for let mut i: u32 = 0; i < 3; i += 1 { }
    let i: u32 = 9;
    return i;
}
"""
    assert run(src, "f") == 9


def test_only_the_innermost_scope_sees_a_shadowed_local():
    src = """
fn f() -> i32 {
    let x: i32 = 1;
    let mut out: i32 = 0;
    if true { let x: i32 = 2; out = x; }
    return out * 10 + x;
}
"""
    assert run(src, "f") == 21


def test_slice_field_reads_back_its_initializer():
    """리터럴이 슬라이스 필드를 두 조각으로 제대로 쓰는지 (implementation.md §2).

    덮어쓰고 나서 읽으면 이 결함이 가려진다. 초기값을 그대로 읽어야 한다.
    """
    src = """
struct Tok { kind: u32, text: []u8, tail: u32 }
fn f() -> u32 {
    let t: Tok = Tok{ kind: 1, text: "hello", tail: 9 };
    return t.kind * 100000 + t.text.len * 1000 + t.text[4] * 10 + t.tail;
}
"""
    assert run(src, "f") == 100000 + 5000 + ord("o") * 10 + 9


def test_enum_slice_payload_reads_back_its_initializer():
    src = """
enum Tok { End, Word([]u8, u32) }
fn f() -> u32 {
    let t: Tok = Tok.Word("abcd", 7);
    match t { End => { return 0; } Word(s, n) => { return s.len * 1000 + s[0] * 10 + n; } }
}
"""
    assert run(src, "f") == 4000 + ord("a") * 10 + 7


def test_slice_literal_does_not_clobber_neighbouring_fields():
    src = """
struct S { a: u32, s: []u8, b: u32 }
fn f() -> u32 {
    let x: S = S{ a: 111, s: "zz", b: 222 };
    return x.a * 1000 + x.b;
}
"""
    assert run(src, "f") == 111 * 1000 + 222
