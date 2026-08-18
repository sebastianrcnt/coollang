"""cool0c 를 단계별로 cool0.py 와 맞춰 본다 (README 의 "단계마다 패리티").

마일스톤 파일이 아니다. 여기는 cool0c 를 기른 작업대이며, 등식이 깨졌을 때 어느
단계에서 깨졌는지 -- 렉서인지 파서인지 검사기인지 방출인지 -- 를 짚어 준다.
최종 목표 자체는 tests/test_milestone.py 에 등식 하나로 있다.
"""

from __future__ import annotations

import pathlib

import pytest
import wasmtime

from conftest import ENGINE
from cool0.cool0 import STATUS_OK, compile as reference_compile, lex as reference_lex

SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "cool0"
COOL0C = SRC_DIR / "cool0c.cool0"

# cool0c 의 전역 주소. cool0c.cool0 과 같아야 한다
G_ERR = 0x0014
G_NTOK = 0x002C
G_TOKS = 0x0030
SRC_ADDR = 0x1000
TOK_SIZE = 32  # kind start len value line col aux punct

KIND_NAME = {0: "eof", 1: "ident", 2: "int", 3: "char", 4: "str", 5: "kw", 6: "punct"}

KEYWORDS = """fn struct enum const let mut if else for break continue
              return match unsafe as true false slice slice_mut offset""".split()
PUNCT = [
    "<<=", ">>=", "->", "=>", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "(", ")", "{", "}", "[", "]", ",", ";", ":", ".", "=", "<", ">",
    "+", "-", "*", "/", "%", "&", "|", "^", "!",
]

needs_cool0c = pytest.mark.skipif(not COOL0C.exists(), reason="cool0c.cool0 이 아직 없다")


class Cool0c:
    """cool0.py 로 컴파일한 cool0c 하나. 내부를 들여다볼 수 있다."""

    def __init__(self):
        status, wasm = reference_compile(COOL0C.read_bytes())
        assert status == STATUS_OK, wasm.decode("ascii", "replace")
        self.wasm = wasm
        self.store = wasmtime.Store(ENGINE)
        self.inst = wasmtime.Instance(self.store, wasmtime.Module(ENGINE, wasm), [])
        self.ex = self.inst.exports(self.store)
        self.mem = self.ex["memory"]

    def u32(self, addr: int) -> int:
        return int.from_bytes(bytes(self.mem.read(self.store, addr, addr + 4)), "little")

    def read(self, addr: int, n: int) -> bytes:
        return bytes(self.mem.read(self.store, addr, addr + n))

    def compile(self, src: bytes) -> tuple[int, bytes]:
        self.mem.write(self.store, src, SRC_ADDR)
        status = self.ex["compile"](self.store, len(src))
        ptr, length = self.u32(0), self.u32(4)
        return status, self.read(ptr, length)

    def tokens(self) -> list[tuple]:
        """(kind, line, col, value-ish) 목록. cool0.py 와 견줄 수 있는 모양으로."""
        base, n = self.u32(G_TOKS), self.u32(G_NTOK)
        out = []
        for i in range(n):
            t = base + i * TOK_SIZE
            kind = self.u32(t)
            start, ln, value = self.u32(t + 4), self.u32(t + 8), self.u32(t + 12)
            line, col, aux = self.u32(t + 16), self.u32(t + 20), self.u32(t + 24)
            punct = self.u32(t + 28)
            name = KIND_NAME[kind]
            if name in ("ident",):
                v = self.read(SRC_ADDR + start, ln).decode("ascii")
            elif name == "kw":
                v = KEYWORDS[value]
            elif name == "punct":
                v = PUNCT[punct]
            elif name == "str":
                v = self.read(value, aux)
            elif name in ("int", "char"):
                v = value
            else:
                v = ""
            out.append((name, v, line, col))
        return out


@pytest.fixture(scope="module")
def cc() -> Cool0c:
    return Cool0c()


def reference_tokens(src: bytes) -> list[tuple]:
    out = []
    for t in reference_lex(src):
        v = t.value if t.kind in ("int", "char", "str") else (t.text if t.kind != "eof" else "")
        out.append((t.kind, v, t.line, t.col))
    return out


# --- 1 단계: 렉서 (language.md §2) ----------------------------------------------

LEX_CASES = [
    "",
    "fn f() { }",
    "  \t\n  ",
    "// comment only",
    "a // trailing\nb",
    "_x9 A_b zz",
    "fn struct enum const let mut if else for break continue return match unsafe as true false",
    "0 123 0xFF 0xff 0b1010 1_000_000 0xDEAD_BEEF 4294967295",
    "'a' '\\n' '\\t' '\\r' '\\0' '\\\\' '\\''",
    '"" "abc" "a\\nb\\"c" "\\\\"',
    "<<= >>= -> => == != <= >= && || << >> += -= *= /= %= &= |= ^=",
    "( ) { } [ ] , ; : . = < > + - * / % & | ^ !",
    "&mut x .^ a[i] s.f",
    "x<<=1 y>>=2 a<=b c>=d",
    "let mut i: u32 = 0;\nfor i < 3 { i += 1; }\n",
    'struct S { a: i32 }\nenum E { A, B(i32) }\nconst C: u32 = 7;\nfn g(p: &mut S) -> i32 { return p.^.a; }',
]


@needs_cool0c
@pytest.mark.parametrize("src", LEX_CASES, ids=range(len(LEX_CASES)))
def test_lexer_matches_the_oracle(cc, src):
    # 조각들은 프로그램이 아니라 파서가 거절할 수 있다. 여기서 보는 것은 토큰이다
    cc.compile(src.encode("ascii"))
    assert cc.u32(G_ERR) == 0 or cc.tokens()
    assert cc.tokens() == reference_tokens(src.encode("ascii"))


