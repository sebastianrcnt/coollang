"""구문 분석 (SPEC.md §4, §5, §6)."""

import pytest

from cool0.cool0 import (
    Assign, Binary, Borrow, Break, Call, Cast, CompileError, Continue, Deref,
    EnumDecl, Field, For, FnDecl, Ident, If, Index, Int, Let, Match, Parser,
    Return, StructDecl, StructLit, Unary, Unsafe, lex,
)


def parse(src: str):
    return Parser(lex(src.encode("ascii"))).parse_program()


def expr(src: str):
    """식 하나를 파싱한다. `fn f() { let x = <식>; }` 로 감싼다."""
    return parse("fn f() { let x = " + src + "; }")[0].body[0].init


def stmt(src: str):
    return parse("fn f() { " + src + " }")[0].body[0]


def error(src: str) -> str:
    with pytest.raises(CompileError) as ei:
        parse(src)
    return f"{ei.value.line}:{ei.value.col}: {ei.value.msg}"


def shape(e):
    """식을 괄호 붙인 문자열로. 우선순위 검사용."""
    if isinstance(e, Binary):
        return f"({shape(e.lhs)} {e.op} {shape(e.rhs)})"
    if isinstance(e, Unary):
        return f"({e.op}{shape(e.operand)})"
    if isinstance(e, Borrow):
        return f"(&{'mut ' if e.mut else ''}{shape(e.operand)})"
    if isinstance(e, Cast):
        return f"({shape(e.operand)} as {e.ty_node.name or e.ty_node.kind})"
    if isinstance(e, Call):
        return f"{shape(e.callee)}({', '.join(shape(a) for a in e.args)})"
    if isinstance(e, Index):
        return f"{shape(e.base)}[{shape(e.index)}]"
    if isinstance(e, Field):
        return f"{shape(e.base)}.{e.name}"
    if isinstance(e, Deref):
        return f"{shape(e.base)}.^"
    if isinstance(e, Ident):
        return e.name
    if isinstance(e, Int):
        return str(e.value)
    return type(e).__name__


# --- 우선순위 (§5) ---------------------------------------------------------


@pytest.mark.parametrize(
    "src,want",
    [
        # 곱셈이 덧셈보다
        ("a + b * c", "(a + (b * c))"),
        ("a * b + c", "((a * b) + c)"),
        # 덧셈이 시프트보다
        ("a << b + c", "(a << (b + c))"),
        # 시프트가 & 보다
        ("a & b << c", "(a & (b << c))"),
        # & ^ | 순서
        ("a | b ^ c & d", "(a | (b ^ (c & d)))"),
        ("a & b ^ c | d", "(((a & b) ^ c) | d)"),
        # | 가 비교보다
        ("a | b == c | d", "((a | b) == (c | d))"),
        # 비교가 && 보다
        ("a == b && c != d", "((a == b) && (c != d))"),
        # && 가 || 보다
        ("a && b || c && d", "((a && b) || (c && d))"),
        # 결합 방향
        ("a - b - c", "((a - b) - c)"),
        ("a / b / c", "((a / b) / c)"),
        # 단항이 as 보다 세다
        ("-a as i32", "((-a) as i32)"),
        ("!a as i32", "((!a) as i32)"),
        # as 가 곱셈보다 세다
        ("a * b as i32", "(a * (b as i32))"),
        ("a as i32 * b", "((a as i32) * b)"),
        # 후위가 단항보다 세다
        ("-a[0]", "(-a[0])"),
        ("!p.^", "(!p.^)"),
        ("&mut s.f", "(&mut s.f)"),
        # 후위 연쇄
        ("a.b[0].c.^", "a.b[0].c.^"),
        ("f(x)[1].y", "f(x)[1].y"),
        # 괄호
        ("(a + b) * c", "((a + b) * c)"),
    ],
)
def test_precedence(src, want):
    assert shape(expr(src)) == want


def test_comparison_cannot_chain():
    # 위치는 두 번째 비교 연산자다
    assert error("fn f() { let x = a < b < c; }") == (
        "1:24: comparison operators cannot be chained"
    )


def test_comparison_chain_via_parens_is_fine():
    assert shape(expr("(a < b) < c")) == "((a < b) < c)"


# --- 선언 (§4) -------------------------------------------------------------


def test_function_without_return_type():
    d = parse("fn f() { }")[0]
    assert isinstance(d, FnDecl) and d.ret is None and d.params == []


def test_function_params_and_return():
    d = parse("fn f(a: i32, b: []mut u8) -> u32 { return 0; }")[0]
    assert [p[1] for p in d.params] == ["a", "b"]
    assert d.ret.name == "u32"


def test_trailing_comma_in_params():
    assert len(parse("fn f(a: i32,) { }")[0].params) == 1


