"""의미 분석 (language.md §3, §4, §6, §7).

명세가 금지한 것 하나하나에 시험이 하나씩 붙는다.
"""

import pytest

from conftest import compile_err, compile_ok


def fn(body: str, sig: str = "fn f()") -> str:
    return sig + " { " + body + " }"


# --- 타입 일치 -------------------------------------------------------------


def test_no_implicit_conversion_between_int_types():
    assert compile_err(fn("let a: i32 = 1; let b: u32 = a;")) == (
        "1:39: expected `u32`, found `i32`"
    )


def test_as_reinterprets():
    compile_ok(fn("let a: i32 = -1; let b: u32 = a as u32;"))


def test_bool_is_not_an_integer():
    assert "expected `bool`" in compile_err(fn("if 1 { }"))
    assert "cannot cast" in compile_err(fn("let a: i32 = true as i32;"))


def test_condition_must_be_bool():
    assert "expected `bool`" in compile_err(fn("let x: i32 = 0; if x { }"))
    assert "expected `bool`" in compile_err(fn("let x: i32 = 0; for x { }"))


def test_integer_literal_takes_its_type_from_context():
    compile_ok(fn("let a: u32 = 5; let b: i32 = 5;"))
    compile_ok(fn("let a: u32 = 1 + 2 * 3;"))


def test_integer_literal_defaults_to_i32():
    assert compile_err(fn("let a = 5; let b: u32 = a;")) == (
        "1:34: expected `u32`, found `i32`"
    )


def test_mixed_operands_do_not_match():
    assert compile_err(fn("let a: i32 = 1; let b: u32 = 2; let c = a + b;")) == (
        "1:52: `+`: `i32` and `u32` do not match"
    )


def test_shift_amount_must_be_u32():
    compile_ok(fn("let a: i32 = 1; let b = a << 3;"))
    assert "expected `u32`, found `i32`" in compile_err(
        fn("let a: i32 = 1; let n: i32 = 2; let b = a << n;")
    )


def test_index_must_be_u32():
    assert "expected `u32`, found `i32`" in compile_err(
        fn('let s: []u8 = "x"; let i: i32 = 0; let b = s[i];')
    )


def test_cannot_order_bools():
    assert "cannot order `bool`" in compile_err(fn("let a = true < false;"))


def test_can_equate_bools():
    compile_ok(fn("let a = true == false;"))


# --- u8 은 저장 전용 (language.md §3) ---------------------------------------------------


def test_no_u8_locals():
    assert compile_err(fn("let b: u8 = 0;")) == (
        "1:10: `u8` is storage-only; there are no u8 locals"
    )


def test_no_u8_params_or_returns():
    assert "storage-only" in compile_err("fn f(a: u8) { }")
    assert "storage-only" in compile_err("fn f() -> u8 { return 0; }")


def test_u8_reads_as_u32():
    compile_ok(fn('let s: []u8 = "x"; let b: u32 = s[0];'))
    compile_ok("struct S { b: u8 } " + fn("let s: S = S{ b: 1 }; let x: u32 = s.b;"))


# --- 집합체 (language.md §3, §4) --------------------------------------------------------


def test_cannot_pass_aggregate_by_value():
    src = "struct S { a: i32 } fn g(s: S) { }"
    assert compile_err(src) == "1:26: cannot pass aggregate `S` by value"


def test_cannot_return_aggregate():
    src = "struct S { a: i32 } fn g() -> S { }"
    assert compile_err(src) == "1:31: cannot return aggregate `S` by value"


def test_cannot_copy_aggregate():
    src = "struct S { a: i32 } " + fn("let x: S = S{ a: 1 }; let y: S = x;")
    assert "cannot copy aggregate `S`" in compile_err(src)


def test_aggregate_locals_and_literals_are_fine():
    compile_ok("struct S { a: i32, b: u32 } " + fn("let mut s: S = S{ a: 1, b: 2 };"))


def test_struct_fields_cannot_be_aggregates():
    assert compile_err("struct A { x: i32 } struct B { a: A }") == (
        "1:32: struct field cannot have aggregate type `A`"
    )


def test_struct_fields_cannot_be_borrows():
    assert compile_err("struct A { x: &i32 }") == (
        "1:12: struct field cannot have borrow type `&i32`"
    )


def test_enum_payload_cannot_be_aggregate():
    assert "enum payload cannot have aggregate type `A`" in compile_err(
        "struct A { x: i32 } enum E { V(A) }"
    )


def test_struct_literal_needs_all_fields_in_order():
    src = "struct S { a: i32, b: i32 } "
    assert "`S` has 2 field(s)" in compile_err(src + fn("let s: S = S{ a: 1 };"))
    assert "expected field `a`, found `b`" in compile_err(
        src + fn("let s: S = S{ b: 1, a: 2 };")
    )