LEX_ERRORS = [
    b"\xed\x95\x9c",
    b"fn f() { }\n// \xff",
    b"4294967296",
    b"123abc",
    b"0x",
    b"0b12",
    b"''",
    b"'ab'",
    b"'\\a'",
    b'"abc',
    b'"abc\n"',
    b'"\\a"',
    b"@",
    b"~",
    b"\x00",
    b"\x7f",
    b"'\x01'",
    b'"\x01"',
]


@needs_cool0c
@pytest.mark.parametrize("src", LEX_ERRORS, ids=range(len(LEX_ERRORS)))
def test_lexer_diagnostics_match_the_oracle(cc, src):
    got = cc.compile(src)
    want = reference_compile(src)
    assert got[1] == want[1], f"{got[1]!r} != {want[1]!r}"
    assert got[0] == want[0]


@needs_cool0c
def test_lexer_handles_its_own_source(cc):
    src = COOL0C.read_bytes()
    status, out = cc.compile(src)
    assert status == STATUS_OK, out.decode("ascii", "replace")
    assert cc.tokens() == reference_tokens(src)


# --- 2 단계: 파서 (language.md §4, §5, §6) ---------------------------------------
#
# 양쪽 AST 를 같은 S-식으로 찍어서 문자열로 비교한다. 위치와 깊이까지 들어간다.

from cool0.cool0 import (  # noqa: E402
    Assign, Binary, Bool, Borrow, Break, Call, Cast, Char, ConstDecl, Continue,
    Deref, EnumDecl, Field, FnDecl, For, Ident, If, Index, Int, Let, Match,
    Parser, Return, Str, StructDecl, StructLit, TyNode, Unary, Unsafe,
)
from cool0.cool0 import OffsetExpr, SliceExpr  # noqa: E402
from cool0.cool0 import ExprStmt  # noqa: E402

G_DECLS = 0x0040
G_NODES = 0x0044

NK = {
    1: "int", 2: "char", 3: "str", 4: "bool", 5: "ident", 6: "unary", 7: "binary",
    8: "borrow", 9: "cast", 10: "call", 11: "index", 12: "field", 13: "deref",
    14: "structlit", 15: "litfield", 16: "sliceexpr", 17: "offset",
    20: "prim", 21: "named", 22: "slice", 23: "ref", 24: "ptr",
    30: "let", 31: "assign", 32: "expr", 33: "if", 34: "for", 35: "break",
    36: "continue", 37: "return", 38: "match", 39: "unsafe", 40: "arm", 41: "bind",
    50: "fn", 51: "struct", 52: "enum", 53: "const", 54: "param", 55: "field",
    56: "variant",
}
NODE_SIZE = 56
F = {"KIND": 0, "LINE": 4, "COL": 8, "A": 12, "B": 16, "C": 20, "D": 24,
     "E": 28, "NEXT": 32, "TY": 36, "AUX": 40, "AUX2": 44, "DEPTH": 48,
     "OP": 52}  # 연산자는 이제 Punct 라 자기 필드에 있다


