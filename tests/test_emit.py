"""방출 규약 (implementation.md §8, §5).

implementation.md §8 이 요구하는 것은 바이트 단위 결정론이다. 그래서 여기서는 모듈을 직접 뜯어본다.
"""

import pytest

from conftest import compile_ok, validate

MAGIC = b"\x00asm\x01\x00\x00\x00"

SEC_NAMES = {
    1: "type", 2: "import", 3: "function", 4: "table", 5: "memory",
    6: "global", 7: "export", 8: "start", 9: "element", 10: "code",
    11: "data", 0: "custom",
}


def leb_u(data: bytes, i: int):
    n = shift = 0
    while True:
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not (b & 0x80):
            return n, i
        shift += 7


def sections(wasm: bytes):
    """(이름, 페이로드) 목록. 나온 순서 그대로."""
    assert wasm[:8] == MAGIC
    out, i = [], 8
    while i < len(wasm):
        sid = wasm[i]
        size, i = leb_u(wasm, i + 1)
        out.append((SEC_NAMES[sid], wasm[i : i + size]))
        i += size
    return out


def section(wasm: bytes, name: str):
    for n, payload in sections(wasm):
        if n == name:
            return payload
    return None


# --- 골든 바이트 ------------------------------------------------------------


def hexstr(s: str) -> str:
    """공백과 `#` 주석을 걷어낸 16진 문자열."""
    out = []
    for line in s.strip().splitlines():
        out.append(line.split("#")[0].strip().replace(" ", ""))
    return "".join(out)


def test_empty_function_module_is_exact():
    """가장 작은 모듈. 인코딩이 바뀌면 여기서 먼저 깨진다."""
    assert compile_ok("fn f() { }").hex() == hexstr(
        """
        0061736d 01000000            # 매직 + 버전
        01 04 01 6000 00             # type:     () -> ()
        03 02 01 00                  # function: 함수 하나, 타입 0
        05 06 01 01 8004 8004        # memory:   min = max = 512
        06 07 01 7f 01 41 8020 0b    # global:   mut i32 = 0x1000
        07 0e 02
           01 66 00 00               # export:   "f" -> 함수 0
           06 6d656d6f7279 02 00     #           "memory" -> 메모리 0
        0a 04 01 02 00 0b            # code:     지역변수 없음, end
        """
    )


def test_add_function_is_exact():
    assert compile_ok("fn add(a: i32, b: i32) -> i32 { return a + b; }").hex() == hexstr(
        """
        0061736d 01000000
        01 07 01 60 02 7f7f 01 7f    # type:  (i32, i32) -> i32
        03 02 01 00
        05 06 01 01 8004 8004
        06 07 01 7f 01 41 8020 0b
        07 10 02
           03 616464 00 00           # "add"
           06 6d656d6f7279 02 00
        0a 0b 01 09 00
           20 00                     # local.get 0
           20 01                     # local.get 1
           6a                        # i32.add
           0f                        # return
           00                        # unreachable  (implementation.md §5 -- 값 함수의 꼬리)
           0b                        # end
        """
    )


# --- 섹션 -----------------------------------------------------------------


def test_section_order_is_canonical():
    wasm = compile_ok('const C: i32 = 1; struct S { a: i32 } fn f() { let s: []u8 = "x"; }')
    assert [n for n, _ in sections(wasm)] == [
        "type", "function", "memory", "global", "export", "code", "data"
    ]


def test_no_imports_no_tables_no_start():
    wasm = compile_ok("fn f() { }")
    names = [n for n, _ in sections(wasm)]
    assert "import" not in names and "table" not in names and "start" not in names
    assert "element" not in names and "custom" not in names


def test_memory_is_fixed_at_512_pages():
    # 하나짜리 벡터, flags=1 (최대값 있음), min = max = 512
    payload = section(compile_ok("fn f() { }"), "memory")
    assert payload == bytes([0x01, 0x01]) + b"\x80\x04" + b"\x80\x04"


def test_data_section_is_absent_without_strings():
    assert section(compile_ok("fn f() { }"), "data") is None


def test_module_with_no_functions_is_valid():
    wasm = compile_ok("const C: i32 = 1;")
    validate(wasm)
    assert [n for n, _ in sections(wasm)] == ["memory", "global", "export"]


# --- export (implementation.md §7, §4) -------------------------------------------------------


def export_names(wasm: bytes):
    return [e.name for e in validate(wasm).exports]


def test_every_function_is_exported_by_name():
    assert export_names(compile_ok("fn a() { } fn b() { } fn c() { }")) == [
        "a", "b", "c", "memory"
    ]


def test_memory_is_exported_last():
    assert export_names(compile_ok("fn z() { }"))[-1] == "memory"


def test_export_order_follows_declaration_order():
    src = "fn z() { } const C: i32 = 1; fn a() { } struct S { x: i32 } fn m() { }"
    assert export_names(compile_ok(src)) == ["z", "a", "m", "memory"]


# --- 타입 섹션 (implementation.md §4) --------------------------------------------------------


def test_signatures_are_deduplicated_in_first_use_order():
    src = """
fn a(x: i32) -> i32 { return x; }
fn b() { }
fn c(y: u32) -> u32 { return y; }
"""
    # a 와 c 는 서명이 같다 (둘 다 (i32) -> i32)
    payload = section(compile_ok(src), "type")
    count, _ = leb_u(payload, 0)
    assert count == 2


def test_slice_parameters_flatten_to_two_i32():
    payload = section(compile_ok("fn f(s: []u8) { }"), "type")
    assert payload == bytes([0x01, 0x60, 0x02, 0x7F, 0x7F, 0x00])