def test_struct_literal_only_as_initializer():
    src = "struct S { a: i32 } fn g(p: &S) { } "
    assert "struct literal is only allowed as an initializer" in compile_err(
        src + fn("g(&S{ a: 1 });")
    )


# --- 슬라이스 --------------------------------------------------------------


def test_cannot_return_a_slice():
    assert compile_err("fn f() -> []u8 { }") == (
        "1:11: cannot return a slice (wasm 1.0 has a single result)"
    )


def test_slice_params_are_fine():
    compile_ok("fn f(s: []u8) -> u32 { return s.len; }")


def test_slice_len_is_u32():
    assert "expected `i32`, found `u32`" in compile_err(
        fn('let s: []u8 = "x"; let n: i32 = s.len;')
    )


def test_slice_ptr_is_a_raw_pointer_and_read_only():
    compile_ok(fn('let s: []u8 = "x"; let p: *u8 = s.ptr;'))
    assert "has no field `ptr`" in compile_err(
        fn('let s: []u8 = "x"; s.ptr = 0 as *u8;')
    )


def test_slice_constructors_require_unsafe_pointer_and_u32_length():
    compile_ok("fn f(p: *u8, n: u32) { unsafe { let a = slice(p, n); let b = slice_mut(p, n); } }")
    assert "`slice` requires `unsafe`" in compile_err(
        "fn f(p: *u8) { let s = slice(p, 1); }"
    )
    assert "expected a raw pointer" in compile_err(
        "fn f() { unsafe { let s = slice(1, 2); } }"
    )
    assert "expected `u32`, found `i32`" in compile_err(
        "fn f(p: *u8, n: i32) { unsafe { let s = slice(p, n); } }"
    )


def test_slice_keywords_cannot_be_captured_as_names():
    assert "expected identifier" in compile_err("fn slice() { }")


def test_string_literal_is_immutable_slice():
    assert "expected `[]mut u8`, found `[]u8`" in compile_err(
        fn('let s: []mut u8 = "x";')
    )


# --- 가변성 (language.md §6) -----------------------------------------------------------


def test_cannot_assign_to_immutable_local():
    assert compile_err(fn("let x: i32 = 1; x = 2;")) == (
        "1:26: cannot assign to an immutable place"
    )


def test_cannot_assign_through_immutable_slice():
    assert "immutable place" in compile_err("fn f(s: []u8) { s[0] = 1; }")


def test_can_assign_through_mut_slice():
    compile_ok("fn f(s: []mut u8) { s[0] = 1; }")


def test_cannot_assign_through_shared_borrow():
    src = "struct S { a: i32 } fn g(p: &S) { p.^.a = 1; }"
    assert "immutable place" in compile_err(src)


def test_can_assign_through_mut_borrow():
    compile_ok("struct S { a: i32 } fn g(p: &mut S) { p.^.a = 1; }")


def test_mutability_can_be_weakened_for_calls():
    compile_ok("""
struct S { a: i32 }
fn read_ref(p: &S) -> i32 { return p.^.a; }
fn read_slice(s: []u8) -> u32 { return s.len; }
fn chain(p: &mut S, s: []mut u8) -> u32 {
    read_ref(p);
    return read_slice(s);
}
""")


def test_field_mutability_follows_the_root():
    src = "struct S { a: i32 } " + fn("let s: S = S{ a: 1 }; s.a = 2;")
    assert "immutable place" in compile_err(src)


# --- 대여 (language.md §7) -------------------------------------------------------------


def test_borrow_only_in_argument_position():
    src = "struct S { a: i32 } " + fn("let mut s: S = S{ a: 1 }; let r = &mut s;")
    assert compile_err(src) == "1:64: a borrow may only appear as a call argument"


def test_borrow_cannot_be_returned():
    assert compile_err("struct S { a: i32 } fn g() -> &S { }") == (
        "1:31: cannot return a borrow"
    )


def test_borrow_cannot_be_a_local_type():
    assert "a borrow cannot be bound to a local" in compile_err(
        "struct S { a: i32 } fn g(p: &S) { let q: &S = p; }"
    )


ALIAS_SRC = """
struct S { a: i32 }
fn one(x: &S) { }
fn two(x: &S, y: &S) { }
fn two_mut(x: &mut S, y: &mut S) { }
fn mix(x: &S, y: &mut S) { }
fn take(x: &mut S, n: i32) { }
"""


def test_two_shared_borrows_are_fine():
    compile_ok(ALIAS_SRC + "fn f() { let s: S = S{ a: 1 }; two(&s, &s); }")