class Dump:
    """cool0c 의 노드 아레나를 S-식으로.

    노드 참조는 주소가 아니라 아레나 인덱스다 (0 이 "없음"). 아레나 밑동은
    G_NODES 에 있다.
    """

    def __init__(self, cc: Cool0c):
        self.cc = cc
        self.base = cc.u32(G_NODES)

    def f(self, n: int, name: str) -> int:
        return self.cc.u32(self.base + n * NODE_SIZE + F[name])

    def name(self, n: int, sf="A", lf="B") -> str:
        return self.cc.read(SRC_ADDR + self.f(n, sf), self.f(n, lf)).decode("ascii")

    def lst(self, head: int, fn) -> list:
        out = []
        while head:
            out.append(fn(head))
            head = self.f(head, "NEXT")
        return out

    def pos(self, n: int) -> str:
        return f"{self.f(n, 'LINE')} {self.f(n, 'COL')}"

    def ty(self, n: int) -> str:
        if not n:
            return "nil"
        k = NK[self.f(n, "KIND")]
        if k == "prim":
            return f"(prim {self.pos(n)} {self.f(n, 'A')})"
        if k == "named":
            return f"(named {self.pos(n)} {self.name(n)})"
        if k == "slice":
            return f"(slice {self.pos(n)} {self.f(n, 'B')} {self.ty(self.f(n, 'A'))})"
        if k == "ref":
            return f"(ref {self.pos(n)} {self.f(n, 'B')} {self.ty(self.f(n, 'A'))})"
        return f"(ptr {self.pos(n)} {self.ty(self.f(n, 'A'))})"

    def ex(self, n: int) -> str:
        if not n:
            return "nil"
        k = NK[self.f(n, "KIND")]
        d = self.f(n, "DEPTH")
        p = self.pos(n)
        if k in ("int", "char", "bool"):
            return f"({k} {p} {d} {self.f(n, 'A')})"
        if k == "str":
            b = self.cc.read(self.f(n, "A"), self.f(n, "B"))
            return f"(str {p} {d} {b!r})"
        if k == "ident":
            return f"(ident {p} {d} {self.name(n)})"
        if k == "unary":
            return f"(unary {p} {d} {PUNCT[self.f(n, 'OP')]} {self.ex(self.f(n, 'B'))})"
        if k == "binary":
            return (f"(binary {p} {d} {PUNCT[self.f(n, 'OP')]} "
                    f"{self.ex(self.f(n, 'B'))} {self.ex(self.f(n, 'C'))})")
        if k == "borrow":
            return f"(borrow {p} {d} {self.f(n, 'A')} {self.ex(self.f(n, 'B'))})"
        if k == "cast":
            return f"(cast {p} {d} {self.ex(self.f(n, 'A'))} {self.ty(self.f(n, 'B'))})"
        if k == "call":
            args = " ".join(self.lst(self.f(n, "B"), self.ex))
            return f"(call {p} {d} {self.ex(self.f(n, 'A'))} [{args}])"
        if k == "index":
            return f"(index {p} {d} {self.ex(self.f(n, 'A'))} {self.ex(self.f(n, 'B'))})"
        if k == "field":
            nm = self.cc.read(SRC_ADDR + self.f(n, "B"), self.f(n, "C")).decode("ascii")
            return f"(field {p} {d} {self.ex(self.f(n, 'A'))} {nm})"
        if k == "deref":
            return f"(deref {p} {d} {self.ex(self.f(n, 'A'))})"
        if k == "structlit":
            fs = " ".join(self.lst(self.f(n, "C"), self.litfield))
            return f"(structlit {p} {d} {self.name(n)} [{fs}])"
        if k == "sliceexpr":
            return (f"(sliceexpr {p} {d} {self.f(n, 'A')} "
                    f"{self.ex(self.f(n, 'B'))} {self.ex(self.f(n, 'C'))})")
        if k == "offset":
            return f"(offset {p} {d} {self.ex(self.f(n, 'A'))} {self.ex(self.f(n, 'B'))})"
        raise AssertionError(k)

    def litfield(self, n: int) -> str:
        return (f"(f {self.pos(n)} {self.f(n, 'DEPTH')} {self.name(n)} "
                f"{self.ex(self.f(n, 'C'))})")

    def block(self, head: int) -> str:
        return "[" + " ".join(self.lst(head, self.stmt)) + "]"

    def stmt(self, n: int) -> str:
        k = NK[self.f(n, "KIND")]
        p = self.pos(n)
        if k == "let":
            return (f"(let {p} {self.f(n, 'E')} {self.name(n)} "
                    f"{self.ty(self.f(n, 'C'))} {self.ex(self.f(n, 'D'))})")
        if k == "assign":
            return (f"(assign {p} {PUNCT[self.f(n, 'OP')]} "
                    f"{self.ex(self.f(n, 'A'))} {self.ex(self.f(n, 'C'))})")
        if k == "expr":
            return f"(expr {p} {self.ex(self.f(n, 'A'))})"
        if k == "if":
            els = self.block(self.f(n, "C")) if self.f(n, "D") else "nil"
            return f"(if {p} {self.ex(self.f(n, 'A'))} {self.block(self.f(n, 'B'))} {els})"
        if k == "for":
            init = self.stmt(self.f(n, "A")) if self.f(n, "A") else "nil"
            post = self.stmt(self.f(n, "C")) if self.f(n, "C") else "nil"
            return (f"(for {p} {init} {self.ex(self.f(n, 'B'))} {post} "
                    f"{self.block(self.f(n, 'D'))})")
        if k in ("break", "continue"):
            return f"({k} {p})"
        if k == "return":
            return f"(return {p} {self.ex(self.f(n, 'A'))})"
        if k == "match":
            arms = " ".join(self.lst(self.f(n, "B"), self.arm))
            return f"(match {p} {self.ex(self.f(n, 'A'))} [{arms}])"
        if k == "unsafe":
            return f"(unsafe {p} {self.block(self.f(n, 'A'))})"
        raise AssertionError(k)

    def arm(self, n: int) -> str:
        # `A | B` 는 첫 이름이 A/B 필드에, 나머지가 AUX2 의 S_ALT 목록에 있다
        v = "_" if self.f(n, "E") else "|".join(
            [self.name(n)] + self.lst(self.f(n, "AUX2"), self.name)
        )
        binds = " ".join(self.lst(self.f(n, "C"), lambda b: self.name(b)))
        return f"(arm {self.pos(n)} {v} [{binds}] {self.block(self.f(n, 'D'))})"

    def decl(self, n: int) -> str:
        k = NK[self.f(n, "KIND")]
        p = self.pos(n)
        if k == "fn":
            ps = " ".join(self.lst(self.f(n, "C"), self.param))
            ret = self.ty(self.f(n, "D"))
            return f"(fn {p} {self.name(n)} [{ps}] {ret} {self.block(self.f(n, 'E'))})"
        if k == "struct":
            fs = " ".join(self.lst(self.f(n, "C"), self.param))
            return f"(struct {p} {self.name(n)} [{fs}])"
        if k == "enum":
            vs = " ".join(self.lst(self.f(n, "C"), self.variant))
            return f"(enum {p} {self.name(n)} [{vs}])"
        return (f"(const {p} {self.name(n)} {self.ty(self.f(n, 'C'))} "
                f"{self.ex(self.f(n, 'D'))})")

    def param(self, n: int) -> str:
        return f"({self.pos(n)} {self.name(n)} {self.ty(self.f(n, 'C'))})"

    def variant(self, n: int) -> str:
        ts = " ".join(self.lst(self.f(n, "C"), self.ty))
        return f"({self.pos(n)} {self.name(n)} [{ts}])"

    def program(self) -> str:
        return "\n".join(self.lst(self.cc.u32(G_DECLS), self.decl))


PRIM_IDX = {"i32": 0, "u32": 1, "bool": 2, "u8": 3}