def test_struct_and_enum():
    ds = parse("struct S { a: i32, b: u8 } enum E { A, B(i32), C(i32, u32) }")
    assert isinstance(ds[0], StructDecl) and [f[1] for f in ds[0].fields] == ["a", "b"]
    assert isinstance(ds[1], EnumDecl)
    assert [(v[1], len(v[2])) for v in ds[1].variants] == [("A", 0), ("B", 1), ("C", 2)]


def test_top_level_order_does_not_matter_syntactically():
    ds = parse("fn f() { } const C: i32 = 1; struct S { a: i32 }")
    assert [type(d).__name__ for d in ds] == ["FnDecl", "ConstDecl", "StructDecl"]


def test_bad_top_level():
    assert error("let x: i32 = 1;") == (
        "1:1: expected `fn`, `struct`, `enum` or `const`, found `let`"
    )


# --- 타입 문법 -------------------------------------------------------------


@pytest.mark.parametrize(
    "src,kind,mut",
    [
        ("[]u8", "slice", False),
        ("[]mut u8", "slice", True),
        ("&S", "ref", False),
        ("&mut S", "ref", True),
        ("*u8", "ptr", False),
    ],
)
def test_type_forms(src, kind, mut):
    t = parse("fn f(a: " + src + ") { }")[0].params[0][2]
    assert t.kind == kind and t.mut == mut


def test_nested_types():
    t = parse("fn f(a: []mut *u8) { }")[0].params[0][2]
    assert t.kind == "slice" and t.mut and t.inner.kind == "ptr"


# --- 문 (§6) ---------------------------------------------------------------


def test_let_forms():
    assert isinstance(stmt("let x: i32 = 1;"), Let)
    assert stmt("let mut x: i32 = 1;").mut is True
    assert stmt("let x = 1;").ty_node is None


def test_assignment_operators():
    for op in ["=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="]:
        s = stmt("x " + op + " 1;")
        assert isinstance(s, Assign) and s.op == op


def test_call_statement():
    assert isinstance(stmt("f(1);").expr, Call)


def test_non_call_expression_statement_is_rejected():
    assert error("fn f() { 1 + 2; }") == "1:10: expression statement must be a call"


def test_three_for_forms():
    a = stmt("for { }")
    assert isinstance(a, For) and a.init is None and a.cond is None and a.post is None
    b = stmt("for x { }")
    assert b.init is None and b.cond is not None and b.post is None
    c = stmt("for let mut i = 0; i < n; i += 1 { }")
    assert isinstance(c.init, Let) and c.cond is not None and isinstance(c.post, Assign)


def test_if_else_chain():
    s = stmt("if a { } else if b { } else { }")
    assert isinstance(s, If) and isinstance(s.els[0], If) and s.els[0].els == []


def test_break_continue_return():
    assert isinstance(stmt("break;"), Break)
    assert isinstance(stmt("continue;"), Continue)
    assert stmt("return;").value is None
    assert stmt("return 1;").value is not None


def test_match_arms():
    s = stmt("match e { A => { } B(x) => { } C(x, y) => { } _ => { } }")
    assert isinstance(s, Match)
    assert [(a.variant, [b[1] for b in a.binds]) for a in s.arms] == [
        ("A", []), ("B", ["x"]), ("C", ["x", "y"]), (None, []),
    ]


def test_match_arm_body_must_be_a_block():
    assert "expected `{`" in error("fn f() { match e { A => 1 } }")


def test_unsafe_block():
    assert isinstance(stmt("unsafe { }"), Unsafe)


# --- 집합체 리터럴의 자리 ---------------------------------------------------


def test_struct_literal_in_initializer():
    assert isinstance(expr("S{ a: 1, b: 2 }"), StructLit)


def test_struct_literal_in_assignment_rhs():
    assert isinstance(stmt("x = S{ a: 1 };").value, StructLit)


def test_struct_literal_not_parsed_in_if_header():
    # `if S { }` 의 `{` 는 블록이다. 리터럴이 아니다
    s = stmt("if S { }")
    assert isinstance(s, If) and isinstance(s.cond, Ident)


def test_struct_literal_not_parsed_in_for_header():
    s = stmt("for S { }")
    assert isinstance(s, For) and isinstance(s.cond, Ident)


def test_struct_literal_not_parsed_in_match_header():
    s = stmt("match S { }")
    assert isinstance(s, Match) and isinstance(s.scrutinee, Ident)


def test_enum_literal_parses_as_field_and_call():
    assert shape(expr("E.A")) == "E.A"
    assert shape(expr("E.B(1)")) == "E.B(1)"


# --- 오류 위치 -------------------------------------------------------------


def test_missing_semicolon():
    assert error("fn f() { let x: i32 = 1 }") == "1:25: expected `;`, found `}`"


def test_unclosed_block():
    assert error("fn f() {") == "1:9: expected `}`, found end of file"


def test_expected_expression():
    assert error("fn f() { let x = ; }") == "1:18: expected expression, found `;`"