def test_two_mutable_borrows_are_an_error():
    err = compile_err(ALIAS_SRC + "fn f() { let mut s: S = S{ a: 1 }; two_mut(&mut s, &mut s); }")
    assert "`s` is borrowed mutably and also used in the same argument list" in err


def test_shared_and_mutable_borrow_is_an_error():
    err = compile_err(ALIAS_SRC + "fn f() { let mut s: S = S{ a: 1 }; mix(&s, &mut s); }")
    assert "borrowed mutably" in err


def test_mutable_borrow_and_plain_use_is_an_error():
    src = ALIAS_SRC + "fn f() { let mut s: S = S{ a: 1 }; take(&mut s, s.a); }"
    assert "borrowed mutably" in compile_err(src)


def test_distinct_variables_are_fine():
    compile_ok(
        ALIAS_SRC
        + "fn f() { let mut a: S = S{ a: 1 }; let mut b: S = S{ a: 2 }; two_mut(&mut a, &mut b); }"
    )


def test_passing_a_borrow_parameter_twice_is_fine():
    # 새 대여를 만드는 것이 아니다 (language.md §7)
    compile_ok(ALIAS_SRC + "fn g(p: &S) { two(p, p); }")


def test_root_is_found_through_index_and_field():
    src = """
struct S { a: i32 }
fn take(x: &mut S, n: i32) { }
fn f(arr: []mut S) { take(&mut arr[0], arr[1].a); }
"""
    assert "`arr` is borrowed mutably" in compile_err(src)


def test_borrow_of_immutable_place_as_mut():
    src = ALIAS_SRC + "fn f() { let s: S = S{ a: 1 }; take(&mut s, 0); }"
    assert "cannot borrow an immutable place as `&mut`" in compile_err(src)


def test_borrow_type_must_match():
    src = ALIAS_SRC + "fn f() { let mut s: S = S{ a: 1 }; one(&mut s); }"
    assert "expected `&S`, found `&mut S`" in compile_err(src)


# --- match (language.md §6) ------------------------------------------------------------


ENUM_SRC = "enum E { A, B(i32), C(i32, u32) }\n"


def test_match_must_be_exhaustive():
    err = compile_err(ENUM_SRC + fn("let e: E = E.A; match e { A => { } }"))
    assert "non-exhaustive match: missing `B`" in err


def test_wildcard_makes_it_exhaustive():
    compile_ok(ENUM_SRC + fn("let e: E = E.A; match e { A => { } _ => { } }"))


def test_wildcard_must_be_last():
    err = compile_err(ENUM_SRC + fn("let e: E = E.A; match e { _ => { } A => { } }"))
    assert "`_` must be the last arm" in err


def test_duplicate_arm():
    err = compile_err(ENUM_SRC + fn("let e: E = E.A; match e { A => { } A => { } _ => { } }"))
    assert "duplicate arm for `A`" in err


def test_unknown_variant():
    err = compile_err(ENUM_SRC + fn("let e: E = E.A; match e { Z => { } _ => { } }"))
    assert "`E` has no variant `Z`" in err


def test_binding_arity_must_match():
    err = compile_err(ENUM_SRC + fn("let e: E = E.A; match e { A => { } B => { } _ => { } }"))
    assert "variant `B` takes 1 binding(s), found 0" in err


def test_match_requires_an_enum():
    assert "`match` requires an enum" in compile_err(fn("let x: i32 = 1; match x { }"))


def test_match_bindings_are_immutable():
    src = ENUM_SRC + fn("let e: E = E.B(1); match e { A => { } B(x) => { x = 2; } C(a, b) => { } }")
    assert "cannot assign to an immutable place" in compile_err(src)


def test_match_is_a_statement_not_an_expression():
    assert "expected expression" in compile_err(fn("let x = match e { };"))


def test_enum_literal_arity():
    assert "variant `B` takes 1 value(s)" in compile_err(ENUM_SRC + fn("let e: E = E.B;"))


def test_enum_literal_only_as_initializer():
    src = ENUM_SRC + "fn g(p: &E) { }\n" + fn("g(&E.A);")
    assert "enum literal is only allowed as an initializer" in compile_err(src)


# --- unsafe (language.md §6) -----------------------------------------------------------


def test_raw_deref_needs_unsafe():
    assert compile_err(fn("let p: *u32 = 0 as *u32; let x = p.^;")) == (
        "1:44: raw pointer dereference requires `unsafe`"
    )


def test_raw_deref_inside_unsafe_is_fine():
    compile_ok(fn("let p: *u32 = 0 as *u32; unsafe { let x = p.^; }"))