class RefDump:
    """cool0.py 의 AST 를 같은 S-식으로."""

    def pos(self, n) -> str:
        return f"{n.pos[0]} {n.pos[1]}"

    def d(self, n) -> int:
        return getattr(n, "depth", 1)

    def ty(self, t) -> str:
        if t is None:
            return "nil"
        if t.kind == "prim":
            return f"(prim {self.pos(t)} {PRIM_IDX[t.name]})"
        if t.kind == "named":
            return f"(named {self.pos(t)} {t.name})"
        if t.kind == "slice":
            return f"(slice {self.pos(t)} {int(t.mut)} {self.ty(t.inner)})"
        if t.kind == "ref":
            return f"(ref {self.pos(t)} {int(t.mut)} {self.ty(t.inner)})"
        return f"(ptr {self.pos(t)} {self.ty(t.inner)})"

    def ex(self, e) -> str:
        if e is None:
            return "nil"
        p, d = self.pos(e), self.d(e)
        if isinstance(e, Int):
            return f"(int {p} {d} {e.value})"
        if isinstance(e, Char):
            return f"(char {p} {d} {e.value})"
        if isinstance(e, Bool):
            return f"(bool {p} {d} {int(e.value)})"
        if isinstance(e, Str):
            return f"(str {p} {d} {e.value!r})"
        if isinstance(e, Ident):
            return f"(ident {p} {d} {e.name})"
        if isinstance(e, Unary):
            return f"(unary {p} {d} {e.op} {self.ex(e.operand)})"
        if isinstance(e, Binary):
            return f"(binary {p} {d} {e.op} {self.ex(e.lhs)} {self.ex(e.rhs)})"
        if isinstance(e, Borrow):
            return f"(borrow {p} {d} {int(e.mut)} {self.ex(e.operand)})"
        if isinstance(e, Cast):
            return f"(cast {p} {d} {self.ex(e.operand)} {self.ty(e.ty_node)})"
        if isinstance(e, Call):
            args = " ".join(self.ex(a) for a in e.args)
            return f"(call {p} {d} {self.ex(e.callee)} [{args}])"
        if isinstance(e, Index):
            return f"(index {p} {d} {self.ex(e.base)} {self.ex(e.index)})"
        if isinstance(e, Field):
            return f"(field {p} {d} {self.ex(e.base)} {e.name})"
        if isinstance(e, Deref):
            return f"(deref {p} {d} {self.ex(e.base)})"
        if isinstance(e, SliceExpr):
            return (f"(sliceexpr {p} {d} {1 if e.mut else 0} "
                    f"{self.ex(e.ptr)} {self.ex(e.length)})")
        if isinstance(e, OffsetExpr):
            return f"(offset {p} {d} {self.ex(e.ptr)} {self.ex(e.index)})"
        if isinstance(e, StructLit):
            fs = " ".join(
                f"(f {fp[0]} {fp[1]} {self.d(v)} {nm} {self.ex(v)})"
                for fp, nm, v in e.fields
            )
            return f"(structlit {p} {d} {e.name} [{fs}])"
        raise AssertionError(type(e))

    def block(self, stmts) -> str:
        return "[" + " ".join(self.stmt(s) for s in stmts) + "]"

    def stmt(self, s) -> str:
        p = self.pos(s)
        if isinstance(s, Let):
            return (f"(let {p} {int(s.mut)} {s.name} {self.ty(s.ty_node)} "
                    f"{self.ex(s.init)})")
        if isinstance(s, Assign):
            return f"(assign {p} {s.op} {self.ex(s.target)} {self.ex(s.value)})"
        if isinstance(s, ExprStmt):
            return f"(expr {p} {self.ex(s.expr)})"
        if isinstance(s, If):
            els = self.block(s.els) if s.els is not None else "nil"
            return f"(if {p} {self.ex(s.cond)} {self.block(s.then)} {els})"
        if isinstance(s, For):
            init = self.stmt(s.init) if s.init is not None else "nil"
            post = self.stmt(s.post) if s.post is not None else "nil"
            return f"(for {p} {init} {self.ex(s.cond)} {post} {self.block(s.body)})"
        if isinstance(s, Break):
            return f"(break {p})"
        if isinstance(s, Continue):
            return f"(continue {p})"
        if isinstance(s, Return):
            return f"(return {p} {self.ex(s.value)})"
        if isinstance(s, Match):
            arms = []
            for a in s.arms:
                v = "|".join(a.variants) if a.variants else "_"
                binds = " ".join(b[1] for b in a.binds)
                arms.append(
                    f"(arm {a.pos[0]} {a.pos[1]} {v} [{binds}] {self.block(a.body)})"
                )
            return f"(match {p} {self.ex(s.scrutinee)} [{' '.join(arms)}])"
        if isinstance(s, Unsafe):
            return f"(unsafe {p} {self.block(s.body)})"
        raise AssertionError(type(s))

    def decl(self, d) -> str:
        p = self.pos(d)
        if isinstance(d, FnDecl):
            ps = " ".join(f"({q[0]} {q[1]} {nm} {self.ty(t)})" for q, nm, t in d.params)
            return f"(fn {p} {d.name} [{ps}] {self.ty(d.ret)} {self.block(d.body)})"
        if isinstance(d, StructDecl):
            fs = " ".join(f"({q[0]} {q[1]} {nm} {self.ty(t)})" for q, nm, t in d.fields)
            return f"(struct {p} {d.name} [{fs}])"
        if isinstance(d, EnumDecl):
            vs = []
            for q, nm, pl in d.variants:
                ts = " ".join(self.ty(t) for t in pl)
                vs.append(f"({q[0]} {q[1]} {nm} [{ts}])")
            return f"(enum {p} {d.name} [{' '.join(vs)}])"
        return f"(const {p} {d.name} {self.ty(d.ty_node)} {self.ex(d.value)})"

    def program(self, src: bytes) -> str:
        # cool0.py 는 compile() 안에서만 재귀 한계를 올린다 (MAX_DEPTH 를 받치려고)
        import sys

        saved = sys.getrecursionlimit()
        sys.setrecursionlimit(max(saved, 20000))
        try:
            decls = Parser(reference_lex(src)).parse_program()
            return "\n".join(self.decl(d) for d in decls)
        finally:
            sys.setrecursionlimit(saved)


