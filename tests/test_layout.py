"""배치와 상수 접기 (SPEC.md §4).

배치가 결정적이지 않으면 §9 의 바이트 패리티가 성립하지 않는다. 그래서 오프셋을
직접 들여다본다.
"""

import pytest

from cool0.cool0 import BOOL, I32, Checker, Parser, Slice, U8, U32, lex


def analyze(src: str) -> Checker:
    return Checker(Parser(lex(src.encode("ascii"))).parse_program()).run()


def struct_of(src: str, name: str = "S"):
    return analyze(src).structs[name]


def offsets(src: str, name: str = "S"):
    s = struct_of(src, name)
    return [(f.name, f.off) for f in s.fields], s.size, s.align


# --- struct (§4) -----------------------------------------------------------


def test_fields_are_in_declaration_order():
    offs, size, align = offsets("struct S { a: i32, b: u32 }")
    assert offs == [("a", 0), ("b", 4)]
    assert (size, align) == (8, 4)


def test_each_field_aligns_to_its_own_size():
    offs, size, align = offsets("struct S { a: u8, b: i32 }")
    assert offs == [("a", 0), ("b", 4)]  # a 뒤에 3바이트 구멍
    assert (size, align) == (8, 4)


def test_size_rounds_up_to_the_struct_alignment():
    offs, size, align = offsets("struct S { a: i32, b: u8 }")
    assert offs == [("a", 0), ("b", 4)]
    assert (size, align) == (8, 4)  # 5 를 4의 배수로 올린다


def test_bytes_pack_tightly():
    offs, size, align = offsets("struct S { a: u8, b: u8, c: u8 }")
    assert offs == [("a", 0), ("b", 1), ("c", 2)]
    assert (size, align) == (3, 1)


def test_bool_is_one_byte():
    offs, size, align = offsets("struct S { a: bool, b: bool, c: i32 }")
    assert offs == [("a", 0), ("b", 1), ("c", 4)]
    assert (size, align) == (8, 4)


def test_slice_is_eight_bytes_aligned_four():
    offs, size, align = offsets("struct S { a: u8, b: []u8 }")
    assert offs == [("a", 0), ("b", 4)]
    assert (size, align) == (12, 4)


def test_pointer_is_four_bytes():
    offs, size, align = offsets("struct S { a: *u8, b: *S }")
    assert offs == [("a", 0), ("b", 4)]
    assert (size, align) == (8, 4)


def test_empty_struct():
    offs, size, align = offsets("struct S { }")
    assert (offs, size, align) == ([], 0, 1)


def test_field_types_are_resolved():
    s = struct_of("struct S { a: i32, b: u32, c: bool, d: u8, e: []mut u8 }")
    assert [f.ty for f in s.fields] == [I32, U32, BOOL, U8, Slice(U8, True)]


# --- enum (§4) -------------------------------------------------------------


def enum_of(src: str, name: str = "E"):
    return analyze(src).enums[name]


def test_tags_are_declaration_order_from_zero():
    e = enum_of("enum E { A, B, C }")
    assert [(v.name, v.tag) for v in e.variants] == [("A", 0), ("B", 1), ("C", 2)]


def test_payloadless_enum_is_four_bytes():
    e = enum_of("enum E { A, B }")
    assert (e.size, e.align) == (4, 4)


def test_payload_slot_starts_at_four():
    e = enum_of("enum E { A, B(i32) }")
    assert [f.off for f in e.index["B"].payload] == [4]
    assert (e.size, e.align) == (8, 4)


def test_payload_slot_is_the_largest_variant():
    e = enum_of("enum E { A, B(i32), C(i32, u32), D(u8) }")
    assert [f.off for f in e.index["C"].payload] == [4, 8]
    assert e.size == 12  # 태그 4 + 슬롯 8


def test_payload_fields_align_inside_the_slot():
    e = enum_of("enum E { A(u8, i32) }")
    assert [f.off for f in e.index["A"].payload] == [4, 8]
    assert e.size == 12


def test_enum_alignment_is_always_four():
    assert enum_of("enum E { A(u8) }").align == 4


# --- const 접기 (§4) -------------------------------------------------------


def const_value(src: str, name: str = "C"):
    return analyze(src).consts[name].value


@pytest.mark.parametrize(
    "expr,want",
    [
        ("1 + 2", 3),
        ("10 - 20", (-10) & 0xFFFFFFFF),
        ("6 * 7", 42),
        ("-7 / 2", (-3) & 0xFFFFFFFF),  # 0 방향 절단
        ("-7 % 2", (-1) & 0xFFFFFFFF),
        ("1 << 31", 0x80000000),
        ("0xF0 | 0x0F", 0xFF),
        ("0xFF ^ 0x0F", 0xF0),
        ("0xFF & 0x0F", 0x0F),
        ("2147483647 + 1", 0x80000000),  # 랩어라운드
        ("'A' as i32", 65),
    ],
)
def test_constant_folding(expr, want):
    assert const_value("const C: i32 = " + expr + ";") == want


def test_unsigned_shift_right_is_logical():
    assert const_value("const C: u32 = 0x80000000 >> 4;") == 0x08000000


def test_signed_shift_right_is_arithmetic():
    assert const_value("const C: i32 = -16 >> 2;") == (-4) & 0xFFFFFFFF


def test_shift_amount_wraps_at_32():
    assert const_value("const C: u32 = 1 << 33;") == 2


def test_bool_constants():
    assert const_value("const C: bool = true && !false;") == 1
    assert const_value("const C: bool = 1 < 2;") == 1


def test_consts_can_reference_each_other_in_any_order():
    src = "const B: i32 = A * 2; const A: i32 = 21;"
    assert const_value(src, "B") == 42


def test_const_cast_reinterprets():
    assert const_value("const C: u32 = -1 as u32;") == 0xFFFFFFFF


# --- 문자열 상수 (§11) -----------------------------------------------------


def test_identical_strings_share_an_address():
    ck = analyze('fn f() { let a: []u8 = "hi"; let b: []u8 = "hi"; }')
    assert len(ck.strings) == 1


def test_strings_are_placed_in_first_appearance_order():
    ck = analyze('fn f() { let a: []u8 = "aa"; let b: []u8 = "b"; }')
    addrs = list(ck.strings.items())
    assert addrs[0][0] == b"aa" and addrs[1][0] == b"b"
    assert addrs[1][1] == addrs[0][1] + 2  # 패딩 없음


def test_string_placement_follows_declaration_order():
    ck = analyze('fn g() { let a: []u8 = "g"; } fn f() { let b: []u8 = "f"; }')
    assert list(ck.strings) == [b"g", b"f"]