def test_borrow_deref_does_not_need_unsafe():
    compile_ok("struct S { a: i32 } fn g(p: &S) -> i32 { return p.^.a; }")


def test_unsafe_does_not_unlock_anything_else():
    src = "struct S { a: i32 } fn g(s: S) { } fn f() { unsafe { } }"
    assert "cannot pass aggregate" in compile_err(src)


# --- 흐름 (language.md §6) -------------------------------------------------------------


def test_missing_return():
    assert compile_err("fn f() -> i32 { }") == (
        "1:1: function `f` must return a value on every path"
    )


def test_return_on_both_branches_is_enough():
    compile_ok("fn f(c: bool) -> i32 { if c { return 1; } else { return 2; } }")


def test_one_branch_is_not_enough():
    assert "must return a value" in compile_err(
        "fn f(c: bool) -> i32 { if c { return 1; } }"
    )


def test_infinite_loop_diverges():
    compile_ok("fn f() -> i32 { for { } }")


def test_infinite_loop_with_break_does_not():
    assert "must return a value" in compile_err("fn f() -> i32 { for { break; } }")


def test_exhaustive_match_diverges():
    src = ENUM_SRC + "fn f(e: &E) -> i32 { match e.^ { A => { return 0; } B(x) => { return x; } C(a, b) => { return a; } } }"
    compile_ok(src)


def test_return_type_mismatch():
    assert "expected `i32`, found `bool`" in compile_err(
        "fn f() -> i32 { return true; }"
    )


def test_return_value_from_void_function():
    assert compile_err("fn f() { return 1; }") == "1:10: function has no return type"


def test_missing_return_value():
    assert "expected `i32`, found no value" in compile_err(
        "fn f() -> i32 { return; }"
    )


def test_break_outside_loop():
    assert compile_err(fn("break;")) == "1:10: `break` outside of a loop"


def test_continue_outside_loop():
    assert compile_err(fn("continue;")) == "1:10: `continue` outside of a loop"


def test_break_in_nested_function_scope_of_loop_is_fine():
    compile_ok(fn("for { if true { break; } }"))


# --- 이름 -----------------------------------------------------------------


def test_duplicate_top_level_name():
    assert compile_err("fn f() { } fn f() { }") == "1:12: duplicate top-level name `f`"
    assert "duplicate top-level name `x`" in compile_err("struct x { a: i32 } fn x() { }")


def test_unknown_name():
    assert compile_err(fn("let x = y;")) == "1:18: unknown name `y`"


def test_unknown_function():
    assert compile_err(fn("g();")) == "1:11: unknown function `g`"


def test_unknown_type():
    assert compile_err("fn f(a: Foo) { }") == "1:9: unknown type `Foo`"


def test_functions_are_not_values():
    assert compile_err("fn g() { } " + fn("let x = g;")) == "1:29: functions are not values"


def test_duplicate_local_in_same_scope():
    assert "`x` is already declared in this scope" in compile_err(
        fn("let x: i32 = 1; let x: i32 = 2;")
    )


def test_shadowing_a_block_is_allowed():
    compile_ok(fn("let x: i32 = 1; if true { let x: i32 = 2; }"))


def test_local_cannot_shadow_a_top_level_name():
    assert "shadows a top-level name" in compile_err("struct S { a: i32 } fn f() { let S: i32 = 1; }")


def test_argument_count():
    assert compile_err("fn g(a: i32) { } " + fn("g();")) == (
        "1:28: `g` takes 1 argument(s), found 0"
    )


def test_top_level_declarations_are_order_independent():
    compile_ok("fn f() -> i32 { return g(); } fn g() -> i32 { return C; } const C: i32 = 7;")


def test_mutual_recursion():
    compile_ok(
        "fn even(n: u32) -> bool { if n == 0 { return true; } return odd(n - 1); }"
        "fn odd(n: u32) -> bool { if n == 0 { return false; } return even(n - 1); }"
    )


# --- const (language.md §4) ------------------------------------------------------------


def test_const_must_be_scalar():
    assert "const must have type i32, u32 or bool" in compile_err('const C: []u8 = "x";')


def test_const_cycle():
    assert "depends on itself" in compile_err("const A: i32 = B; const B: i32 = A;")


def test_const_self_reference():
    assert "depends on itself" in compile_err("const A: i32 = A;")


def test_const_division_by_zero():
    assert "division by zero in constant expression" in compile_err("const A: i32 = 1 / 0;")


def test_const_is_not_a_place():
    assert "`C` is a constant, not a place" in compile_err(
        "const C: i32 = 1; " + fn("C = 2;")
    )


def test_non_constant_expression():
    assert "not a constant expression" in compile_err(
        "fn g() -> i32 { return 1; } const C: i32 = g();"
    )