PARSE_CASES = [
    "fn f() { }",
    "fn f(a: i32, b: []mut u8) -> u32 { return 0; }",
    "fn f(a: i32,) { }",
    "struct S { a: i32, b: u8 }",
    "enum E { A, B(i32), C(i32, u32) }",
    "const C: u32 = 1 + 2 * 3;",
    "fn f() { let x = a + b * c; }",
    "fn f() { let x = a | b ^ c & d; }",
    "fn f() { let x = a && b || c && d; }",
    "fn f() { let x = a == b; }",
    "fn f() { let x = -a as i32; }",
    "fn f() { let x = a * b as i32; }",
    "fn f() { let x = &mut s.f; }",
    "fn f() { let x = a.b[0].c.^; }",
    "fn f() { let x = f(x)[1].y; }",
    "fn f() { let x = (a + b) * c; }",
    "fn f() { let x = a - b - c; }",
    "fn f() { let x = 1 + 2 + 3 + 4 + 5; }",
    "fn f() { let x: i32 = 1; let mut y: u32 = 2; }",
    "fn f() { x = 1; x += 2; x <<= 3; x %= 4; }",
    "fn f() { g(1, 2); }",
    "fn f() { for { } for x { } for let mut i = 0; i < n; i += 1 { } }",
    "fn f() { if a { } else if b { } else { } }",
    "fn f() { break; continue; return; }",
    "fn f() { match e { A => { } B(x) => { } C(x, y) => { } _ => { } } }",
    "fn f() { unsafe { } }",
    "fn f() { let s = S{ a: 1, b: 2 }; }",
    "fn f() { x = S{ a: 1 }; }",
    "fn f() { if S { } }",
    "fn f() { g(&S{ a: 1 }); }",
    "fn f() { let e = E.A; let g = E.B(1); }",
    "fn f() { let x = p.^; }",
    "fn f(a: []mut *u8) { }",
    "fn z() { } const C: i32 = 1; struct S { a: i32 } fn m() { }",
    "fn f() { let x = " + "(" * 60 + "1" + ")" * 60 + "; }",
    "fn f() { let x = " + "1+" * 60 + "1; }",
    "fn f() { let x = a.b.c.d.e; }",
    "fn f() { let x = f(g(h(1)), 2); }",
    "fn f() { let x = arr[i + 1][j]; }",
    "fn f() { let x = 1 as u32 as i32 as *u8; }",
]


@needs_cool0c
@pytest.mark.parametrize("src", PARSE_CASES, ids=range(len(PARSE_CASES)))
def test_parser_matches_the_oracle(cc, src):
    # 조각들은 이름이 없어 검사기가 거절할 수 있다. 여기서 보는 것은 트리다
    cc.compile(src.encode("ascii"))
    assert Dump(cc).program() == RefDump().program(src.encode("ascii"))


PARSE_ERRORS = [
    b"let x: i32 = 1;",
    b"fn f() { let x = a < b < c; }",
    b"fn f() { let x: i32 = 1 }",
    b"fn f() {",
    b"fn f() { let x = ; }",
    b"fn f() { 1 + 2; }",
    b"fn f() { match e { A => 1 } }",
    b"fn f(a: ) { }",
    b"fn f() -> { }",
    b"struct S { a }",
    b"enum E { A( }",
    b"const C = 1;",
    b"fn 1() { }",
    b"fn f() { let = 1; }",
    b"fn f() -> i32 { return " + b"(" * 200 + b"1" + b")" * 200 + b"; }",
    b"fn f() -> i32 { return " + b"1+" * 200 + b"1; }",
    b"fn f() { " + b"if true { " * 200 + b"}" * 200 + b" }",
    b"fn f(a: " + b"*" * 200 + b"u8) { }",
    b"fn f() { if true { } " + b"else if true { } " * 200 + b"}",
    b"fn f() { let x = " + b"-" * 200 + b"1; }",
    b"fn f() { let x = " + b"!" * 200 + b"true; }",
    b"fn f() { let x = p" + b".^" * 200 + b"; }",
    b"fn f() { let x = a" + b".b" * 200 + b"; }",
    b"fn f() { " + b"g(" * 200 + b")" * 200 + b"; }",
    b"fn f() { let x = 1" + b" as i32" * 200 + b"; }",
]


@needs_cool0c
@pytest.mark.parametrize("src", PARSE_ERRORS, ids=range(len(PARSE_ERRORS)))
def test_parser_diagnostics_match_the_oracle(cc, src):
    got = cc.compile(src)
    want = reference_compile(src)
    assert got[1] == want[1], f"{got[1]!r} != {want[1]!r}"


@needs_cool0c
def test_parser_handles_its_own_source(cc):
    src = COOL0C.read_bytes()
    status, out = cc.compile(src)
    assert status == STATUS_OK, out.decode("ascii", "replace")
    assert Dump(cc).program() == RefDump().program(src)


# --- 3 단계: 검사기 (language.md §3, §4, §6, §7) ---------------------------------
#
# 방출이 아직 없으니 여기서 보는 것은 진단이다. 첫 오류의 위치와 문구가 바이트까지
# 같아야 한다 (implementation.md §9).