def test_borrow_parameters_are_one_i32():
    src = "struct S { a: i32 } fn f(p: &mut S) { }"
    assert section(compile_ok(src), "type") == bytes([0x01, 0x60, 0x01, 0x7F, 0x00])


# --- 결정론 (implementation.md §8) ------------------------------------------------------------

DETERMINISM_SRC = """
struct Point { x: i32, y: i32 }
enum Shape { Dot, Line(i32) }
const K: u32 = 3;
fn area(s: &Shape) -> i32 { match s.^ { Dot => { return 0; } Line(n) => { return n; } } }
fn f(a: []mut u8) -> u32 {
    let mut p: Point = Point{ x: 1, y: 2 };
    for let mut i: u32 = 0; i < K; i += 1 { a[i] = 65; }
    let s: []u8 = "hello";
    return s.len;
}
"""


def test_same_source_gives_the_same_bytes():
    assert compile_ok(DETERMINISM_SRC) == compile_ok(DETERMINISM_SRC)


def test_comments_and_whitespace_do_not_change_the_bytes():
    # 소스는 ASCII 전용이라 주석도 ASCII 다 (language.md §2)
    noisy = DETERMINISM_SRC.replace("\n", "  // noise\n").replace("{", "{\n\t")
    assert compile_ok(noisy) == compile_ok(DETERMINISM_SRC)


def test_everything_validates():
    validate(compile_ok(DETERMINISM_SRC))


# --- 코드 생성의 균일함 (implementation.md §5) ------------------------------------------------


def code_body(wasm: bytes) -> bytes:
    payload = section(wasm, "code")
    _, i = leb_u(payload, 0)  # 본문 개수
    size, i = leb_u(payload, i)
    return payload[i : i + size]


def test_memarg_offset_is_always_zero():
    """implementation.md §5 -- 오프셋 즉치값은 0 이고 주소는 i32.add 로 만든다."""
    src = "struct S { a: i32, b: i32 } fn f(p: &S) -> i32 { return p.^.b; }"
    body = code_body(compile_ok(src))
    # local.get 0, i32.const 4, i32.add, i32.load align=2 offset=0
    assert body.endswith(bytes([0x20, 0x00, 0x41, 0x04, 0x6A, 0x28, 0x02, 0x00, 0x0F, 0x00, 0x0B]))


def test_zero_offset_is_still_emitted():
    src = "struct S { a: i32 } fn f(p: &S) -> i32 { return p.^.a; }"
    body = code_body(compile_ok(src))
    assert bytes([0x41, 0x00, 0x6A]) in body  # i32.const 0; i32.add


def test_byte_access_uses_load8_u_with_alignment_zero():
    body = code_body(compile_ok("fn f(s: []u8) -> u32 { return s[0]; }"))
    assert bytes([0x2D, 0x00, 0x00]) in body


def test_cast_emits_nothing():
    a = code_body(compile_ok("fn f(x: i32) -> u32 { return x as u32; }"))
    b = code_body(compile_ok("fn f(x: i32) -> i32 { return x; }"))
    assert a == b


def test_no_frame_means_no_prologue():
    body = code_body(compile_ok("fn f(x: i32) -> i32 { return x; }"))
    assert bytes([0x23]) not in body  # global.get 가 없다


def test_aggregate_local_creates_a_frame():
    src = "struct S { a: i32 } fn f() { let s: S = S{ a: 1 }; }"
    body = code_body(compile_ok(src))
    # 프롤로그: global.get $sp, i32.const 4, i32.sub, global.set $sp
    assert bytes([0x23, 0x00, 0x41, 0x04, 0x6B, 0x24, 0x00]) in body
    # 에필로그: 같은 값을 도로 더한다
    assert body.endswith(bytes([0x23, 0x00, 0x41, 0x04, 0x6A, 0x24, 0x00, 0x0B]))


def test_bounds_check_is_emitted_even_for_byte_slices():
    body = code_body(compile_ok("fn f(s: []u8) -> u32 { return s[0]; }"))
    assert bytes([0x4F]) in body  # i32.ge_u
    assert bytes([0x00, 0x0B]) in body  # unreachable; end
    assert bytes([0x41, 0x01, 0x6C]) in body  # i32.const 1; i32.mul -- 줄이지 않는다


def test_value_returning_function_ends_with_unreachable():
    body = code_body(compile_ok("fn f() -> i32 { return 1; }"))
    assert body.endswith(bytes([0x0F, 0x00, 0x0B]))  # return, unreachable, end


def test_void_function_does_not_end_with_unreachable():
    # 지역변수 벡터(0) 다음 바로 end. unreachable 이 붙지 않는다
    assert code_body(compile_ok("fn f() { }")) == bytes([0x00, 0x0B])


def test_slice_field_store_is_two_stores():
    """implementation.md §2 -- 슬라이스는 메모리에서도 두 조각이다."""
    src = 'struct S { s: []u8 } fn f() { let x: S = S{ s: "hi" }; }'
    body = code_body(compile_ok(src))
    assert body.count(bytes([0x36, 0x02, 0x00])) == 2  # i32.store 두 번
    assert bytes([0x41, 0x04, 0x6A]) in body  # 두 번째는 off + 4


def test_slice_field_load_is_two_loads():
    src = 'struct S { s: []u8 } fn f() -> u32 { let x: S = S{ s: "hi" }; return x.s.len; }'
    body = code_body(compile_ok(src))
    assert body.count(bytes([0x28, 0x02, 0x00])) >= 2  # i32.load 두 번
