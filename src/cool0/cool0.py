"""cool0 reference implementation.

spec/ 의 참조 구현이다. 신뢰 사슬에는 없다 -- 오라클이다.

핵심 진입점은 순수 함수 하나다:

    compile(src: bytes) -> tuple[int, bytes]

        (0, wasm_bytes)   성공
        (1, diag_ascii)   실패

파일도, 경로도, 인자도 없다. 파일 맨 아래의 __main__ 은 호스트이며
컴파일러의 일부가 아니다.

구성:

    1. 어휘 분석      Lexer
    2. 구문 트리      AST
    3. 타입           Ty
    4. 구문 분석      Parser
    5. 의미 분석      Checker
    6. wasm 인코딩    binary helpers
    7. 코드 생성      Emitter
    8. 진입점         compile()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ============================================================================
# 0. 상수 -- 호스트 ABI (implementation.md §7)
# ============================================================================

MEM_PAGES = 512  # 32 MiB, 고정
OUT_PTR_ADDR = 0x0000
OUT_LEN_ADDR = 0x0004
SRC_ADDR = 0x1000  # 호스트가 소스를 놓는 곳
RODATA_ADDR = 0x0100_0000  # 문자열 리터럴. 위로 자란다
SHADOW_FLOOR = 0x0180_0000  # 섀도 스택 영역 하한. 넘으면 트랩 (프롤로그가 검사한다)
SHADOW_TOP = 0x0200_0000  # $sp 초기값. 아래로 자란다 (8 MiB)

STATUS_OK = 0
STATUS_ERR = 1


class CompileError(Exception):
    """진단 하나. 첫 오류에서 컴파일이 멈춘다."""

    def __init__(self, line: int, col: int, msg: str):
        super().__init__(f"{line}:{col}: {msg}")
        self.line = line
        self.col = col
        self.msg = msg

    def render(self) -> bytes:
        return f"{self.line}:{self.col}: {self.msg}\n".encode("ascii")


def _err(pos, msg: str) -> CompileError:
    return CompileError(pos[0], pos[1], msg)


# ============================================================================
# 1. 어휘 분석 (language.md §2)
# ============================================================================

KEYWORDS = frozenset(
    """fn struct enum const let mut if else for break continue
       return match unsafe as true false slice slice_mut offset""".split()
)

# 긴 것부터. 최장 일치.
PUNCT = [
    "<<=", ">>=",
    "->", "=>", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "(", ")", "{", "}", "[", "]", ",", ";", ":", ".", "=",
    "<", ">", "+", "-", "*", "/", "%", "&", "|", "^", "!",
]

ESCAPES = {"n": 0x0A, "t": 0x09, "r": 0x0D, "0": 0x00, "\\": 0x5C, "'": 0x27}
STR_ESCAPES = dict(ESCAPES, **{'"': 0x22})


# 파이썬의 str.isalpha() 는 ASCII 를 넘어선다 -- 'ü'.isalnum() 은 참이다.
# 소스는 ASCII 전용이므로 (language.md §2) 바이트로 직접 판정한다.
def is_alpha(b: int) -> bool:
    return 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A


def is_digit(b: int) -> bool:
    return 0x30 <= b <= 0x39


def is_ident_start(b: int) -> bool:
    return is_alpha(b) or b == 0x5F


def is_ident_cont(b: int) -> bool:
    return is_alpha(b) or is_digit(b) or b == 0x5F


@dataclass
class Token:
    kind: str  # ident | int | char | str | kw | punct | eof
    text: str  # 식별자/키워드/구두점의 철자
    value: object  # int/char 는 값, str 은 bytes
    line: int
    col: int

    @property
    def pos(self):
        return (self.line, self.col)

    def describe(self) -> str:
        if self.kind == "eof":
            return "end of file"
        if self.kind == "int":
            return "integer literal"
        if self.kind == "char":
            return "character literal"
        if self.kind == "str":
            return "string literal"
        return "`" + self.text + "`"


def lex(src: bytes) -> list[Token]:
    """소스 바이트를 토큰 목록으로. 마지막은 항상 eof."""
    toks: list[Token] = []
    i = 0
    n = len(src)
    line = 1
    col = 1

    def adv(k: int = 1):
        nonlocal i, line, col
        for _ in range(k):
            if src[i] == 0x0A:
                line += 1
                col = 1
            else:
                col += 1
            i += 1

    while i < n:
        b = src[i]

        # ASCII 전용
        if not (b in (0x09, 0x0A, 0x0D) or 0x20 <= b <= 0x7E):
            raise CompileError(line, col, "non-ascii byte in source")

        if b in (0x09, 0x0A, 0x0D, 0x20):
            adv()
            continue

        # 주석
        if b == 0x2F and i + 1 < n and src[i + 1] == 0x2F:  # //
            while i < n and src[i] != 0x0A:
                if not (src[i] in (0x09, 0x0D) or 0x20 <= src[i] <= 0x7E):
                    raise CompileError(line, col, "non-ascii byte in source")
                adv()
            continue

        sl, sc = line, col

        # 식별자 / 키워드
        if is_ident_start(b):
            j = i
            while j < n and is_ident_cont(src[j]):
                j += 1
            word = src[i:j].decode("ascii")
            adv(j - i)
            kind = "kw" if word in KEYWORDS else "ident"
            toks.append(Token(kind, word, word, sl, sc))
            continue

        # 정수 리터럴
        if is_digit(b):
            base, digits, j = 10, "0123456789", i
            if b == 0x30 and i + 1 < n and src[i + 1] in (0x78, 0x58, 0x62, 0x42):
                if src[i + 1] in (0x78, 0x58):
                    base, digits = 16, "0123456789abcdefABCDEF"
                else:
                    base, digits = 2, "01"
                j = i + 2
            # `_` separates digits, so it cannot come first -- grammar.ebnf spells
            # the prefix forms as `"0x", hex_digit, { hex_digit | "_" }`
            ndigits = 0
            value = 0
            over = False
            while j < n and (chr(src[j]) in digits or (src[j] == 0x5F and ndigits)):
                if src[j] != 0x5F:
                    d = int(chr(src[j]), base)
                    # detect overflow past 32 bits while scanning; never build a
                    # bignum from the source, or a long literal escapes compile()
                    if value > (0xFFFF_FFFF - d) // base:
                        over = True
                    value = value * base + d
                    ndigits += 1
                j += 1
            if j < n and is_ident_cont(src[j]):
                raise CompileError(sl, sc, "invalid digit in integer literal")
            if not ndigits:
                raise CompileError(sl, sc, "integer literal has no digits")
            if over:
                raise CompileError(sl, sc, "integer literal out of range")
            text = src[i:j].decode("ascii")
            adv(j - i)
            toks.append(Token("int", text, value, sl, sc))
            continue

        # 문자 리터럴
        if b == 0x27:  # '
            adv()
            if i >= n:
                raise CompileError(sl, sc, "unterminated character literal")
            if src[i] == 0x27:
                raise CompileError(sl, sc, "empty character literal")
            if src[i] == 0x5C:  # backslash
                adv()
                if i >= n:
                    raise CompileError(sl, sc, "unterminated character literal")
                e = chr(src[i])
                if e not in ESCAPES:
                    raise CompileError(line, col, "unknown escape sequence")
                value = ESCAPES[e]
                adv()
            else:
                value = src[i]
                if not (0x20 <= value <= 0x7E):
                    raise CompileError(line, col, "invalid character in character literal")
                adv()
            if i >= n or src[i] != 0x27:
                raise CompileError(sl, sc, "unterminated character literal")
            adv()
            toks.append(Token("char", "char", value, sl, sc))
            continue

        # 문자열 리터럴
        if b == 0x22:  # "
            adv()
            out = bytearray()
            while True:
                if i >= n or src[i] == 0x0A:
                    raise CompileError(sl, sc, "unterminated string literal")
                if src[i] == 0x22:
                    adv()
                    break
                if src[i] == 0x5C:
                    adv()
                    if i >= n:
                        raise CompileError(sl, sc, "unterminated string literal")
                    e = chr(src[i])
                    if e not in STR_ESCAPES:
                        raise CompileError(line, col, "unknown escape sequence")
                    out.append(STR_ESCAPES[e])
                    adv()
                else:
                    if not (0x20 <= src[i] <= 0x7E):
                        raise CompileError(line, col, "invalid character in string literal")
                    out.append(src[i])
                    adv()
            toks.append(Token("str", "str", bytes(out), sl, sc))
            continue

        # 구두점
        for p in PUNCT:
            if src[i : i + len(p)] == p.encode("ascii"):
                adv(len(p))
                toks.append(Token("punct", p, p, sl, sc))
                break
        else:
            raise CompileError(sl, sc, "unexpected character")

    toks.append(Token("eof", "", None, line, col))
    return toks


# ============================================================================
# 2. 타입 (language.md §3)
# ============================================================================


class Ty:
    pass


@dataclass(frozen=True)
class Prim(Ty):
    name: str


@dataclass(frozen=True)
class Slice(Ty):
    elem: Ty
    mut: bool


@dataclass(frozen=True)
class Ref(Ty):
    inner: Ty
    mut: bool


@dataclass(frozen=True)
class Ptr(Ty):
    inner: Ty


@dataclass(frozen=True)
class Named(Ty):
    name: str


I32 = Prim("i32")
U32 = Prim("u32")
BOOL = Prim("bool")
U8 = Prim("u8")
VOID = Prim("void")
INTLIT = Prim("{int}")  # 아직 굳지 않은 정수 리터럴

INT_TYPES = (I32, U32)


def ty_str(t: Ty) -> str:
    if isinstance(t, Prim):
        return "integer literal" if t is INTLIT else t.name
    if isinstance(t, Slice):
        return "[]mut " + ty_str(t.elem) if t.mut else "[]" + ty_str(t.elem)
    if isinstance(t, Ref):
        return "&mut " + ty_str(t.inner) if t.mut else "&" + ty_str(t.inner)
    if isinstance(t, Ptr):
        return "*" + ty_str(t.inner)
    if isinstance(t, Named):
        return t.name
    return "?"


def is_int(t: Ty) -> bool:
    return t in (I32, U32, INTLIT)


def is_aggregate(t: Ty) -> bool:
    return isinstance(t, Named)


def slot_count(t: Ty) -> int:
    """wasm 값 슬롯 수. 슬라이스는 (ptr, len) 둘, 나머지 스칼라는 하나."""
    if isinstance(t, Slice):
        return 2
    return 1


def read_ty(t: Ty) -> Ty:
    """장소의 타입 -> 읽었을 때의 값 타입. u8 은 u32 로 읽힌다 (language.md §3)."""
    return U32 if t is U8 else t


def align_up(x: int, a: int) -> int:
    return (x + a - 1) // a * a


# ============================================================================
# 3. 구문 트리
# ============================================================================


# 중첩 한계 (language.md §6). 재귀 하강 컴파일러가 고정 크기 섀도 스택 위에서 돌아야 하므로
# 식과 블록의 깊이에 한계가 있다. 잎은 깊이 1 이다.
MAX_DEPTH = 64


class Node:
    pos: tuple
    depth: int = 1


@dataclass
class TyNode(Node):
    pos: tuple
    kind: str  # prim | named | slice | ref | ptr
    name: str = ""
    inner: Optional["TyNode"] = None
    mut: bool = False


# --- 식 -------------------------------------------------------------------


@dataclass
class Int(Node):
    pos: tuple
    value: int


@dataclass
class Char(Node):
    pos: tuple
    value: int


@dataclass
class Str(Node):
    pos: tuple
    value: bytes


@dataclass
class Bool(Node):
    pos: tuple
    value: bool


@dataclass
class Ident(Node):
    pos: tuple
    name: str


@dataclass
class Unary(Node):
    pos: tuple
    op: str
    operand: Node


@dataclass
class Binary(Node):
    pos: tuple
    op: str
    lhs: Node
    rhs: Node


@dataclass
class Borrow(Node):
    pos: tuple
    mut: bool
    operand: Node


@dataclass
class Cast(Node):
    pos: tuple
    operand: Node
    ty_node: TyNode


@dataclass
class Call(Node):
    pos: tuple
    callee: Node
    args: list


@dataclass
class Index(Node):
    pos: tuple
    base: Node
    index: Node


@dataclass
class Field(Node):
    pos: tuple
    base: Node
    name: str


@dataclass
class Deref(Node):
    pos: tuple
    base: Node


@dataclass
class StructLit(Node):
    pos: tuple
    name: str
    fields: list  # [(pos, name, expr)]


@dataclass
class SliceExpr(Node):
    """`slice(p, len)` / `slice_mut(p, len)` 내장 식."""

    pos: tuple
    ptr: Node
    length: Node
    mut: bool


@dataclass
class OffsetExpr(Node):
    """`offset(p, i)` 내장 식. 원소 단위로 움직인다."""

    pos: tuple
    ptr: Node
    index: Node


# --- 문 -------------------------------------------------------------------


@dataclass
class Let(Node):
    pos: tuple
    name: str
    mut: bool
    ty_node: Optional[TyNode]
    init: Node


@dataclass
class Assign(Node):
    pos: tuple
    target: Node
    op: str  # "=" 또는 "+=" 따위
    value: Node


@dataclass
class ExprStmt(Node):
    pos: tuple
    expr: Node


@dataclass
class If(Node):
    pos: tuple
    cond: Node
    then: list
    els: Optional[list]


@dataclass
class For(Node):
    pos: tuple
    init: Optional[Let]
    cond: Optional[Node]
    post: Optional[Node]
    body: list


@dataclass
class Break(Node):
    pos: tuple


@dataclass
class Continue(Node):
    pos: tuple


@dataclass
class Return(Node):
    pos: tuple
    value: Optional[Node]


@dataclass
class Arm(Node):
    pos: tuple
    variant: Optional[str]  # None 이면 `_`
    binds: list  # [(pos, name)]
    body: list


@dataclass
class Match(Node):
    pos: tuple
    scrutinee: Node
    arms: list


@dataclass
class Unsafe(Node):
    pos: tuple
    body: list


# --- 최상위 선언 -----------------------------------------------------------


@dataclass
class FnDecl(Node):
    pos: tuple
    name: str
    params: list  # [(pos, name, TyNode)]
    ret: Optional[TyNode]
    body: list


@dataclass
class StructDecl(Node):
    pos: tuple
    name: str
    fields: list  # [(pos, name, TyNode)]


@dataclass
class EnumDecl(Node):
    pos: tuple
    name: str
    variants: list  # [(pos, name, [TyNode])]


@dataclass
class ConstDecl(Node):
    pos: tuple
    name: str
    ty_node: TyNode
    value: Node


# ============================================================================
# 4. 구문 분석 (language.md §4, §5, §6)
# ============================================================================

# 낮은 것부터 (language.md §5 를 뒤집은 순서). 비교는 비결합이라 자리만 잡아 두고 따로 다룬다.
CMP_LEVEL = 2
BIN_LEVELS = [
    ["||"],
    ["&&"],
    [],  # CMP_LEVEL -- parse_cmp 가 맡는다
    ["|"],
    ["^"],
    ["&"],
    ["<<", ">>"],
    ["+", "-"],
    ["*", "/", "%"],
]
CMP_OPS = ["==", "!=", "<", ">", "<=", ">="]
ASSIGN_OPS = ["=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="]


class Parser:
    def __init__(self, toks: list[Token]):
        self.toks = toks
        self.i = 0
        self.block_depth = 0
        self.expr_depth = 0

    def deep(self, node: Node, *kids: Node) -> Node:
        """합성 식의 깊이를 매기고 한계를 지킨다 (language.md §6).

        여기서 막아 두면 검사기와 방출기의 재귀도 같이 묶인다 -- 왼쪽으로 깊은
        `1+1+1+...` 도 파서에서는 반복이지만 트리로는 깊다.
        """
        d = 1 + max((k.depth for k in kids), default=0)
        if d > MAX_DEPTH:
            raise _err(node.pos, "expression nests too deeply")
        node.depth = d
        return node

    # --- 토큰 조작 ---------------------------------------------------------

    @property
    def tok(self) -> Token:
        return self.toks[self.i]

    def at(self, text: str) -> bool:
        t = self.tok
        return t.kind in ("punct", "kw") and t.text == text

    def eat(self, text: str) -> bool:
        if self.at(text):
            self.i += 1
            return True
        return False

    def expect(self, text: str) -> Token:
        if not self.at(text):
            raise _err(self.tok.pos, f"expected `{text}`, found {self.tok.describe()}")
        t = self.tok
        self.i += 1
        return t

    def expect_ident(self) -> Token:
        if self.tok.kind != "ident":
            raise _err(self.tok.pos, f"expected identifier, found {self.tok.describe()}")
        t = self.tok
        self.i += 1
        return t

    # --- 최상위 -----------------------------------------------------------

    def parse_program(self) -> list:
        decls = []
        while self.tok.kind != "eof":
            decls.append(self.parse_decl())
        return decls

    def parse_decl(self):
        if self.at("fn"):
            return self.parse_fn()
        if self.at("struct"):
            return self.parse_struct()
        if self.at("enum"):
            return self.parse_enum()
        if self.at("const"):
            return self.parse_const()
        raise _err(
            self.tok.pos,
            f"expected `fn`, `struct`, `enum` or `const`, found {self.tok.describe()}",
        )

    def parse_fn(self) -> FnDecl:
        pos = self.tok.pos
        self.expect("fn")
        name = self.expect_ident()
        self.expect("(")
        params = []
        while not self.at(")"):
            p = self.expect_ident()
            self.expect(":")
            params.append((p.pos, p.text, self.parse_ty()))
            if not self.eat(","):
                break
        self.expect(")")
        ret = self.parse_ty() if self.eat("->") else None
        body = self.parse_block()
        return FnDecl(pos, name.text, params, ret, body)

    def parse_struct(self) -> StructDecl:
        pos = self.tok.pos
        self.expect("struct")
        name = self.expect_ident()
        self.expect("{")
        fields = []
        while not self.at("}"):
            f = self.expect_ident()
            self.expect(":")
            fields.append((f.pos, f.text, self.parse_ty()))
            if not self.eat(","):
                break
        self.expect("}")
        return StructDecl(pos, name.text, fields)

    def parse_enum(self) -> EnumDecl:
        pos = self.tok.pos
        self.expect("enum")
        name = self.expect_ident()
        self.expect("{")
        variants = []
        while not self.at("}"):
            v = self.expect_ident()
            payload = []
            if self.eat("("):
                while not self.at(")"):
                    payload.append(self.parse_ty())
                    if not self.eat(","):
                        break
                self.expect(")")
            variants.append((v.pos, v.text, payload))
            if not self.eat(","):
                break
        self.expect("}")
        return EnumDecl(pos, name.text, variants)

    def parse_const(self) -> ConstDecl:
        pos = self.tok.pos
        self.expect("const")
        name = self.expect_ident()
        self.expect(":")
        ty_node = self.parse_ty()
        self.expect("=")
        value = self.parse_expr()
        self.expect(";")
        return ConstDecl(pos, name.text, ty_node, value)

    # --- 타입 -------------------------------------------------------------

    def parse_ty(self) -> TyNode:
        if self.expr_depth >= MAX_DEPTH:
            raise _err(self.tok.pos, "type nests too deeply")
        self.expr_depth += 1
        t = self.parse_ty_inner()
        self.expr_depth -= 1
        return t

    def parse_ty_inner(self) -> TyNode:
        pos = self.tok.pos
        if self.eat("["):
            self.expect("]")
            mut = self.eat("mut")
            return TyNode(pos, "slice", inner=self.parse_ty(), mut=mut)
        if self.eat("&"):
            mut = self.eat("mut")
            return TyNode(pos, "ref", inner=self.parse_ty(), mut=mut)
        if self.eat("*"):
            return TyNode(pos, "ptr", inner=self.parse_ty())
        if self.tok.kind == "ident":
            name = self.tok.text
            self.i += 1
            if name in ("i32", "u32", "bool", "u8"):
                return TyNode(pos, "prim", name=name)
            return TyNode(pos, "named", name=name)
        raise _err(self.tok.pos, f"expected type, found {self.tok.describe()}")

    # --- 문 ---------------------------------------------------------------

    def parse_block(self) -> list:
        if self.block_depth >= MAX_DEPTH:
            raise _err(self.tok.pos, "block nests too deeply")
        self.block_depth += 1
        self.expect("{")
        stmts = []
        while not self.at("}"):
            if self.tok.kind == "eof":
                raise _err(self.tok.pos, "expected `}`, found end of file")
            stmts.append(self.parse_stmt())
        self.expect("}")
        self.block_depth -= 1
        return stmts

    def parse_stmt(self):
        pos = self.tok.pos
        if self.at("let"):
            let = self.parse_let()
            self.expect(";")
            return let
        if self.at("if"):
            return self.parse_if()
        if self.at("for"):
            return self.parse_for()
        if self.at("match"):
            return self.parse_match()
        if self.at("unsafe"):
            self.i += 1
            return Unsafe(pos, self.parse_block())
        if self.eat("break"):
            self.expect(";")
            return Break(pos)
        if self.eat("continue"):
            self.expect(";")
            return Continue(pos)
        if self.eat("return"):
            if self.eat(";"):
                return Return(pos, None)
            value = self.parse_expr()
            self.expect(";")
            return Return(pos, value)
        st = self.parse_simple_stmt()
        self.expect(";")
        return st

    def parse_simple_stmt(self):
        """대입 또는 호출문. `;` 는 부르는 쪽이 먹는다 (for 후처리 때문)."""
        pos = self.tok.pos
        e = self.parse_expr()
        if self.tok.kind == "punct" and self.tok.text in ASSIGN_OPS:
            op = self.tok.text
            self.i += 1
            value = self.parse_expr(allow_struct_lit=True)
            return Assign(pos, e, op, value)
        if not isinstance(e, Call):
            raise _err(pos, "expression statement must be a call")
        return ExprStmt(pos, e)

    def parse_let(self) -> Let:
        pos = self.tok.pos
        self.expect("let")
        mut = self.eat("mut")
        name = self.expect_ident()
        ty_node = self.parse_ty() if self.eat(":") else None
        self.expect("=")
        init = self.parse_expr(allow_struct_lit=True)
        return Let(pos, name.text, mut, ty_node, init)

    def parse_if(self) -> If:
        pos = self.tok.pos
        self.expect("if")
        cond = self.parse_expr()
        then = self.parse_block()
        els = None
        if self.eat("else"):
            if self.at("if"):
                # else-if 사슬도 재귀다. 블록 깊이에 함께 센다
                if self.block_depth >= MAX_DEPTH:
                    raise _err(self.tok.pos, "block nests too deeply")
                self.block_depth += 1
                els = [self.parse_if()]
                self.block_depth -= 1
            else:
                els = self.parse_block()
        return If(pos, cond, then, els)

    def parse_for(self) -> For:
        pos = self.tok.pos
        self.expect("for")
        if self.at("{"):  # 무한
            return For(pos, None, None, None, self.parse_block())
        if self.at("let"):  # 초기화·조건·후처리
            init = self.parse_let()
            self.expect(";")
            cond = self.parse_expr()
            self.expect(";")
            post = self.parse_simple_stmt()
            return For(pos, init, cond, post, self.parse_block())
        cond = self.parse_expr()  # 조건
        return For(pos, None, cond, None, self.parse_block())

    def parse_match(self) -> Match:
        pos = self.tok.pos
        self.expect("match")
        scrutinee = self.parse_expr()
        self.expect("{")
        arms = []
        while not self.at("}"):
            apos = self.tok.pos
            if self.tok.kind == "ident" and self.tok.text == "_":
                self.i += 1
                variant, binds = None, []
            else:
                variant = self.expect_ident().text
                binds = []
                if self.eat("("):
                    while not self.at(")"):
                        b = self.expect_ident()
                        binds.append((b.pos, b.text))
                        if not self.eat(","):
                            break
                    self.expect(")")
            self.expect("=>")
            arms.append(Arm(apos, variant, binds, self.parse_block()))
        self.expect("}")
        return Match(pos, scrutinee, arms)

    # --- 식 ---------------------------------------------------------------

    def parse_expr(self, allow_struct_lit: bool = False) -> Node:
        # 문법 중첩은 parse_prefix 가 센다 -- 모든 식이 반드시 거치는 곳이라
        # 거기 하나면 괄호·단항·인자·첨자가 전부 정확히 한 번씩 세어진다
        return self.parse_bin(0, allow_struct_lit)

    def parse_bin(self, level: int, asl: bool) -> Node:
        if level == CMP_LEVEL:
            return self.parse_cmp(asl)
        if level >= len(BIN_LEVELS):
            return self.parse_unary(asl)
        ops = BIN_LEVELS[level]
        lhs = self.parse_bin(level + 1, asl)
        while self.tok.kind == "punct" and self.tok.text in ops:
            pos = self.tok.pos
            op = self.tok.text
            self.i += 1
            rhs = self.parse_bin(level + 1, asl)
            lhs = self.deep(Binary(pos, op, lhs, rhs), lhs, rhs)
        return lhs

    def parse_cmp(self, asl: bool) -> Node:
        lhs = self.parse_bin(CMP_LEVEL + 1, asl)
        if self.tok.kind == "punct" and self.tok.text in CMP_OPS:
            pos = self.tok.pos
            op = self.tok.text
            self.i += 1
            rhs = self.parse_bin(CMP_LEVEL + 1, asl)
            if self.tok.kind == "punct" and self.tok.text in CMP_OPS:
                raise _err(self.tok.pos, "comparison operators cannot be chained")
            return self.deep(Binary(pos, op, lhs, rhs), lhs, rhs)
        return lhs

    def parse_unary(self, asl: bool) -> Node:
        """`as` 단계. 단항이 `as` 보다 세게 묶는다 (language.md §5) -- `-x as u32` 는 `(-x) as u32`."""
        e = self.parse_prefix(asl)
        while self.at("as"):
            pos = self.tok.pos
            self.i += 1
            e = self.deep(Cast(pos, e, self.parse_ty()), e)
        return e

    def parse_prefix(self, asl: bool) -> Node:
        # `- - - ...` 는 트리를 만들기 전에 파서가 먼저 깊어진다
        if self.expr_depth >= MAX_DEPTH:
            raise _err(self.tok.pos, "expression nests too deeply")
        self.expr_depth += 1
        e = self.parse_prefix_inner(asl)
        self.expr_depth -= 1
        return e

    def parse_prefix_inner(self, asl: bool) -> Node:
        pos = self.tok.pos
        if self.at("-"):
            self.i += 1
            k = self.parse_prefix(asl)
            return self.deep(Unary(pos, "-", k), k)
        if self.at("!"):
            self.i += 1
            k = self.parse_prefix(asl)
            return self.deep(Unary(pos, "!", k), k)
        if self.at("&"):
            self.i += 1
            mut = self.eat("mut")
            k = self.parse_prefix(asl)
            return self.deep(Borrow(pos, mut, k), k)
        return self.parse_postfix(asl)

    def parse_postfix(self, asl: bool) -> Node:
        e = self.parse_primary(asl)
        while True:
            pos = self.tok.pos
            if self.at("("):
                # 괄호 안에서는 제한이 풀린다 -- 블록이 뒤따를 수 없으니 모호하지 않다
                self.i += 1
                args = []
                while not self.at(")"):
                    args.append(self.parse_expr(allow_struct_lit=True))
                    if not self.eat(","):
                        break
                self.expect(")")
                e = self.deep(Call(pos, e, args), e, *args)
            elif self.at("["):
                self.i += 1
                idx = self.parse_expr(allow_struct_lit=True)
                self.expect("]")
                e = self.deep(Index(pos, e, idx), e, idx)
            elif self.at("."):
                self.i += 1
                if self.eat("^"):
                    e = self.deep(Deref(pos, e), e)
                else:
                    e = self.deep(Field(pos, e, self.expect_ident().text), e)
            else:
                return e

    def parse_primary(self, asl: bool) -> Node:
        t = self.tok
        pos = t.pos
        if t.kind == "int":
            self.i += 1
            return Int(pos, t.value)
        if t.kind == "char":
            self.i += 1
            return Char(pos, t.value)
        if t.kind == "str":
            self.i += 1
            return Str(pos, t.value)
        if self.at("true"):
            self.i += 1
            return Bool(pos, True)
        if self.at("false"):
            self.i += 1
            return Bool(pos, False)
        if self.at("slice") or self.at("slice_mut"):
            mut = self.at("slice_mut")
            self.i += 1
            self.expect("(")
            ptr = self.parse_expr(allow_struct_lit=True)
            self.expect(",")
            length = self.parse_expr(allow_struct_lit=True)
            self.expect(")")
            return self.deep(SliceExpr(pos, ptr, length, mut), ptr, length)
        if self.at("offset"):
            self.i += 1
            self.expect("(")
            ptr = self.parse_expr(allow_struct_lit=True)
            self.expect(",")
            index = self.parse_expr(allow_struct_lit=True)
            self.expect(")")
            return self.deep(OffsetExpr(pos, ptr, index), ptr, index)
        if self.at("("):
            self.i += 1
            e = self.parse_expr(allow_struct_lit=True)
            self.expect(")")
            return e
        if t.kind == "ident":
            self.i += 1
            if asl and self.at("{"):
                return self.parse_struct_lit(pos, t.text)
            return Ident(pos, t.text)
        raise _err(pos, f"expected expression, found {t.describe()}")

    def parse_struct_lit(self, pos, name: str) -> StructLit:
        self.expect("{")
        fields = []
        while not self.at("}"):
            f = self.expect_ident()
            self.expect(":")
            fields.append((f.pos, f.text, self.parse_expr(allow_struct_lit=True)))
            if not self.eat(","):
                break
        self.expect("}")
        lit = StructLit(pos, name, fields)
        return self.deep(lit, *[f[2] for f in fields])


# ============================================================================
# 5. 의미 분석 (language.md §3, §4, §6, §7)
# ============================================================================


@dataclass
class FieldInfo:
    name: str
    ty: Ty
    off: int


@dataclass
class StructInfo:
    name: str
    fields: list  # [FieldInfo]
    size: int
    align: int
    index: dict  # name -> FieldInfo


@dataclass
class VariantInfo:
    name: str
    tag: int
    payload: list  # [FieldInfo]  (오프셋은 enum 시작 기준)


@dataclass
class EnumInfo:
    name: str
    variants: list  # [VariantInfo]
    size: int
    align: int
    index: dict  # name -> VariantInfo


@dataclass
class FnInfo:
    name: str
    decl: FnDecl
    params: list  # [(name, Ty)]
    ret: Ty
    index: int


@dataclass
class ConstInfo:
    name: str
    ty: Ty
    value: int


@dataclass(eq=False)  # 동일성으로 비교한다. 이름이 같아도 다른 슬롯이다
class Local:
    name: str
    ty: Ty
    mut: bool
    is_param: bool
    addr_taken: bool = False
    slot: int = -1  # wasm 지역변수 인덱스 (메모리 전용이면 -1)
    frame_off: int = -1  # 섀도 프레임 오프셋 (없으면 -1)

    @property
    def in_memory(self) -> bool:
        return self.frame_off >= 0


@dataclass
class FnBody:
    info: FnInfo
    locals: list = field(default_factory=list)  # 생성 순서
    frame_size: int = 0
    nparam_slots: int = 0
    nlocal_slots: int = 0


class Checker:
    def __init__(self, decls: list):
        self.decls = decls
        self.structs: dict[str, StructInfo] = {}
        self.enums: dict[str, EnumInfo] = {}
        self.consts: dict[str, ConstInfo] = {}
        self.fns: dict[str, FnInfo] = {}
        self.fn_order: list[FnInfo] = []
        self.type_names: set[str] = set()
        self.bodies: dict[str, FnBody] = {}
        self.strings: dict[bytes, int] = {}  # 바이트 -> 주소
        self.rodata_next = RODATA_ADDR
        self._const_decls: dict[str, ConstDecl] = {}
        self._const_state: dict[str, str] = {}
        self.taken: dict[str, tuple] = {}  # 최상위 이름 -> 위치
        # 함수별 상태
        self.scopes: list[dict] = []
        self.cur: Optional[FnBody] = None
        self.loop_depth = 0
        self.unsafe_depth = 0

    # --- 이름 등록 ---------------------------------------------------------

    def claim(self, name: str, pos):
        if name in self.taken:
            raise _err(pos, f"duplicate top-level name `{name}`")
        if name in ("i32", "u32", "bool", "u8"):
            raise _err(pos, f"`{name}` is a built-in type name")
        self.taken[name] = pos

    def run(self):
        # 1패스: 선언 수집. 최상위는 순서에 상관없다 (language.md §4)
        struct_decls, enum_decls, const_decls, fn_decls = [], [], [], []
        for d in self.decls:
            self.claim(d.name, d.pos)
            if isinstance(d, StructDecl):
                struct_decls.append(d)
            elif isinstance(d, EnumDecl):
                enum_decls.append(d)
            elif isinstance(d, ConstDecl):
                const_decls.append(d)
            else:
                fn_decls.append(d)

        # 타입 이름을 먼저 전부 등록한다. `struct Node { next: *Node }` 가 되어야 한다.
        # 필드는 집합체가 될 수 없으므로 배치는 서로를 필요로 하지 않는다 (language.md §4)
        for d in struct_decls + enum_decls:
            self.type_names.add(d.name)
        for d in struct_decls:
            self.declare_struct(d)
        for d in enum_decls:
            self.declare_enum(d)
        # const 는 서로를 참조할 수 있다. 먼저 다 등록하고 그다음 값을 낸다
        for d in const_decls:
            self.declare_const(d)
        for d in const_decls:
            self.force_const(d.name, d.pos)
        for i, d in enumerate(fn_decls):
            self.declare_fn(d, i)

        # 2패스: 본문
        for info in self.fn_order:
            self.check_fn(info)
        return self

    # --- 타입 해석 ---------------------------------------------------------

    def resolve_ty(self, t: TyNode) -> Ty:
        if t.kind == "prim":
            return {"i32": I32, "u32": U32, "bool": BOOL, "u8": U8}[t.name]
        if t.kind == "named":
            if t.name in self.type_names:
                return Named(t.name)
            raise _err(t.pos, f"unknown type `{t.name}`")
        if t.kind == "slice":
            return Slice(self.resolve_ty(t.inner), t.mut)
        if t.kind == "ref":
            return Ref(self.resolve_ty(t.inner), t.mut)
        if t.kind == "ptr":
            return Ptr(self.resolve_ty(t.inner))
        raise AssertionError(t.kind)

    def size_of(self, t: Ty) -> int:
        if t in (I32, U32):
            return 4
        if t in (BOOL, U8):
            return 1
        if isinstance(t, Slice):
            return 8
        if isinstance(t, (Ref, Ptr)):
            return 4
        if isinstance(t, Named):
            return self.agg(t.name).size
        raise AssertionError(ty_str(t))

    def align_of(self, t: Ty) -> int:
        if t in (I32, U32) or isinstance(t, (Slice, Ref, Ptr)):
            return 4
        if t in (BOOL, U8):
            return 1
        if isinstance(t, Named):
            return self.agg(t.name).align
        raise AssertionError(ty_str(t))

    def agg(self, name: str):
        return self.structs.get(name) or self.enums[name]

    def check_field_ty(self, t: Ty, pos, what: str):
        """필드·페이로드 타입 제약 (language.md §4): 집합체와 대여는 담을 수 없다."""
        if is_aggregate(t):
            raise _err(pos, f"{what} cannot have aggregate type `{ty_str(t)}`")
        if isinstance(t, Ref):
            raise _err(pos, f"{what} cannot have borrow type `{ty_str(t)}`")

    def layout(self, entries, base: int):
        """(pos, name, Ty) 목록을 선언 순서대로 배치한다 (language.md §4)."""
        off, align = base, 1
        out = []
        for _, name, t in entries:
            a = self.align_of(t)
            off = align_up(off, a)
            out.append(FieldInfo(name, t, off))
            off += self.size_of(t)
            align = max(align, a)
        return out, align_up(off, align), align

    def declare_struct(self, d: StructDecl):
        seen = set()
        entries = []
        for pos, name, tn in d.fields:
            if name in seen:
                raise _err(pos, f"duplicate field `{name}`")
            seen.add(name)
            t = self.resolve_ty(tn)
            self.check_field_ty(t, pos, "struct field")
            entries.append((pos, name, t))
        fields, size, align = self.layout(entries, 0)
        self.structs[d.name] = StructInfo(
            d.name, fields, size, align, {f.name: f for f in fields}
        )

    def declare_enum(self, d: EnumDecl):
        seen = set()
        variants = []
        slot = 0
        for tag, (pos, name, payload_nodes) in enumerate(d.variants):
            if name in seen:
                raise _err(pos, f"duplicate variant `{name}`")
            seen.add(name)
            entries = []
            for tn in payload_nodes:
                t = self.resolve_ty(tn)
                self.check_field_ty(t, tn.pos, "enum payload")
                entries.append((tn.pos, "", t))
            fields, size, _ = self.layout(entries, 0)
            slot = max(slot, size)
            variants.append(VariantInfo(name, tag, fields))
        # 태그(u32) + 페이로드 슬롯. 슬롯은 오프셋 4 에서 시작한다 (language.md §4)
        for v in variants:
            v.payload = [FieldInfo(f.name, f.ty, f.off + 4) for f in v.payload]
        size = align_up(4 + slot, 4)
        self.enums[d.name] = EnumInfo(
            d.name, variants, size, 4, {v.name: v for v in variants}
        )

    def declare_const(self, d: ConstDecl):
        t = self.resolve_ty(d.ty_node)
        if t not in (I32, U32, BOOL):
            raise _err(d.pos, f"const must have type i32, u32 or bool, found `{ty_str(t)}`")
        self.consts[d.name] = ConstInfo(d.name, t, None)
        self._const_decls[d.name] = d
        self._const_state[d.name] = "pending"

    def force_const(self, name: str, pos):
        st = self._const_state.get(name)
        if st == "done":
            return self.consts[name]
        if st == "busy":
            raise _err(pos, f"const `{name}` depends on itself")
        self._const_state[name] = "busy"
        d = self._const_decls[name]
        info = self.consts[name]
        value = self.eval_const(d.value, info.ty)
        info.value = value
        self._const_state[name] = "done"
        return info

    def eval_const(self, e: Node, want: Ty) -> int:
        v, t = self.const_expr(e, want)
        if t is INTLIT:
            t = want if want in INT_TYPES else I32
        if t != want:
            raise _err(e.pos, f"expected `{ty_str(want)}`, found `{ty_str(t)}`")
        return v & 0xFFFF_FFFF if want in INT_TYPES else v

    def const_expr(self, e: Node, want: Optional[Ty] = None):
        """상수식 평가. (값, 타입) 을 돌려준다. 정수는 32비트로 랩어라운드.

        `want` 는 굳지 않은 정수 리터럴을 위해 아래로 흐른다 -- 검사기와 같은 규칙이다.
        """
        M = 0xFFFF_FFFF
        int_want = want if want in INT_TYPES else None
        if isinstance(e, Int):
            return e.value & M, INTLIT
        if isinstance(e, Char):
            return e.value, U32
        if isinstance(e, Bool):
            return (1 if e.value else 0), BOOL
        if isinstance(e, Ident):
            if e.name in self.consts:
                if self._const_state.get(e.name) != "done":
                    self.force_const(e.name, e.pos)
                info = self.consts[e.name]
                return info.value, info.ty
            raise _err(e.pos, f"`{e.name}` is not a constant")
        if isinstance(e, Cast):
            v, t = self.const_expr(e.operand)
            dst = self.resolve_ty(e.ty_node)
            if not (is_int(t) or isinstance(t, Ptr)):
                raise _err(e.pos, f"cannot cast from `{ty_str(t)}`")
            if dst not in INT_TYPES and not isinstance(dst, Ptr):
                raise _err(e.pos, f"cannot cast to `{ty_str(dst)}`")
            return v & M, dst
        if isinstance(e, Unary):
            v, t = self.const_expr(e.operand, want if e.op == "-" else BOOL)
            if e.op == "-":
                if not is_int(t):
                    raise _err(e.pos, f"cannot negate `{ty_str(t)}`")
                return (-v) & M, t
            if t is not BOOL:
                raise _err(e.pos, f"cannot apply `!` to `{ty_str(t)}`")
            return 1 - v, BOOL
        if isinstance(e, Binary):
            op = e.op
            cmp = op in CMP_OPS or op in ("&&", "||")
            lv, lt = self.const_expr(e.lhs, None if cmp else want)
            rv, rt = self.const_expr(
                e.rhs,
                U32 if op in ("<<", ">>") else (None if cmp else (lt if lt is not INTLIT else want)),
            )
            if op in ("&&", "||"):
                if lt is not BOOL or rt is not BOOL:
                    raise _err(e.pos, f"`{op}` requires bool operands")
                return (int(bool(lv) and bool(rv)) if op == "&&" else int(bool(lv) or bool(rv))), BOOL
            if op in ("<<", ">>"):
                if not is_int(lt) or not is_int(rt):
                    raise _err(e.pos, f"`{op}` requires integer operands")
                # the shift amount is `u32` here for the same reason it is
                # everywhere else (language.md §5) -- a constant is not a
                # weaker position than an expression
                if rt is not INTLIT and rt is not U32:
                    raise _err(e.rhs.pos, f"expected `u32`, found `{ty_str(rt)}`")
                t = lt if lt is not INTLIT else (int_want or I32)
                sh = rv & 31
                if op == "<<":
                    return (lv << sh) & M, t
                if t is I32:
                    return (to_signed(lv) >> sh) & M, t
                return (lv >> sh) & M, t
            if not is_int(lt) or not is_int(rt):
                if op in ("==", "!=") and lt is BOOL and rt is BOOL:
                    return int((lv == rv) if op == "==" else (lv != rv)), BOOL
                raise _err(e.pos, f"`{op}` requires integer operands")
            # cool0 has no implicit i32/u32 conversion, in a constant either
            # (language.md §3). An unsettled literal takes the other side's
            # type; two settled ones have to already agree.
            if lt is not INTLIT and rt is not INTLIT and lt is not rt:
                raise _err(e.pos, f"`{op}`: `{ty_str(lt)}` and `{ty_str(rt)}` do not match")
            t = lt if lt is not INTLIT else rt
            if t is INTLIT:
                t = (None if cmp else int_want) or I32
            signed = t is I32
            a = to_signed(lv) if signed else lv
            b = to_signed(rv) if signed else rv
            if op in CMP_OPS:
                res = {
                    "==": a == b, "!=": a != b, "<": a < b,
                    ">": a > b, "<=": a <= b, ">=": a >= b,
                }[op]
                return int(res), BOOL
            if op in ("/", "%") and b == 0:
                raise _err(e.pos, "division by zero in constant expression")
            if op == "+":
                r = a + b
            elif op == "-":
                r = a - b
            elif op == "*":
                r = a * b
            elif op == "/":
                r = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
            elif op == "%":
                r = abs(a) % abs(b) * (1 if a >= 0 else -1)
            elif op == "&":
                r = lv & rv
            elif op == "|":
                r = lv | rv
            elif op == "^":
                r = lv ^ rv
            else:
                raise _err(e.pos, f"`{op}` is not allowed in a constant expression")
            return r & M, t
        raise _err(e.pos, "not a constant expression")

    def declare_fn(self, d: FnDecl, index: int):
        params = []
        seen = set()
        for pos, name, tn in d.params:
            if name in seen:
                raise _err(pos, f"duplicate parameter `{name}`")
            seen.add(name)
            t = self.resolve_ty(tn)
            if is_aggregate(t):
                raise _err(pos, f"cannot pass aggregate `{ty_str(t)}` by value")
            if t is U8:
                raise _err(pos, "`u8` is storage-only; use `u32`")
            params.append((name, t))
        ret = self.resolve_ty(d.ret) if d.ret else VOID
        if is_aggregate(ret):
            raise _err(d.ret.pos, f"cannot return aggregate `{ty_str(ret)}` by value")
        if isinstance(ret, Slice):
            raise _err(d.ret.pos, "cannot return a slice (wasm 1.0 has a single result)")
        if isinstance(ret, Ref):
            raise _err(d.ret.pos, "cannot return a borrow")
        if ret is U8:
            raise _err(d.ret.pos, "`u8` is storage-only; use `u32`")
        info = FnInfo(d.name, d, params, ret, index)
        self.fns[d.name] = info
        self.fn_order.append(info)

    # --- 지역 스코프 -------------------------------------------------------

    def push_scope(self):
        self.scopes.append({})

    def pop_scope(self):
        self.scopes.pop()

    def lookup(self, name: str) -> Optional[Local]:
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return None

    def declare_local(self, name: str, ty: Ty, mut: bool, is_param: bool, pos) -> Local:
        if name in self.scopes[-1]:
            raise _err(pos, f"`{name}` is already declared in this scope")
        if name in self.taken:
            raise _err(pos, f"`{name}` shadows a top-level name")
        loc = Local(name, ty, mut, is_param)
        self.scopes[-1][name] = loc
        self.cur.locals.append(loc)
        return loc

    # --- 함수 본문 ---------------------------------------------------------

    def check_fn(self, info: FnInfo):
        body = FnBody(info)
        self.bodies[info.name] = body
        self.cur = body
        self.scopes = [{}]
        self.loop_depth = 0
        self.unsafe_depth = 0
        for (pos, name, _), (_, t) in zip(info.decl.params, info.params):
            self.declare_local(name, t, False, True, pos)
        self.check_block(info.decl.body)
        if info.ret is not VOID and not diverges(info.decl.body):
            raise _err(info.decl.pos, f"function `{info.name}` must return a value on every path")
        self.pop_scope()
        self.cur = None

    def check_block(self, stmts: list):
        self.push_scope()
        for s in stmts:
            self.check_stmt(s)
        self.scopes.pop()

    def check_stmt(self, s: Node):
        if isinstance(s, Let):
            declared = self.resolve_ty(s.ty_node) if s.ty_node else None
            if declared is not None:
                self.check_local_ty(declared, s.pos)
            t = self.check_init(s.init, declared)
            if declared is None:
                if t is INTLIT:
                    t = I32
                    self.retype(s.init, I32)
                self.check_local_ty(t, s.pos)
            s.local = self.declare_local(s.name, declared or t, s.mut, False, s.pos)
            return

        if isinstance(s, Assign):
            ty, mut, _ = self.check_place(s.target)
            if not mut:
                raise _err(s.pos, "cannot assign to an immutable place")
            want = read_ty(ty)
            if s.op == "=":
                self.check_init(s.value, want)
            else:
                op = s.op[:-1]
                if op in ("<<", ">>"):
                    if not is_int(want):
                        raise _err(s.pos, f"`{op}` requires an integer, found `{ty_str(want)}`")
                    self.coerce(s.value, U32)
                else:
                    self.coerce(s.value, want)
                    self.check_binop_types(s.pos, op, want, want, s.value)
            return

        if isinstance(s, ExprStmt):
            t = self.check_expr(s.expr, None)
            s.discard = t is not VOID
            return

        if isinstance(s, If):
            self.coerce(s.cond, BOOL)
            self.check_block(s.then)
            if s.els is not None:
                self.check_block(s.els)
            return

        if isinstance(s, For):
            self.push_scope()
            if s.init is not None:
                self.check_stmt(s.init)
            if s.cond is not None:
                self.coerce(s.cond, BOOL)
            self.loop_depth += 1
            self.check_block(s.body)
            if s.post is not None:
                self.check_stmt(s.post)
            self.loop_depth -= 1
            self.scopes.pop()
            return

        if isinstance(s, (Break, Continue)):
            if self.loop_depth == 0:
                word = "break" if isinstance(s, Break) else "continue"
                raise _err(s.pos, f"`{word}` outside of a loop")
            return

        if isinstance(s, Return):
            ret = self.cur.info.ret
            if s.value is None:
                if ret is not VOID:
                    raise _err(s.pos, f"expected `{ty_str(ret)}`, found no value")
            else:
                if ret is VOID:
                    raise _err(s.pos, "function has no return type")
                self.coerce(s.value, ret)
            return

        if isinstance(s, Match):
            self.check_match(s)
            return

        if isinstance(s, Unsafe):
            self.unsafe_depth += 1
            self.check_block(s.body)
            self.unsafe_depth -= 1
            return

        raise AssertionError(type(s))

    def check_local_ty(self, t: Ty, pos):
        if t is U8:
            raise _err(pos, "`u8` is storage-only; there are no u8 locals")
        if isinstance(t, Ref):
            raise _err(pos, "a borrow cannot be bound to a local")
        if t is VOID:
            raise _err(pos, "local cannot have type `void`")

    def check_match(self, s: Match):
        ty, _, _ = self.check_place(s.scrutinee)
        if not (isinstance(ty, Named) and ty.name in self.enums):
            raise _err(s.pos, f"`match` requires an enum, found `{ty_str(ty)}`")
        info = self.enums[ty.name]
        s.enum_info = info
        seen = set()
        has_wild = False
        for i, arm in enumerate(s.arms):
            if arm.variant is None:
                if i != len(s.arms) - 1:
                    raise _err(arm.pos, "`_` must be the last arm")
                has_wild = True
                arm.variant_info = None
                self.push_scope()
                self.check_block(arm.body)
                self.scopes.pop()
                continue
            if arm.variant not in info.index:
                raise _err(arm.pos, f"`{ty.name}` has no variant `{arm.variant}`")
            if arm.variant in seen:
                raise _err(arm.pos, f"duplicate arm for `{arm.variant}`")
            seen.add(arm.variant)
            v = info.index[arm.variant]
            arm.variant_info = v
            if len(arm.binds) != len(v.payload):
                raise _err(
                    arm.pos,
                    f"variant `{arm.variant}` takes {len(v.payload)} binding(s), "
                    f"found {len(arm.binds)}",
                )
            self.push_scope()
            arm.bind_locals = []
            for (bpos, bname), f in zip(arm.binds, v.payload):
                arm.bind_locals.append(
                    self.declare_local(bname, read_ty(f.ty), False, False, bpos)
                )
            self.check_block(arm.body)
            self.scopes.pop()
        if not has_wild and len(seen) != len(info.variants):
            missing = [v.name for v in info.variants if v.name not in seen]
            raise _err(s.pos, f"non-exhaustive match: missing `{missing[0]}`")
        s.has_wild = has_wild

    # --- 식 ---------------------------------------------------------------

    def check_init(self, e: Node, want: Optional[Ty]) -> Ty:
        """let 초기화·대입 우변. 여기서만 집합체 리터럴이 허용된다 (language.md §5)."""
        if isinstance(e, StructLit):
            return self.check_struct_lit(e, want)
        if self.is_enum_lit(e):
            return self.check_enum_lit(e, want)
        if want is not None and not is_aggregate(want):
            return self.coerce(e, want)
        t = self.check_expr(e, want)
        if is_aggregate(t):
            raise _err(e.pos, f"cannot copy aggregate `{ty_str(t)}`")
        if want is not None and t != want:
            raise _err(e.pos, f"expected `{ty_str(want)}`, found `{ty_str(t)}`")
        return t

    def is_enum_lit(self, e: Node) -> bool:
        base = e.callee if isinstance(e, Call) else e
        return (
            isinstance(base, Field)
            and isinstance(base.base, Ident)
            and base.base.name in self.enums
        )

    def check_struct_lit(self, e: StructLit, want: Optional[Ty]) -> Ty:
        if e.name not in self.structs:
            raise _err(e.pos, f"unknown struct `{e.name}`")
        info = self.structs[e.name]
        if want is not None and want != Named(e.name):
            raise _err(e.pos, f"expected `{ty_str(want)}`, found `{e.name}`")
        if len(e.fields) != len(info.fields):
            raise _err(e.pos, f"`{e.name}` has {len(info.fields)} field(s)")
        for (pos, name, val), f in zip(e.fields, info.fields):
            if name != f.name:
                raise _err(pos, f"expected field `{f.name}`, found `{name}`")
            self.coerce(val, read_ty(f.ty))
        e.struct_info = info
        e.ty = Named(e.name)
        return e.ty

    def check_enum_lit(self, e: Node, want: Optional[Ty]) -> Ty:
        call = e if isinstance(e, Call) else None
        fld = call.callee if call else e
        info = self.enums[fld.base.name]
        if fld.name not in info.index:
            raise _err(fld.pos, f"`{info.name}` has no variant `{fld.name}`")
        v = info.index[fld.name]
        args = call.args if call else []
        if len(args) != len(v.payload):
            raise _err(e.pos, f"variant `{v.name}` takes {len(v.payload)} value(s)")
        if want is not None and want != Named(info.name):
            raise _err(e.pos, f"expected `{ty_str(want)}`, found `{info.name}`")
        for a, f in zip(args, v.payload):
            self.coerce(a, read_ty(f.ty))
        e.enum_info = info
        e.variant_info = v
        e.enum_args = args
        e.ty = Named(info.name)
        return e.ty

    def retype(self, e: Node, t: Ty):
        """굳지 않은 정수 리터럴 하위식을 t 로 굳힌다."""
        if getattr(e, "ty", None) is not INTLIT:
            return
        e.ty = t
        if isinstance(e, Unary):
            self.retype(e.operand, t)
        elif isinstance(e, Binary):
            self.retype(e.lhs, t)
            if e.op not in ("<<", ">>"):
                self.retype(e.rhs, t)

    def coerce(self, e: Node, want: Ty) -> Ty:
        got = self.check_expr(e, want)
        if got is INTLIT and want in INT_TYPES:
            self.retype(e, want)
            return want
        if (
            isinstance(got, Ref)
            and isinstance(want, Ref)
            and got.mut
            and not want.mut
            and got.inner == want.inner
        ) or (
            isinstance(got, Slice)
            and isinstance(want, Slice)
            and got.mut
            and not want.mut
            and got.elem == want.elem
        ):
            return want
        if got != want:
            raise _err(e.pos, f"expected `{ty_str(want)}`, found `{ty_str(got)}`")
        return want

    def check_expr(self, e: Node, want: Optional[Ty]) -> Ty:
        t = self._check_expr(e, want)
        e.ty = t
        return t

    def _check_expr(self, e: Node, want: Optional[Ty]) -> Ty:
        if isinstance(e, Int):
            return INTLIT
        if isinstance(e, Char):
            return U32
        if isinstance(e, Bool):
            return BOOL
        if isinstance(e, Str):
            e.addr = self.intern(e.value, e.pos)
            return Slice(U8, False)
        if isinstance(e, StructLit):
            raise _err(e.pos, "struct literal is only allowed as an initializer")

        if isinstance(e, SliceExpr):
            if self.unsafe_depth == 0:
                raise _err(e.pos, f"`{'slice_mut' if e.mut else 'slice'}` requires `unsafe`")
            pt = self.check_expr(e.ptr, None)
            if not isinstance(pt, Ptr):
                raise _err(e.ptr.pos, f"expected a raw pointer, found `{ty_str(pt)}`")
            self.coerce(e.length, U32)
            return Slice(pt.inner, e.mut)

        if isinstance(e, OffsetExpr):
            # `unsafe` 를 요구하지 않는다. 주소를 만드는 것은 이미 `as` 로도
            # 되고 (§8), `*T` 는 역참조 전까지 아무것도 아니다. 위험은 `p.^`
            # 와 `slice` 에 있고 거기엔 이미 표시가 붙어 있다
            pt = self.check_expr(e.ptr, None)
            if not isinstance(pt, Ptr):
                raise _err(e.ptr.pos, f"expected a raw pointer, found `{ty_str(pt)}`")
            self.coerce(e.index, U32)
            e.stride = self.size_of(pt.inner)
            return pt

        if isinstance(e, Ident):
            loc = self.lookup(e.name)
            if loc is not None:
                e.local = loc
                return read_ty(loc.ty)
            if e.name in self.consts:
                info = self.force_const(e.name, e.pos)
                e.const_value = info.value
                return info.ty
            if e.name in self.fns:
                raise _err(e.pos, "functions are not values")
            raise _err(e.pos, f"unknown name `{e.name}`")

        if isinstance(e, Unary):
            if e.op == "!":
                self.coerce(e.operand, BOOL)
                return BOOL
            t = self.check_expr(e.operand, want if want in INT_TYPES else None)
            if not is_int(t):
                raise _err(e.pos, f"cannot negate `{ty_str(t)}`")
            return t

        if isinstance(e, Borrow):
            raise _err(e.pos, "a borrow may only appear as a call argument")

        if isinstance(e, Cast):
            src = self.check_expr(e.operand, None)
            if src is INTLIT:
                self.retype(e.operand, I32)
                src = I32
            dst = self.resolve_ty(e.ty_node)
            ok_src = src in INT_TYPES or isinstance(src, Ptr)
            ok_dst = dst in INT_TYPES or isinstance(dst, Ptr)
            if not ok_src:
                raise _err(e.pos, f"cannot cast from `{ty_str(src)}`")
            if not ok_dst:
                raise _err(e.pos, f"cannot cast to `{ty_str(dst)}`")
            return dst

        if isinstance(e, Binary):
            return self.check_binary(e, want)

        if isinstance(e, Call):
            if self.is_enum_lit(e):
                raise _err(e.pos, "enum literal is only allowed as an initializer")
            return self.check_call(e)

        if isinstance(e, (Index, Field, Deref)):
            if isinstance(e, Field) and self.is_enum_lit(e):
                raise _err(e.pos, "enum literal is only allowed as an initializer")
            if isinstance(e, Field):
                field = self.slice_field(e)
                bt = self.check_expr(e.base, None) if field is not None else None
                if bt is not None:
                    e.slice_field = field
                    return U32 if field == "len" else Ptr(bt.elem)
            ty, _, _ = self.check_place(e)
            if is_aggregate(ty):
                return ty  # 장소로만 쓰인다. 상위에서 거른다
            return read_ty(ty)

        raise AssertionError(type(e))

    def slice_field(self, e: Field) -> Optional[str]:
        if e.name not in ("len", "ptr"):
            return None
        if isinstance(e.base, Ident) and e.base.name in self.enums:
            return None
        try:
            probe = self.probe_ty(e.base)
        except CompileError:
            return None
        return e.name if isinstance(probe, Slice) else None

    def probe_ty(self, e: Node) -> Ty:
        """부작용 없이 타입만 미리 본다 (`.len` 판별용)."""
        saved = getattr(e, "ty", None)
        t = self.check_expr(e, None)
        if saved is not None:
            e.ty = saved
        return t

    def check_binary(self, e: Binary, want: Optional[Ty]) -> Ty:
        op = e.op
        if op in ("&&", "||"):
            self.coerce(e.lhs, BOOL)
            self.coerce(e.rhs, BOOL)
            return BOOL
        int_want = want if want in INT_TYPES else None
        cmp = op in CMP_OPS

        if op in ("<<", ">>"):
            lt = self.check_expr(e.lhs, int_want)
            self.coerce(e.rhs, U32)
            if lt is INTLIT:
                if int_want is None:
                    e.opnd_ty = INTLIT
                    return INTLIT  # 아직 굳지 않았다. 부모가 굳힌다
                self.retype(e.lhs, int_want)
                lt = int_want
            if not is_int(lt):
                raise _err(e.pos, f"`{op}` requires an integer, found `{ty_str(lt)}`")
            e.opnd_ty = lt
            return lt

        lt = self.check_expr(e.lhs, None if cmp else int_want)
        rt = self.check_expr(e.rhs, lt if lt is not INTLIT else (None if cmp else int_want))
        if lt is INTLIT and rt is not INTLIT:
            self.retype(e.lhs, rt)
            lt = rt
        elif rt is INTLIT and lt is not INTLIT:
            self.retype(e.rhs, lt)
            rt = lt
        elif lt is INTLIT and rt is INTLIT:
            if not cmp:
                e.opnd_ty = INTLIT
                return INTLIT  # 통째로 미룬다. `1 + 2 == x` 가 이래야 산다
            self.retype(e.lhs, I32)
            self.retype(e.rhs, I32)
            lt = rt = I32
        self.check_binop_types(e.pos, op, lt, rt, e.rhs)
        e.opnd_ty = lt
        return BOOL if cmp else lt

    def check_binop_types(self, pos, op: str, lt: Ty, rt: Ty, rhs: Node):
        if op in ("<<", ">>"):
            if not is_int(lt):
                raise _err(pos, f"`{op}` requires an integer, found `{ty_str(lt)}`")
            if rt is not U32:
                raise _err(pos, f"shift amount must be `u32`, found `{ty_str(rt)}`")
            return
        if lt != rt:
            raise _err(pos, f"`{op}`: `{ty_str(lt)}` and `{ty_str(rt)}` do not match")
        if op in ("==", "!="):
            if not (lt in (I32, U32, BOOL) or isinstance(lt, Ptr)):
                raise _err(pos, f"cannot compare `{ty_str(lt)}`")
            return
        if op in ("<", ">", "<=", ">="):
            if not (lt in (I32, U32) or isinstance(lt, Ptr)):
                raise _err(pos, f"cannot order `{ty_str(lt)}`")
            return
        if lt not in (I32, U32):
            raise _err(pos, f"`{op}` requires `i32` or `u32`, found `{ty_str(lt)}`")

    def check_call(self, e: Call) -> Ty:
        if not isinstance(e.callee, Ident):
            raise _err(e.pos, "callee must be a function name")
        name = e.callee.name
        if name not in self.fns:
            raise _err(e.pos, f"unknown function `{name}`")
        info = self.fns[name]
        if len(e.args) != len(info.params):
            raise _err(
                e.pos,
                f"`{name}` takes {len(info.params)} argument(s), found {len(e.args)}",
            )
        for a, (_, pt) in zip(e.args, info.params):
            if isinstance(a, Borrow):
                self.check_borrow(a, pt)
            else:
                self.coerce(a, pt)
        self.check_aliasing(e)
        e.fn_info = info
        return info.ret

    def check_borrow(self, b: Borrow, want: Ty):
        if isinstance(b.operand, StructLit):
            raise _err(b.operand.pos, "struct literal is only allowed as an initializer")
        if self.is_enum_lit(b.operand):
            raise _err(b.operand.pos, "enum literal is only allowed as an initializer")
        ty, mut, root = self.check_place(b.operand)
        if not isinstance(want, Ref):
            raise _err(b.pos, f"expected `{ty_str(want)}`, found a borrow")
        if b.mut and not mut:
            raise _err(b.pos, "cannot borrow an immutable place as `&mut`")
        if want.mut != b.mut or want.inner != ty:
            raise _err(b.pos, f"expected `{ty_str(want)}`, found `{ty_str(Ref(ty, b.mut))}`")
        if root is not None:
            root.addr_taken = True
        b.root = root
        b.ty = Ref(ty, b.mut)

    def check_aliasing(self, e: Call):
        """language.md §7 -- 한 인자 목록에서 `&mut` 로 빌린 지역변수가 다른 곳에도 나오면 오류."""
        mentions = [self.mentioned_locals(a) for a in e.args]
        for i, a in enumerate(e.args):
            if not (isinstance(a, Borrow) and a.mut and a.root is not None):
                continue
            for j, other in enumerate(e.args):
                if i == j:
                    continue
                if a.root in mentions[j]:
                    raise _err(
                        e.pos,
                        f"`{a.root.name}` is borrowed mutably and also used "
                        f"in the same argument list",
                    )
            if self.mention_count(a.operand, a.root) > 1:
                raise _err(e.pos, f"`{a.root.name}` is borrowed mutably more than once")

    def mentioned_locals(self, e: Node) -> set:
        out = set()
        self.walk_locals(e, out.add)
        return out

    def mention_count(self, e: Node, target: Local) -> int:
        n = 0

        def visit(loc):
            nonlocal n
            if loc is target:
                n += 1

        self.walk_locals(e, visit)
        return n

    def walk_locals(self, e: Node, visit):
        if isinstance(e, Ident):
            loc = getattr(e, "local", None)
            if loc is not None:
                visit(loc)
        for name in ("operand", "lhs", "rhs", "base", "index", "callee", "ptr", "length"):
            child = getattr(e, name, None)
            if isinstance(child, Node):
                self.walk_locals(child, visit)
        for name in ("args", "enum_args"):
            for child in getattr(e, name, ()) or ():
                self.walk_locals(child, visit)
        if isinstance(e, StructLit):
            for _, _, v in e.fields:
                self.walk_locals(v, visit)

    # --- 장소 -------------------------------------------------------------

    def check_place(self, e: Node):
        """(타입, 가변인가, 뿌리 지역변수) 를 돌려준다."""
        if isinstance(e, Ident):
            loc = self.lookup(e.name)
            if loc is None:
                if e.name in self.consts:
                    raise _err(e.pos, f"`{e.name}` is a constant, not a place")
                if e.name in self.structs or e.name in self.enums:
                    raise _err(e.pos, f"`{e.name}` is a type, not a place")
                raise _err(e.pos, f"unknown name `{e.name}`")
            e.local = loc
            e.ty = read_ty(loc.ty)
            return loc.ty, loc.mut, loc

        if isinstance(e, Field):
            bt, bmut, root = self.check_place(e.base)
            if not (isinstance(bt, Named) and bt.name in self.structs):
                raise _err(e.pos, f"`{ty_str(bt)}` has no field `{e.name}`")
            info = self.structs[bt.name]
            if e.name not in info.index:
                raise _err(e.pos, f"`{bt.name}` has no field `{e.name}`")
            f = info.index[e.name]
            e.field_info = f
            e.ty = read_ty(f.ty)
            return f.ty, bmut, root

        if isinstance(e, Index):
            bt = self.check_expr(e.base, None)
            if not isinstance(bt, Slice):
                raise _err(e.pos, f"cannot index `{ty_str(bt)}`")
            self.coerce(e.index, U32)
            e.elem_size = self.size_of(bt.elem)
            e.ty = read_ty(bt.elem)
            root = self.root_local(e.base)
            return bt.elem, bt.mut, root

        if isinstance(e, Deref):
            bt = self.check_expr(e.base, None)
            if isinstance(bt, Ref):
                e.ty = read_ty(bt.inner)
                return bt.inner, bt.mut, self.root_local(e.base)
            if isinstance(bt, Ptr):
                if self.unsafe_depth == 0:
                    raise _err(e.pos, "raw pointer dereference requires `unsafe`")
                e.ty = read_ty(bt.inner)
                return bt.inner, True, self.root_local(e.base)
            raise _err(e.pos, f"cannot dereference `{ty_str(bt)}`")

        raise _err(e.pos, "expected a place expression")

    def root_local(self, e: Node) -> Optional[Local]:
        while True:
            if isinstance(e, Ident):
                return getattr(e, "local", None)
            if isinstance(e, (Field, Index, Deref)):
                e = e.base
                continue
            return None

    # --- 문자열 상수 -------------------------------------------------------

    def intern(self, data: bytes, pos=(1, 1)) -> int:
        """문자열 상수를 rodata 에 놓고 주소를 준다.

        rodata 는 위로 자라고 그 위에는 섀도 스택 바닥이 있다 (implementation.md §7).
        닿으면 컴파일된 프로그램의 리터럴이 자기 프레임과 겹치므로, 여기서 막는다.
        """
        if data in self.strings:
            return self.strings[data]
        addr = self.rodata_next
        if addr + len(data) > SHADOW_FLOOR:
            raise _err(pos, "string literals do not fit below the shadow stack")
        self.strings[data] = addr
        self.rodata_next = addr + len(data)
        return addr


def to_signed(v: int) -> int:
    v &= 0xFFFF_FFFF
    return v - 0x1_0000_0000 if v >= 0x8000_0000 else v


def diverges(stmts: list) -> bool:
    """이 블록이 반드시 흐름을 끊는가 (return/break/continue/무한루프)."""
    for s in stmts:
        if isinstance(s, (Return, Break, Continue)):
            return True
        if isinstance(s, If):
            if s.els is not None and diverges(s.then) and diverges(s.els):
                return True
        elif isinstance(s, For):
            if s.cond is None and not has_break(s.body):
                return True
        elif isinstance(s, Match):
            if (s.has_wild or len(s.arms) == len(s.enum_info.variants)) and all(
                diverges(a.body) for a in s.arms
            ):
                return True
        elif isinstance(s, Unsafe):
            if diverges(s.body):
                return True
    return False


def has_break(stmts: list) -> bool:
    """이 루프 몸통에 (중첩 루프 것이 아닌) break 가 있는가."""
    for s in stmts:
        if isinstance(s, Break):
            return True
        if isinstance(s, If):
            if has_break(s.then) or (s.els is not None and has_break(s.els)):
                return True
        elif isinstance(s, Match):
            if any(has_break(a.body) for a in s.arms):
                return True
        elif isinstance(s, Unsafe):
            if has_break(s.body):
                return True
    return False


# ============================================================================
# 6. wasm 이진 인코딩
# ============================================================================

SEC_TYPE, SEC_FUNC, SEC_MEM, SEC_GLOBAL, SEC_EXPORT, SEC_CODE, SEC_DATA = 1, 3, 5, 6, 7, 10, 11

# 명령
OP_UNREACHABLE = 0x00
OP_BLOCK, OP_LOOP, OP_IF, OP_ELSE, OP_END = 0x02, 0x03, 0x04, 0x05, 0x0B
OP_BR, OP_BR_IF, OP_RETURN, OP_CALL = 0x0C, 0x0D, 0x0F, 0x10
OP_DROP = 0x1A
OP_LOCAL_GET, OP_LOCAL_SET, OP_LOCAL_TEE = 0x20, 0x21, 0x22
OP_GLOBAL_GET, OP_GLOBAL_SET = 0x23, 0x24
OP_I32_LOAD, OP_I32_LOAD8_U = 0x28, 0x2D
OP_I32_STORE, OP_I32_STORE8 = 0x36, 0x3A
OP_I32_CONST = 0x41
OP_I32_EQZ = 0x45
OP_I32_ADD, OP_I32_SUB, OP_I32_MUL = 0x6A, 0x6B, 0x6C
TYPE_I32 = 0x7F
BLOCK_VOID = 0x40

CMP_OPCODE = {
    ("==", False): 0x46, ("==", True): 0x46,
    ("!=", False): 0x47, ("!=", True): 0x47,
    ("<", True): 0x48, ("<", False): 0x49,
    (">", True): 0x4A, (">", False): 0x4B,
    ("<=", True): 0x4C, ("<=", False): 0x4D,
    (">=", True): 0x4E, (">=", False): 0x4F,
}
ARITH_OPCODE = {
    ("+", True): 0x6A, ("+", False): 0x6A,
    ("-", True): 0x6B, ("-", False): 0x6B,
    ("*", True): 0x6C, ("*", False): 0x6C,
    ("/", True): 0x6D, ("/", False): 0x6E,
    ("%", True): 0x6F, ("%", False): 0x70,
    ("&", True): 0x71, ("&", False): 0x71,
    ("|", True): 0x72, ("|", False): 0x72,
    ("^", True): 0x73, ("^", False): 0x73,
    ("<<", True): 0x74, ("<<", False): 0x74,
    (">>", True): 0x75, (">>", False): 0x76,
}


def leb_u(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def leb_s(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        done = (n == 0 and not (b & 0x40)) or (n == -1 and (b & 0x40))
        out.append(b if done else b | 0x80)
        if done:
            return bytes(out)


def vec(items: list[bytes]) -> bytes:
    return leb_u(len(items)) + b"".join(items)


def name_bytes(s: str) -> bytes:
    raw = s.encode("ascii")
    return leb_u(len(raw)) + raw


def section(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + leb_u(len(payload)) + payload


# ============================================================================
# 7. 코드 생성 (implementation.md §5)
# ============================================================================


class Body:
    """한 함수의 명령 버퍼."""

    def __init__(self):
        self.buf = bytearray()
        self.ctrl: list[str] = []

    def op(self, *bs: int):
        self.buf.extend(bs)

    def i32const(self, v: int):
        self.buf.append(OP_I32_CONST)
        self.buf.extend(leb_s(to_signed(v)))

    def idx(self, opcode: int, i: int):
        self.buf.append(opcode)
        self.buf.extend(leb_u(i))

    def mem(self, opcode: int, align_log2: int):
        self.buf.append(opcode)
        self.buf.extend(leb_u(align_log2))
        self.buf.extend(leb_u(0))  # offset 은 항상 0. 주소는 명시적으로 더한다

    def push(self, kind: str):
        self.ctrl.append(kind)

    def pop(self):
        self.ctrl.pop()

    def depth_to(self, kind: str) -> int:
        for k in range(len(self.ctrl) - 1, -1, -1):
            if self.ctrl[k] == kind:
                return len(self.ctrl) - 1 - k
        raise AssertionError("no enclosing " + kind)


class Emitter:
    def __init__(self, ck: Checker):
        self.ck = ck
        self.types: list[tuple] = []  # 서명 목록 (첫 등장 순)
        self.type_index: dict[tuple, int] = {}
        self.b: Optional[Body] = None
        self.fb: Optional[FnBody] = None
        self.ntemp = 0
        self.free_temps: list[int] = []

    # --- 서명 -------------------------------------------------------------

    def flat_params(self, info: FnInfo) -> list:
        out = []
        for _, t in info.params:
            out.extend([TYPE_I32] * slot_count(t))
        return out

    def sig_of(self, info: FnInfo) -> tuple:
        results = () if info.ret is VOID else (TYPE_I32,)
        return (tuple(self.flat_params(info)), results)

    def type_idx(self, sig: tuple) -> int:
        if sig not in self.type_index:
            self.type_index[sig] = len(self.types)
            self.types.append(sig)
        return self.type_index[sig]

    # --- 임시 지역변수 -----------------------------------------------------

    def temp(self) -> int:
        if self.free_temps:
            return self.free_temps.pop()
        i = self.fb.nparam_slots + self.fb.nlocal_slots + self.ntemp
        self.ntemp += 1
        return i

    def release(self, *ts: int):
        for t in sorted(ts, reverse=True):
            self.free_temps.append(t)

    # --- 모듈 -------------------------------------------------------------

    def emit_module(self) -> bytes:
        ck = self.ck
        for info in ck.fn_order:
            self.type_idx(self.sig_of(info))

        codes = []
        for info in ck.fn_order:
            codes.append(self.emit_fn(info))

        type_sec = vec(
            [
                bytes([0x60]) + vec([bytes([t]) for t in p]) + vec([bytes([t]) for t in r])
                for p, r in self.types
            ]
        )
        func_sec = vec([leb_u(self.type_idx(self.sig_of(f))) for f in ck.fn_order])
        mem_sec = vec([bytes([0x01]) + leb_u(MEM_PAGES) + leb_u(MEM_PAGES)])
        global_sec = vec(
            [bytes([TYPE_I32, 0x01, OP_I32_CONST]) + leb_s(SHADOW_TOP) + bytes([OP_END])]
        )
        exports = [name_bytes(f.name) + bytes([0x00]) + leb_u(f.index) for f in ck.fn_order]
        exports.append(name_bytes("memory") + bytes([0x02]) + leb_u(0))
        export_sec = vec(exports)
        code_sec = vec(codes)
        data = [
            bytes([0x00, OP_I32_CONST]) + leb_s(addr) + bytes([OP_END]) + leb_u(len(raw)) + raw
            for raw, addr in ck.strings.items()
        ]

        out = bytearray(b"\x00asm\x01\x00\x00\x00")
        if self.types:
            out += section(SEC_TYPE, type_sec)
        if ck.fn_order:
            out += section(SEC_FUNC, func_sec)
        out += section(SEC_MEM, mem_sec)
        out += section(SEC_GLOBAL, global_sec)
        out += section(SEC_EXPORT, export_sec)
        if ck.fn_order:
            out += section(SEC_CODE, code_sec)
        if data:
            out += section(SEC_DATA, vec(data))
        return bytes(out)

    # --- 함수 -------------------------------------------------------------

    def assign_storage(self, fb: FnBody):
        ck = self.ck
        # 매개변수는 언제나 wasm 매개변수를 차지한다
        slot = 0
        for loc in fb.locals:
            if not loc.is_param:
                continue
            loc.slot = slot
            slot += slot_count(loc.ty)
        fb.nparam_slots = slot
        for loc in fb.locals:
            if loc.is_param:
                continue
            if is_aggregate(loc.ty) or loc.addr_taken:
                continue
            loc.slot = slot
            slot += slot_count(loc.ty)
        fb.nlocal_slots = slot - fb.nparam_slots

        # 섀도 프레임: 선언 순서대로, 각자 자기 정렬로
        off, align = 0, 4
        for loc in fb.locals:
            if not (is_aggregate(loc.ty) or loc.addr_taken):
                continue
            a = ck.align_of(loc.ty)
            off = align_up(off, a)
            loc.frame_off = off
            off += ck.size_of(loc.ty)
            align = max(align, a)
        fb.frame_size = align_up(off, align) if off else 0

    def emit_fn(self, info: FnInfo) -> bytes:
        fb = self.ck.bodies[info.name]
        self.fb = fb
        self.assign_storage(fb)
        self.b = Body()
        self.ntemp = 0
        self.free_temps = []

        if fb.frame_size:
            self.b.idx(OP_GLOBAL_GET, 0)
            self.b.i32const(fb.frame_size)
            self.b.op(OP_I32_SUB)
            self.b.idx(OP_GLOBAL_SET, 0)
            self.b.idx(OP_GLOBAL_GET, 0)
            self.b.i32const(SHADOW_FLOOR)
            self.b.op(CMP_OPCODE[("<", False)])
            self.b.op(OP_IF, BLOCK_VOID)
            self.b.op(OP_UNREACHABLE)
            self.b.op(OP_END)
        # 주소를 가진 매개변수는 프레임으로 복사한다
        for loc in fb.locals:
            if loc.is_param and loc.in_memory:
                self.frame_addr(loc)
                if isinstance(loc.ty, Slice):
                    ta = self.temp()
                    self.b.idx(OP_LOCAL_SET, ta)
                    self.b.idx(OP_LOCAL_GET, ta)
                    self.b.idx(OP_LOCAL_GET, loc.slot)
                    self.b.mem(OP_I32_STORE, 2)
                    self.b.idx(OP_LOCAL_GET, ta)
                    self.b.i32const(4)
                    self.b.op(OP_I32_ADD)
                    self.b.idx(OP_LOCAL_GET, loc.slot + 1)
                    self.b.mem(OP_I32_STORE, 2)
                    self.release(ta)
                else:
                    self.b.idx(OP_LOCAL_GET, loc.slot)
                    self.store_op(loc.ty)

        self.emit_block(info.decl.body)
        self.emit_epilogue()
        if info.ret is not VOID:
            self.b.op(OP_UNREACHABLE)
        self.b.op(OP_END)

        nlocals = fb.nlocal_slots + self.ntemp
        decls = vec([leb_u(nlocals) + bytes([TYPE_I32])] if nlocals else [])
        body = decls + bytes(self.b.buf)
        return leb_u(len(body)) + body

    def emit_epilogue(self):
        if self.fb.frame_size:
            self.b.idx(OP_GLOBAL_GET, 0)
            self.b.i32const(self.fb.frame_size)
            self.b.op(OP_I32_ADD)
            self.b.idx(OP_GLOBAL_SET, 0)

    # --- 메모리 접근 -------------------------------------------------------

    def load_op(self, t: Ty):
        if t in (BOOL, U8):
            self.b.mem(OP_I32_LOAD8_U, 0)
        else:
            self.b.mem(OP_I32_LOAD, 2)

    def store_op(self, t: Ty):
        if t in (BOOL, U8):
            self.b.mem(OP_I32_STORE8, 0)
        else:
            self.b.mem(OP_I32_STORE, 2)

    def frame_addr(self, loc: Local):
        self.b.idx(OP_GLOBAL_GET, 0)
        self.b.i32const(loc.frame_off)
        self.b.op(OP_I32_ADD)

    def emit_addr(self, e: Node):
        """장소의 주소를 스택에 남긴다."""
        if isinstance(e, Ident):
            self.frame_addr(e.local)
            return
        if isinstance(e, Field):
            self.emit_addr(e.base)
            self.b.i32const(e.field_info.off)
            self.b.op(OP_I32_ADD)
            return
        if isinstance(e, Deref):
            self.emit_expr(e.base)
            return
        if isinstance(e, Index):
            tp, tl, ti = self.temp(), self.temp(), self.temp()
            self.emit_expr(e.base)  # ptr, len
            self.b.idx(OP_LOCAL_SET, tl)
            self.b.idx(OP_LOCAL_SET, tp)
            self.emit_expr(e.index)
            self.b.idx(OP_LOCAL_SET, ti)
            self.b.idx(OP_LOCAL_GET, ti)  # 경계 검사 (language.md §5)
            self.b.idx(OP_LOCAL_GET, tl)
            self.b.op(CMP_OPCODE[(">=", False)])
            self.b.op(OP_IF, BLOCK_VOID)
            self.b.op(OP_UNREACHABLE)
            self.b.op(OP_END)
            self.b.idx(OP_LOCAL_GET, tp)
            self.b.idx(OP_LOCAL_GET, ti)
            self.b.i32const(e.elem_size)
            self.b.op(OP_I32_MUL)
            self.b.op(OP_I32_ADD)
            self.release(tp, tl, ti)
            return
        raise AssertionError(type(e))

    def in_wasm_local(self, e: Node) -> bool:
        return isinstance(e, Ident) and getattr(e, "local", None) is not None and not e.local.in_memory

    def emit_place_load(self, e: Node, place_ty: Ty):
        if self.in_wasm_local(e):
            loc = e.local
            self.b.idx(OP_LOCAL_GET, loc.slot)
            if isinstance(loc.ty, Slice):
                self.b.idx(OP_LOCAL_GET, loc.slot + 1)
            return
        if isinstance(place_ty, Slice):
            ta = self.temp()
            self.emit_addr(e)
            self.b.idx(OP_LOCAL_SET, ta)
            self.b.idx(OP_LOCAL_GET, ta)
            self.b.mem(OP_I32_LOAD, 2)
            self.b.idx(OP_LOCAL_GET, ta)
            self.b.i32const(4)
            self.b.op(OP_I32_ADD)
            self.b.mem(OP_I32_LOAD, 2)
            self.release(ta)
            return
        self.emit_addr(e)
        self.load_op(place_ty)

    def place_ty(self, e: Node) -> Ty:
        if isinstance(e, Ident):
            return e.local.ty
        if isinstance(e, Field):
            return e.field_info.ty
        if isinstance(e, Index):
            return e.base.ty.elem
        if isinstance(e, Deref):
            return e.base.ty.inner
        raise AssertionError(type(e))

    # --- 식 ---------------------------------------------------------------

    def emit_expr(self, e: Node):
        if isinstance(e, Int):
            self.b.i32const(e.value)
            return
        if isinstance(e, Char):
            self.b.i32const(e.value)
            return
        if isinstance(e, Bool):
            self.b.i32const(1 if e.value else 0)
            return
        if isinstance(e, Str):
            self.b.i32const(e.addr)
            self.b.i32const(len(e.value))
            return
        if isinstance(e, SliceExpr):
            self.emit_expr(e.ptr)
            self.emit_expr(e.length)
            return
        if isinstance(e, OffsetExpr):
            self.emit_expr(e.ptr)
            self.emit_expr(e.index)
            self.b.i32const(e.stride)
            self.b.op(OP_I32_MUL)
            self.b.op(OP_I32_ADD)
            return
        if isinstance(e, Ident):
            if getattr(e, "local", None) is not None:
                self.emit_place_load(e, e.local.ty)
            else:
                self.b.i32const(e.const_value)
            return
        if isinstance(e, Cast):
            self.emit_expr(e.operand)  # 비트 재해석. 명령 없음
            return
        if isinstance(e, Unary):
            if e.op == "!":
                self.emit_expr(e.operand)
                self.b.op(OP_I32_EQZ)
            else:
                self.b.i32const(0)
                self.emit_expr(e.operand)
                self.b.op(OP_I32_SUB)
            return
        if isinstance(e, Binary):
            self.emit_binary(e)
            return
        if isinstance(e, Call):
            for a in e.args:
                if isinstance(a, Borrow):
                    self.emit_addr(a.operand)
                else:
                    self.emit_expr(a)
            self.b.idx(OP_CALL, e.fn_info.index)
            return
        if isinstance(e, Field) and getattr(e, "slice_field", None) is not None:
            self.emit_expr(e.base)
            if e.slice_field == "ptr":
                self.b.op(OP_DROP)
                return
            # 슬라이스의 .len
            t = self.temp()
            self.b.idx(OP_LOCAL_SET, t)
            self.b.op(OP_DROP)
            self.b.idx(OP_LOCAL_GET, t)
            self.release(t)
            return
        if isinstance(e, (Field, Index, Deref)):
            self.emit_place_load(e, self.place_ty(e))
            return
        raise AssertionError(type(e))

    def emit_binary(self, e: Binary):
        if e.op in ("&&", "||"):
            self.emit_expr(e.lhs)
            self.b.op(OP_IF, TYPE_I32)
            self.b.push("if")
            if e.op == "&&":
                self.emit_expr(e.rhs)
                self.b.op(OP_ELSE)
                self.b.i32const(0)
            else:
                self.b.i32const(1)
                self.b.op(OP_ELSE)
                self.emit_expr(e.rhs)
            self.b.pop()
            self.b.op(OP_END)
            return
        self.emit_expr(e.lhs)
        self.emit_expr(e.rhs)
        if e.op in CMP_OPS:
            # 비교는 피연산자 타입이, 나머지는 결과 타입이 부호를 정한다.
            # 산술과 시프트는 결과 타입이 곧 왼쪽 피연산자 타입이다
            self.b.op(CMP_OPCODE[(e.op, e.opnd_ty is I32)])
        else:
            self.b.op(ARITH_OPCODE[(e.op, e.ty is I32)])

    # --- 대입 -------------------------------------------------------------

    def emit_store_to(self, place: Node, emit_value):
        ty = self.place_ty(place)
        if self.in_wasm_local(place):
            loc = place.local
            emit_value()
            if isinstance(loc.ty, Slice):
                self.b.idx(OP_LOCAL_SET, loc.slot + 1)
                self.b.idx(OP_LOCAL_SET, loc.slot)
            else:
                self.b.idx(OP_LOCAL_SET, loc.slot)
            return
        ta = self.temp()
        self.emit_addr(place)
        self.b.idx(OP_LOCAL_SET, ta)
        if isinstance(ty, Slice):
            tp, tl = self.temp(), self.temp()
            emit_value()
            self.b.idx(OP_LOCAL_SET, tl)
            self.b.idx(OP_LOCAL_SET, tp)
            self.b.idx(OP_LOCAL_GET, ta)
            self.b.idx(OP_LOCAL_GET, tp)
            self.b.mem(OP_I32_STORE, 2)
            self.b.idx(OP_LOCAL_GET, ta)
            self.b.i32const(4)
            self.b.op(OP_I32_ADD)
            self.b.idx(OP_LOCAL_GET, tl)
            self.b.mem(OP_I32_STORE, 2)
            self.release(tp, tl)
        else:
            self.b.idx(OP_LOCAL_GET, ta)
            emit_value()
            self.store_op(ty)
        self.release(ta)

    def emit_load_at(self, addr_temp: int, off: int, ty: Ty):
        """addr_temp + off 에서 값 하나를 읽어 스택에 올린다. 슬라이스는 둘이다."""
        if isinstance(ty, Slice):
            for delta in (0, 4):
                self.b.idx(OP_LOCAL_GET, addr_temp)
                self.b.i32const(off + delta)
                self.b.op(OP_I32_ADD)
                self.b.mem(OP_I32_LOAD, 2)
            return
        self.b.idx(OP_LOCAL_GET, addr_temp)
        self.b.i32const(off)
        self.b.op(OP_I32_ADD)
        self.load_op(ty)

    def emit_field_store(self, addr_temp: int, off: int, ty: Ty, value: Node):
        """addr_temp + off 에 값 하나를 쓴다. 슬라이스는 두 조각이다 (implementation.md §2)."""
        if isinstance(ty, Slice):
            tp, tl = self.temp(), self.temp()
            self.emit_expr(value)  # ptr, len
            self.b.idx(OP_LOCAL_SET, tl)
            self.b.idx(OP_LOCAL_SET, tp)
            self.b.idx(OP_LOCAL_GET, addr_temp)
            self.b.i32const(off)
            self.b.op(OP_I32_ADD)
            self.b.idx(OP_LOCAL_GET, tp)
            self.b.mem(OP_I32_STORE, 2)
            self.b.idx(OP_LOCAL_GET, addr_temp)
            self.b.i32const(off + 4)
            self.b.op(OP_I32_ADD)
            self.b.idx(OP_LOCAL_GET, tl)
            self.b.mem(OP_I32_STORE, 2)
            self.release(tp, tl)
            return
        self.b.idx(OP_LOCAL_GET, addr_temp)
        self.b.i32const(off)
        self.b.op(OP_I32_ADD)
        self.emit_expr(value)
        self.store_op(ty)

    def emit_agg_init(self, addr_temp: int, init: Node):
        """집합체 리터럴을 addr_temp 가 가리키는 곳에 직접 쓴다. 복사가 없다."""
        if isinstance(init, StructLit):
            for (_, _, value), f in zip(init.fields, init.struct_info.fields):
                self.emit_field_store(addr_temp, f.off, f.ty, value)
            return
        v = init.variant_info
        self.b.idx(OP_LOCAL_GET, addr_temp)
        self.b.i32const(0)
        self.b.op(OP_I32_ADD)
        self.b.i32const(v.tag)
        self.b.mem(OP_I32_STORE, 2)
        for value, f in zip(init.enum_args, v.payload):
            self.emit_field_store(addr_temp, f.off, f.ty, value)

    def is_agg_lit(self, e: Node) -> bool:
        return isinstance(e, StructLit) or getattr(e, "variant_info", None) is not None

    # --- 문 ---------------------------------------------------------------

    def emit_block(self, stmts: list):
        for s in stmts:
            self.emit_stmt(s)

    def emit_stmt(self, s: Node):
        b = self.b

        if isinstance(s, Let):
            loc = s.local
            if self.is_agg_lit(s.init):
                ta = self.temp()
                self.frame_addr(loc)
                b.idx(OP_LOCAL_SET, ta)
                self.emit_agg_init(ta, s.init)
                self.release(ta)
            else:
                self.emit_store_to(local_stub(loc, s.pos), lambda: self.emit_expr(s.init))
            return

        if isinstance(s, Assign):
            if s.op == "=":
                if self.is_agg_lit(s.value):
                    ta = self.temp()
                    self.emit_addr(s.target)
                    b.idx(OP_LOCAL_SET, ta)
                    self.emit_agg_init(ta, s.value)
                    self.release(ta)
                else:
                    self.emit_store_to(s.target, lambda: self.emit_expr(s.value))
                return
            op = s.op[:-1]
            ty = self.place_ty(s.target)
            signed = read_ty(ty) is I32
            if self.in_wasm_local(s.target):
                loc = s.target.local
                b.idx(OP_LOCAL_GET, loc.slot)
                self.emit_expr(s.value)
                b.op(ARITH_OPCODE[(op, signed)])
                b.idx(OP_LOCAL_SET, loc.slot)
                return
            ta = self.temp()
            self.emit_addr(s.target)
            b.idx(OP_LOCAL_SET, ta)
            b.idx(OP_LOCAL_GET, ta)
            b.idx(OP_LOCAL_GET, ta)
            self.load_op(ty)
            self.emit_expr(s.value)
            b.op(ARITH_OPCODE[(op, signed)])
            self.store_op(ty)
            self.release(ta)
            return

        if isinstance(s, ExprStmt):
            self.emit_expr(s.expr)
            if s.discard:
                for _ in range(slot_count(s.expr.ty)):
                    b.op(OP_DROP)
            return

        if isinstance(s, If):
            self.emit_expr(s.cond)
            b.op(OP_IF, BLOCK_VOID)
            b.push("if")
            self.emit_block(s.then)
            if s.els is not None:
                b.op(OP_ELSE)
                self.emit_block(s.els)
            b.pop()
            b.op(OP_END)
            return

        if isinstance(s, For):
            if s.init is not None:
                self.emit_stmt(s.init)
            b.op(OP_BLOCK, BLOCK_VOID)
            b.push("brk")
            b.op(OP_LOOP, BLOCK_VOID)
            b.push("loop")
            if s.cond is not None:
                self.emit_expr(s.cond)
                b.op(OP_I32_EQZ)
                b.idx(OP_BR_IF, b.depth_to("brk"))
            b.op(OP_BLOCK, BLOCK_VOID)
            b.push("cnt")
            self.emit_block(s.body)
            b.pop()
            b.op(OP_END)
            if s.post is not None:
                self.emit_stmt(s.post)
            b.idx(OP_BR, b.depth_to("loop"))
            b.pop()
            b.op(OP_END)
            b.pop()
            b.op(OP_END)
            return

        if isinstance(s, Break):
            b.idx(OP_BR, b.depth_to("brk"))
            return

        if isinstance(s, Continue):
            b.idx(OP_BR, b.depth_to("cnt"))
            return

        if isinstance(s, Return):
            if s.value is not None:
                self.emit_expr(s.value)
            self.emit_epilogue()
            b.op(OP_RETURN)
            return

        if isinstance(s, Match):
            self.emit_match(s)
            return

        if isinstance(s, Unsafe):
            self.emit_block(s.body)
            return

        raise AssertionError(type(s))

    def emit_match(self, s: Match):
        b = self.b
        ta = self.temp()
        self.emit_addr(s.scrutinee)
        b.idx(OP_LOCAL_SET, ta)
        b.op(OP_BLOCK, BLOCK_VOID)
        b.push("match")
        for arm in s.arms:
            if arm.variant is None:
                self.emit_block(arm.body)
                continue
            v = arm.variant_info
            b.idx(OP_LOCAL_GET, ta)
            b.i32const(0)
            b.op(OP_I32_ADD)
            b.mem(OP_I32_LOAD, 2)
            b.i32const(v.tag)
            b.op(CMP_OPCODE[("==", False)])
            b.op(OP_IF, BLOCK_VOID)
            b.push("if")
            for loc, f in zip(getattr(arm, "bind_locals", []), v.payload):
                self.emit_store_to(
                    local_stub(loc, arm.pos),
                    lambda ta=ta, f=f: self.emit_load_at(ta, f.off, f.ty),
                )
            self.emit_block(arm.body)
            b.idx(OP_BR, b.depth_to("match"))
            b.pop()
            b.op(OP_END)
        if not s.has_wild:
            b.op(OP_UNREACHABLE)
        b.pop()
        b.op(OP_END)
        self.release(ta)


def local_stub(loc: Local, pos) -> Ident:
    """지역변수를 장소 식처럼 다루기 위한 작은 어댑터.

    `let` 의 대상과 `match` 의 바인딩은 AST 에 장소 노드가 없다. 그래도 저장 경로는
    하나여야 한다 -- 슬라이스인지, 프레임에 있는지를 두 군데서 따로 다루면 틀린다.
    """
    stub = Ident(pos, loc.name)
    stub.local = loc
    stub.ty = read_ty(loc.ty)
    return stub


# ============================================================================
# 8. 부트스트랩 대상의 메모리 한계 (implementation.md §7)
# ============================================================================
#
# 자기 호스팅 컴파일러는 32 MiB 안에서 산다. 아레나는 소스 뒤에서 위로 자라고
# 함수 본문 스크래치 S1 은 0x0080_0000 에 고정돼 있으므로, 힙이 거기 닿으면 두
# 영역이 겹친다.
#
# 그 한계는 언어의 것이 아니라 **대상의 것**이다. 그래도 여기서 같이 지킨다 --
# 안 그러면 큰 소스에서 두 구현이 갈리고, 그것이 implementation.md §8 이 금지하는
# 바로 그 상황이다. 산술은 cool0c.cool0 의 compile() 과 한 줄씩 같다.

BOOTSTRAP_SCRATCH = 0x0080_0000  # S1. 힙은 여기 닿을 수 없다


def _count_arena_nodes(decls) -> dict:
    """cool0c 의 count_nodes 가 세는 것과 같은 것을 센다."""
    from dataclasses import fields as dc_fields, is_dataclass

    n = dict.fromkeys(
        "tys fields structs variants enums consts params fns lets binds strs".split(), 0
    )
    seen = set()

    def walk(x):
        if isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
            return
        if not is_dataclass(x) or id(x) in seen:
            return
        seen.add(id(x))
        if isinstance(x, TyNode):
            n["tys"] += 1
        elif isinstance(x, StructDecl):
            n["structs"] += 1
            n["fields"] += len(x.fields)
        elif isinstance(x, EnumDecl):
            n["enums"] += 1
            n["variants"] += len(x.variants)
        elif isinstance(x, ConstDecl):
            n["consts"] += 1
        elif isinstance(x, FnDecl):
            n["fns"] += 1
            n["params"] += len(x.params)
        elif isinstance(x, Let):
            n["lets"] += 1
        elif isinstance(x, Arm):
            n["binds"] += len(x.binds)
        elif isinstance(x, Str):
            n["strs"] += 1
        for f in dc_fields(x):
            walk(getattr(x, f.name))

    walk(decls)
    return n


def check_bootstrap_memory(src_len: int, ntok: Optional[int], decls) -> None:
    """cool0c 의 범프 순서를 그대로 따라가 힙 끝을 구하고 S1 과 견준다.

    `ntok` 이 None 이면 토큰 아레나까지만 본다 -- 렉싱 전에 한 번, 파싱 뒤에 한 번,
    자기 호스팅 컴파일러가 검사하는 바로 그 두 지점이다.
    """
    heap = align_up(SRC_ADDR + src_len, 4) + 4
    heap += (src_len + 1) * 28  # 토큰
    if ntok is not None:
        k = _count_arena_nodes(decls)
        n_local = k["params"] + k["lets"] + k["binds"] + 2
        n_name = k["structs"] + k["enums"] + k["consts"] + k["fns"] + 2
        heap += (ntok + 2) * 52  # 노드
        heap += (k["tys"] + 7 + 8) * 12  # 타입
        heap += (k["fields"] + k["tys"] + 2) * 20
        heap += (k["structs"] + 2) * 24
        heap += (k["variants"] + 2) * 24
        heap += (k["enums"] + 2) * 28
        heap += (k["consts"] + 2) * 28
        heap += (k["params"] + 2) * 16
        heap += (k["fns"] + 2) * 52
        heap += n_local * 36
        heap += (k["strs"] + 2) * 16
        heap += n_name * 12 * 2
        heap += (n_local + 2) * 4  # 스코프
        heap += (MAX_DEPTH + MAX_DEPTH + 8) * 4  # 표시
        heap += 512 * 4 * 2  # free, ctrl
        heap += ((k["fns"] + 2) * 3) * 4  # 시그니처
    if heap > BOOTSTRAP_SCRATCH:
        raise CompileError(1, 1, "program is too large for the compiler's memory")


# ============================================================================
# 9. 진입점
# ============================================================================


def compile(src: bytes) -> tuple[int, bytes]:
    """cool0 소스를 wasm 으로. 순수 함수다 (implementation.md §7).

    무슨 바이트가 들어와도 `(0, wasm)` 아니면 `(1, 진단)` 이다. 예외는 새지 않는다.
    """
    import sys

    # 참조 구현의 사정이다. 언어의 한계는 MAX_DEPTH 이고, 파이썬의 재귀 한계가
    # 그보다 먼저 걸리면 안 된다. WAT 판은 섀도 스택이 같은 일을 한다
    saved = sys.getrecursionlimit()
    sys.setrecursionlimit(max(saved, 8000))
    try:
        check_bootstrap_memory(len(src), None, None)
        toks = lex(src)
        decls = Parser(toks).parse_program()
        check_bootstrap_memory(len(src), len(toks), decls)
        ck = Checker(decls).run()
        return STATUS_OK, Emitter(ck).emit_module()
    except CompileError as e:
        return STATUS_ERR, e.render()
    finally:
        sys.setrecursionlimit(saved)


def compile_text(src: str) -> tuple[int, bytes]:
    """편의 함수. 테스트에서 쓴다."""
    return compile(src.encode("ascii"))


if __name__ == "__main__":  # 호스트. 컴파일러의 일부가 아니다
    import sys

    if len(sys.argv) != 3:
        print("usage: cool0.py <in.cool0> <out.wasm>", file=sys.stderr)
        raise SystemExit(2)
    with open(sys.argv[1], "rb") as f:
        status, out = compile(f.read())
    if status != STATUS_OK:
        sys.stderr.write(out.decode("ascii"))
        raise SystemExit(1)
    with open(sys.argv[2], "wb") as f:
        f.write(out)