CHECK_SOURCES = [
    # 타입 일치
    "fn f() { let a: i32 = 1; let b: u32 = a; }",
    "fn f() { let a: i32 = -1; let b: u32 = a as u32; }",
    "fn f() { if 1 { } }",
    "fn f() { let a: i32 = true as i32; }",
    "fn f() { let x: i32 = 0; if x { } }",
    "fn f() { let x: i32 = 0; for x { } }",
    "fn f() { let a: u32 = 5; let b: i32 = 5; }",
    "fn f() { let a: u32 = 1 + 2 * 3; }",
    "fn f() { let a = 5; let b: u32 = a; }",
    "fn f() { let a: i32 = 1; let b: u32 = 2; let c = a + b; }",
    "fn f() { let a: i32 = 1; let b = a << 3; }",
    "fn f() { let a: i32 = 1; let n: i32 = 2; let b = a << n; }",
    'fn f() { let s: []u8 = "x"; let i: i32 = 0; let b = s[i]; }',
    "fn f() { let a = true < false; }",
    "fn f() { let a = true == false; }",
    "fn f() { let a: u32 = 1; let b: u32 = 2; let c = a == b; }",
    "fn f(p: *u8, q: *u8) -> bool { return p == q; }",
    "fn f(p: *u8, q: *u8) -> bool { return p < q; }",
    'fn f() { let a: []u8 = "x"; let b: []u8 = "y"; let c = a == b; }',
    # u8
    "fn f() { let b: u8 = 0; }",
    "fn f(a: u8) { }",
    "fn f() -> u8 { return 0; }",
    'fn f() { let s: []u8 = "x"; let b: u32 = s[0]; }',
    "struct S { b: u8 } fn f() { let s: S = S{ b: 1 }; let x: u32 = s.b; }",
    # 집합체
    "struct S { a: i32 } fn g(s: S) { }",
    "struct S { a: i32 } fn g() -> S { }",
    "struct S { a: i32 } fn f() { let x: S = S{ a: 1 }; let y: S = x; }",
    "struct S { a: i32, b: u32 } fn f() { let mut s: S = S{ a: 1, b: 2 }; }",
    "struct A { x: i32 } struct B { a: A }",
    "struct A { x: &i32 }",
    "struct A { x: i32 } enum E { V(A) }",
    "enum E { V(&i32) }",
    "struct S { a: i32, b: i32 } fn f() { let s: S = S{ a: 1 }; }",
    "struct S { a: i32, b: i32 } fn f() { let s: S = S{ b: 1, a: 2 }; }",
    "struct S { a: i32 } fn g(p: &S) { } fn f() { g(&S{ a: 1 }); }",
    "struct S { a: i32 } struct S2 { a: i32 } fn f() { let s: S = S2{ a: 1 }; }",
    "fn f() { let s: Zork = Zork{ a: 1 }; }",
    # 슬라이스
    "fn f() -> []u8 { }",
    "fn f(s: []u8) -> u32 { return s.len; }",
    'fn f() { let s: []u8 = "x"; let n: i32 = s.len; }',
    'fn f() { let s: []u8 = "x"; let p = s.ptr; }',
    'fn f() { let s: []mut u8 = "x"; }',
    "struct S { len: u32 } fn f(p: &S) -> u32 { return p.^.len; }",
    # 가변성
    "fn f() { let x: i32 = 1; x = 2; }",
    "fn f(s: []u8) { s[0] = 1; }",
    "fn f(s: []mut u8) { s[0] = 1; }",
    "struct S { a: i32 } fn g(p: &S) { p.^.a = 1; }",
    "struct S { a: i32 } fn g(p: &mut S) { p.^.a = 1; }",
    "struct S { a: i32 } fn f() { let s: S = S{ a: 1 }; s.a = 2; }",
    "fn f(a: []mut u8) { a = a; }",
    # 대여
    "struct S { a: i32 } fn f() { let mut s: S = S{ a: 1 }; let r = &mut s; }",
    "struct S { a: i32 } fn g() -> &S { }",
    "struct S { a: i32 } fn g(p: &S) { let q: &S = p; }",
    "struct S { a: i32 }\nfn two(x: &S, y: &S) { }\nfn f() { let s: S = S{ a: 1 }; two(&s, &s); }",
    "struct S { a: i32 }\nfn two(x: &mut S, y: &mut S) { }\nfn f() { let mut s: S = S{ a: 1 }; two(&mut s, &mut s); }",
    "struct S { a: i32 }\nfn mix(x: &S, y: &mut S) { }\nfn f() { let mut s: S = S{ a: 1 }; mix(&s, &mut s); }",
    "struct S { a: i32 }\nfn take(x: &mut S, n: i32) { }\nfn f() { let mut s: S = S{ a: 1 }; take(&mut s, s.a); }",
    "struct S { a: i32 }\nfn two(x: &mut S, y: &mut S) { }\nfn f() { let mut a: S = S{ a: 1 }; let mut b: S = S{ a: 2 }; two(&mut a, &mut b); }",
    "struct S { a: i32 }\nfn two(x: &S, y: &S) { }\nfn g(p: &S) { two(p, p); }",
    "struct S { a: i32 }\nfn take(x: &mut S, n: i32) { }\nfn f(arr: []mut S) { take(&mut arr[0], arr[1].a); }",
    "struct S { a: i32 }\nfn take(x: &mut S, n: i32) { }\nfn f() { let s: S = S{ a: 1 }; take(&mut s, 0); }",
    "struct S { a: i32 }\nfn one(x: &S) { }\nfn f() { let mut s: S = S{ a: 1 }; one(&mut s); }",
    "struct S { a: i32 }\nfn one(x: &S) { }\nfn f() { let mut s: S = S{ a: 1 }; one(&s); }",
    "struct S { a: i32 }\nfn one(x: i32) { }\nfn f() { let mut s: S = S{ a: 1 }; one(&s); }",
    "fn incr(n: &mut u32) { n.^ += 1; }\nfn f() { let mut x: u32 = 5; incr(&mut x); }",
    "struct S { a: i32 }\nfn take(x: &mut S) { }\nfn f(arr: []mut S) { take(&mut arr[arr[0].a as u32]); }",
    # match
    "enum E { A, B(i32), C(i32, u32) } fn f() { let e: E = E.A; match e { A => { } } }",
    "enum E { A, B(i32), C(i32, u32) } fn f() { let e: E = E.A; match e { A => { } _ => { } } }",
    "enum E { A, B(i32), C(i32, u32) } fn f() { let e: E = E.A; match e { _ => { } A => { } } }",
    "enum E { A, B(i32), C(i32, u32) } fn f() { let e: E = E.A; match e { A => { } A => { } _ => { } } }",
    "enum E { A, B(i32), C(i32, u32) } fn f() { let e: E = E.A; match e { Z => { } _ => { } } }",
    "enum E { A, B(i32), C(i32, u32) } fn f() { let e: E = E.A; match e { A => { } B => { } _ => { } } }",
    "fn f() { let x: i32 = 1; match x { } }",
    "enum E { A, B(i32) } fn f() { let e: E = E.B(1); match e { A => { } B(x) => { x = 2; } } }",
    "enum E { A, B(i32), C(i32, u32) } fn f() { let e: E = E.B; }",
    "enum E { A } fn g(p: &E) { } fn f() { g(&E.A); }",
    "enum E { A, B(i32) } fn f() { let e: E = E.B(true); }",
    "enum E { A, B(i32) } fn f() -> i32 { let e: E = E.A; match e { A => { return 0; } B(x) => { return x; } } }",
    "enum E { A, B } fn f() { let mut e: E = E.A; e = E.B; }",
    "enum E { } fn f() { }",
    # unsafe
    "fn f() { let p: *u32 = 0 as *u32; let x = p.^; }",
    "fn f() { let p: *u32 = 0 as *u32; unsafe { let x = p.^; } }",
    "struct S { a: i32 } fn g(p: &S) -> i32 { return p.^.a; }",
    "struct S { a: i32 } fn g(s: S) { } fn f() { unsafe { } }",
    "fn f() { let x: i32 = 1; let y = x.^; }",
    # 흐름
    "fn f() -> i32 { }",
    "fn f(c: bool) -> i32 { if c { return 1; } else { return 2; } }",
    "fn f(c: bool) -> i32 { if c { return 1; } }",
    "fn f() -> i32 { for { } }",
    "fn f() -> i32 { for { break; } }",
    "fn f() -> i32 { return true; }",
    "fn f() { return 1; }",
    "fn f() -> i32 { return; }",
    "fn f() { break; }",
    "fn f() { continue; }",
    "fn f() { for { if true { break; } } }",
    "fn f() -> i32 { unsafe { return 1; } }",
    "enum E { A, B } fn f() -> i32 { let e: E = E.A; match e { A => { return 0; } B => { return 1; } } }",
    # 이름
    "fn f() { } fn f() { }",
    "struct x { a: i32 } fn x() { }",
    "fn f() { let x = y; }",
    "fn f() { g(); }",
    "fn f(a: Foo) { }",
    "fn g() { } fn f() { let x = g; }",
    "fn f() { let x: i32 = 1; let x: i32 = 2; }",
    "fn f() { let x: i32 = 1; if true { let x: i32 = 2; } }",
    "struct S { a: i32 } fn f() { let S: i32 = 1; }",
    "fn g(a: i32) { } fn f() { g(); }",
    "fn f() -> i32 { return g(); } fn g() -> i32 { return C; } const C: i32 = 7;",
    "fn even(n: u32) -> bool { if n == 0 { return true; } return odd(n - 1); }"
    "fn odd(n: u32) -> bool { if n == 0 { return false; } return even(n - 1); }",
    "fn f(a: i32, a: i32) { }",
    "struct S { a: i32, a: i32 }",
    "enum E { A, A }",
    "struct i32 { a: i32 }",
    "fn f() { let x: i32 = 1; x(); }",
    # const
    'const C: []u8 = "x";',
    "const A: i32 = B; const B: i32 = A;",
    "const A: i32 = A;",
    "const A: i32 = 1 / 0;",
    "const C: i32 = 1; fn f() { C = 2; }",
    "fn g() -> i32 { return 1; } const C: i32 = g();",
    "const A: i32 = 1 + 2; const B: u32 = A as u32 * 3; fn f() -> u32 { return B; }",
    "const C: u32 = 0x80000000 >> 4; fn f() -> u32 { return C; }",
    "const C: i32 = -16 >> 2; fn f() -> i32 { return C; }",
    "const C: u32 = 1 << 33; fn f() -> u32 { return C; }",
    "const C: bool = true && !false; fn f() -> bool { return C; }",
    "const C: bool = 1 < 2; fn f() -> bool { return C; }",
    "const C: i32 = -2147483648 / -1; fn f() -> i32 { return C; }",
    "const C: i32 = -2147483648 % -1; fn f() -> i32 { return C; }",
    "const C: i32 = -7 / 2; fn f() -> i32 { return C; }",
    "const C: i32 = -7 % 2; fn f() -> i32 { return C; }",
    "const C: bool = true + false;",
    "const C: i32 = true;",
    "const C: bool = 1;",
    'const C: i32 = "x";',
    "const C: i32 = 1 as bool;",
    "const C: i32 = -true;",
    "const C: bool = !1;",
]


@needs_cool0c
@pytest.mark.parametrize("src", CHECK_SOURCES, ids=range(len(CHECK_SOURCES)))
def test_checker_matches_the_oracle(cc, src):
    b = src.encode("ascii")
    got = cc.compile(b)
    want = reference_compile(b)
    if want[0] != STATUS_OK:
        assert got[1] == want[1], f"{got[1]!r} != {want[1]!r}"
    assert got[0] == want[0], f"status {got[0]} != {want[0]}; got {got[1]!r}"


@needs_cool0c
def test_checker_accepts_the_corpus(cc):
    from corpus import DIAGNOSTICS, PROGRAMS

    for name, src in PROGRAMS:
        b = src.encode("ascii")
        got, want = cc.compile(b), reference_compile(b)
        assert (got[0], got[1] if got[0] else b"") == (want[0], want[1] if want[0] else b""), name
    for name, src in DIAGNOSTICS:
        got, want = cc.compile(src), reference_compile(src)
        assert got == want, f"{name}: {got!r} != {want!r}"


@needs_cool0c
def test_checker_accepts_its_own_source(cc):
    src = COOL0C.read_bytes()
    status, out = cc.compile(src)
    assert status == STATUS_OK, out.decode("ascii", "replace")


@needs_cool0c
def test_checker_agrees_on_generated_programs(cc):
    import random

    from test_fuzz import ProgramGen

    gen = ProgramGen(random.Random(0xC0FFEE))
    for _ in range(120):
        b = gen.program().encode("ascii")
        got, want = cc.compile(b), reference_compile(b)
        assert got[0] == want[0], f"{got[1]!r} vs {want[1]!r}\n{b.decode()}"
        if want[0] != STATUS_OK:
            assert got[1] == want[1]


# --- 4 단계: 방출 (implementation.md §8, §5) ------------------------------------------
#
# 여기서부터는 바이트 비교다.


def both(cc, src: bytes):
    return cc.compile(src), reference_compile(src)


@needs_cool0c
@pytest.mark.parametrize("src", CHECK_SOURCES, ids=range(len(CHECK_SOURCES)))
def test_emitted_bytes_match_the_oracle(cc, src):
    got, want = both(cc, src.encode("ascii"))
    assert got == want, f"{got!r}\n!=\n{want!r}"


@needs_cool0c
def test_emitted_bytes_match_on_the_corpus(cc):
    from corpus import DIAGNOSTICS, PROGRAMS

    for name, src in PROGRAMS:
        got, want = both(cc, src.encode("ascii"))
        assert got == want, name
    for name, src in DIAGNOSTICS:
        got, want = both(cc, src)
        assert got == want, name


@needs_cool0c
def test_emitted_bytes_match_on_the_run_tests(cc):
    """test_run.py 가 쓰는 프로그램들. 의미가 있는 것들이다."""
    import test_run

    seen = 0
    for name in dir(test_run):
        obj = getattr(test_run, name)
        if not isinstance(obj, str) or "fn " not in obj:
            continue
        try:
            b = obj.encode("ascii")
        except UnicodeEncodeError:
            continue
        got, want = both(cc, b)
        assert got == want, name
        seen += 1
    assert seen > 0


@needs_cool0c
def test_emitted_bytes_match_on_generated_programs(cc):
    import random

    from test_fuzz import ProgramGen

    gen = ProgramGen(random.Random(0x11FE))
    for _ in range(150):
        b = gen.program().encode("ascii")
        got, want = both(cc, b)
        assert got == want, b.decode()


@needs_cool0c
def test_emitted_bytes_match_on_random_expressions(cc):
    import random

    from test_fuzz import ExprGen

    rng = random.Random(0x5A1D)
    gen = ExprGen(rng)
    for _ in range(150):
        text, _ = gen.make(4, {"a": rng.randrange(0, 1 << 31)})
        b = f"fn f(a: i32) -> i32 {{ return {text}; }}".encode("ascii")
        got, want = both(cc, b)
        assert got == want, text


@needs_cool0c
def test_emitted_bytes_match_on_random_bytes(cc):
    """1층 퍼저를 두 구현에 동시에 먹인다. 진단까지 같아야 한다."""
    import random

    rng = random.Random(0xB17E)
    for _ in range(400):
        n = rng.randrange(0, 64)
        b = bytes(rng.randrange(0, 256) for _ in range(n))
        got, want = both(cc, b)
        assert got == want, repr(b)


@needs_cool0c
def test_emitted_bytes_match_on_token_soup(cc):
    import random

    rng = random.Random(0x7011)
    toks = """fn struct enum const let mut if else for break continue return match
              unsafe as true false i32 u32 bool u8 x y z 0 1 0xFF ( ) { } [ ] , ; :
              . = == != < > <= >= + - * / % & | ^ ! && || << >> -> =>""".split()
    for _ in range(400):
        n = rng.randrange(0, 40)
        b = " ".join(rng.choice(toks) for _ in range(n)).encode("ascii")
        got, want = both(cc, b)
        assert got == want, b.decode()


@needs_cool0c
def test_emitted_bytes_match_on_truncations_of_its_own_source(cc):
    src = COOL0C.read_bytes()
    for i in range(0, 4000, 37):
        got, want = both(cc, src[:i])
        assert got == want, i


@needs_cool0c
def test_self_reproduction(cc):
    """B = py(cool0c), B(cool0c) == B. 마일스톤의 관문 2 (WAT 없이)."""
    src = COOL0C.read_bytes()
    status, b = reference_compile(src)
    assert status == STATUS_OK
    from conftest import run_compiler

    assert run_compiler(b, src) == (STATUS_OK, b)
