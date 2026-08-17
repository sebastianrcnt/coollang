"""퍼징. README 의 세 층.

    1. 랜덤 바이트            렉서와 오류 경로. 크래시하지 않는지
    2. 문법 기반 랜덤 프로그램  파서·타입 검사·코드 생성
    3. 의미가 있는 랜덤 프로그램 wasmtime 으로 돌려 결과까지 비교

3층의 오라클은 아직 cool0c 가 없으므로 파이썬으로 쓴 참조 평가기다. cool0c.wasm 이
생기면 tests/test_parity.py 가 그 자리를 이어받는다.

씨앗은 고정이다. 퍼저가 결정적이지 않으면 실패를 재현할 수 없다.
"""

from __future__ import annotations

import random

import pytest
import wasmtime

from conftest import ENGINE, compile_ok
from cool0.cool0 import STATUS_ERR, STATUS_OK, CompileError, compile as cool0_compile

ROUNDS = 400
U32 = 0xFFFFFFFF


def check_total(src: bytes):
    """무슨 입력이든 (0, wasm) 아니면 (1, 진단) 이어야 한다. 예외는 없다."""
    try:
        status, out = cool0_compile(src)
    except CompileError as e:  # 잡히지 않고 새어 나온 진단
        pytest.fail(f"CompileError escaped compile(): {e} on {src!r}")
    except RecursionError:
        pytest.fail(f"RecursionError on {src!r}")
    except Exception as e:  # noqa: BLE001 -- 그게 요점이다
        pytest.fail(f"{type(e).__name__}: {e} on {src!r}")
    assert status in (STATUS_OK, STATUS_ERR)
    assert isinstance(out, (bytes, bytearray))
    if status == STATUS_ERR:
        assert out.endswith(b"\n")
        assert out.count(b"\n") == 1
        out.decode("ascii")  # 진단은 ASCII 다 (implementation.md §7)
    else:
        wasmtime.Module(ENGINE, bytes(out))  # 성공했으면 검증을 통과해야 한다
    return status, out


# --- 1층: 랜덤 바이트 ------------------------------------------------------


def test_random_bytes_never_crash():
    rng = random.Random(0xC001)
    for _ in range(ROUNDS):
        n = rng.randrange(0, 64)
        check_total(bytes(rng.randrange(0, 256) for _ in range(n)))


def test_random_ascii_never_crashes():
    rng = random.Random(0xC002)
    alphabet = b"abcXYZ_019 \t\n(){}[],;:.=<>+-*/%&|^!'\"\\#@"
    for _ in range(ROUNDS):
        n = rng.randrange(0, 80)
        check_total(bytes(rng.choice(alphabet) for _ in range(n)))


def test_random_token_soup_never_crashes():
    """어휘 분석은 통과하되 문법은 엉망인 것들."""
    rng = random.Random(0xC003)
    toks = """fn struct enum const let mut if else for break continue return match
              unsafe as true false i32 u32 bool u8 x y z 0 1 0xFF ( ) { } [ ] , ; :
              . = == != < > <= >= + - * / % & | ^ ! && || << >> -> => "s" 'c'""".split()
    for _ in range(ROUNDS):
        n = rng.randrange(0, 40)
        check_total(" ".join(rng.choice(toks) for _ in range(n)).encode("ascii"))


def test_truncations_of_a_valid_program_never_crash():
    """유효한 프로그램의 모든 접두사. 조기 종료 경로를 훑는다."""
    src = (
        b"struct P { x: i32, y: u8 }\n"
        b"enum E { A, B(i32) }\n"
        b"const K: u32 = 3;\n"
        b'fn f(s: []mut u8) -> u32 {\n'
        b'  let mut p: P = P{ x: 1, y: 2 };\n'
        b"  for let mut i: u32 = 0; i < K; i += 1 { s[i] = 65; }\n"
        b"  let e: E = E.B(7);\n"
        b"  match e { A => { } B(n) => { p.x = n; } }\n"
        b"  unsafe { let q: *u32 = 16 as *u32; q.^ = 1; }\n"
        b"  return s.len;\n"
        b"}\n"
    )
    for i in range(len(src) + 1):
        check_total(src[:i])


def test_single_byte_mutations_never_crash():
    rng = random.Random(0xC004)
    src = bytearray(b"fn f(a: i32) -> i32 { let mut x: i32 = a * 2; return x; }")
    for _ in range(ROUNDS):
        m = bytearray(src)
        m[rng.randrange(len(m))] = rng.randrange(0, 256)
        check_total(bytes(m))


# --- 2층: 문법 기반 랜덤 프로그램 -------------------------------------------


class ProgramGen:
    """타입이 맞는 cool0 프로그램을 만든다. 전부 컴파일에 성공해야 한다."""

    def __init__(self, rng: random.Random):
        self.rng = rng

    def choice(self, xs):
        return self.rng.choice(xs)

    def expr(self, ty: str, vars: dict, depth: int) -> str:
        r = self.rng
        pool = [v for v, t in vars.items() if t == ty]
        if depth <= 0 or r.random() < 0.25:
            if pool and r.random() < 0.6:
                return self.choice(pool)
            if ty == "bool":
                return self.choice(["true", "false"])
            return str(r.randrange(0, 1 << 16))
        if ty == "bool":
            kind = self.choice(["cmp", "logic", "not"])
            if kind == "not":
                return "(!" + self.expr("bool", vars, depth - 1) + ")"
            if kind == "logic":
                op = self.choice(["&&", "||"])
                return (
                    "(" + self.expr("bool", vars, depth - 1) + " " + op + " "
                    + self.expr("bool", vars, depth - 1) + ")"
                )
            it = self.choice(["i32", "u32"])
            op = self.choice(["==", "!=", "<", ">", "<=", ">="])
            return (
                "(" + self.expr(it, vars, depth - 1) + " " + op + " "
                + self.expr(it, vars, depth - 1) + ")"
            )
        op = self.choice(["+", "-", "*", "&", "|", "^"])
        return (
            "(" + self.expr(ty, vars, depth - 1) + " " + op + " "
            + self.expr(ty, vars, depth - 1) + ")"
        )

    def block(self, vars: dict, depth: int, in_loop: bool, mut: set) -> list[str]:
        r = self.rng
        out = []
        for _ in range(r.randrange(0, 4)):
            kind = self.choice(["let", "assign", "if", "for", "call"] + (["break"] if in_loop else []))
            if kind == "let":
                ty = self.choice(["i32", "u32", "bool"])
                name = "v%d" % len(vars)
                out.append(f"let mut {name}: {ty} = {self.expr(ty, vars, depth)};")
                vars = dict(vars, **{name: ty})
                mut = mut | {name}
            elif kind == "assign":
                pool = sorted(mut)  # 매개변수는 불변이다 (language.md §6)
                if not pool:
                    continue
                name = self.choice(pool)
                out.append(f"{name} = {self.expr(vars[name], vars, depth)};")
            elif kind == "if":
                cond = self.expr("bool", vars, depth)
                then = self.block(vars, depth - 1, in_loop, mut) if depth > 0 else []
                out.append("if " + cond + " { " + " ".join(then) + " }")
            elif kind == "for" and depth > 0:
                body = self.block(vars, depth - 1, True, mut)
                out.append(
                    "for let mut i: u32 = 0; i < 3; i += 1 { " + " ".join(body) + " }"
                )
            elif kind == "break":
                out.append("break;")
            elif kind == "call":
                out.append("noop();")
        return out

    def program(self) -> str:
        vars: dict = {"a": "i32", "b": "u32", "c": "bool"}
        body = self.block(vars, 3, False, set())
        return (
            "fn noop() { }\n"
            "fn f(a: i32, b: u32, c: bool) -> i32 {\n  "
            + "\n  ".join(body)
            + "\n  return a;\n}\n"
        )


def test_generated_programs_all_compile_and_validate():
    rng = random.Random(0xBEEF)
    gen = ProgramGen(rng)
    for _ in range(200):
        src = gen.program()
        status, out = cool0_compile(src.encode("ascii"))
        if status != STATUS_OK:
            pytest.fail("generated program failed: " + out.decode() + "\n" + src)
        wasmtime.Module(ENGINE, out)


def test_generated_programs_are_deterministic():
    gen = ProgramGen(random.Random(0xF00D))
    for _ in range(50):
        src = gen.program().encode("ascii")
        assert cool0_compile(src) == cool0_compile(src)


# --- 3층: 의미가 있는 랜덤 프로그램 ------------------------------------------


class ExprGen:
    """i32 식 하나를 만들고, 같은 식을 파이썬으로도 평가한다.

    트랩이 나는 자리(0 나눗셈 등)는 피한다 -- 여기서 보는 것은 값이지 트랩이 아니다.
    """

    OPS = ["+", "-", "*", "&", "|", "^"]

    def __init__(self, rng: random.Random):
        self.rng = rng

    def make(self, depth: int, env: dict) -> tuple[str, int]:
        r = self.rng
        if depth <= 0:
            if env and r.random() < 0.5:
                name = r.choice(list(env))
                return name, env[name]
            v = r.randrange(0, 1 << 20)
            return str(v), v
        kind = r.random()
        if kind < 0.12:
            s, v = self.make(depth - 1, env)
            return "(0 - " + s + ")", (-v) & U32
        if kind < 0.22:  # 부호 있는 시프트
            s, v = self.make(depth - 1, env)
            n = r.randrange(0, 32)
            sv = v - 0x100000000 if v >= 0x80000000 else v
            return f"({s} >> {n})", (sv >> n) & U32
        if kind < 0.32:
            s, v = self.make(depth - 1, env)
            n = r.randrange(0, 32)
            return f"({s} << {n})", (v << n) & U32
        if kind < 0.42:  # 나누기. 0 이 아닌 상수로만
            s, v = self.make(depth - 1, env)
            d = r.randrange(1, 1000)
            sv = v - 0x100000000 if v >= 0x80000000 else v
            q = abs(sv) // d * (1 if sv >= 0 else -1)
            return f"({s} / {d})", q & U32
        op = r.choice(self.OPS)
        ls, lv = self.make(depth - 1, env)
        rs, rv = self.make(depth - 1, env)
        table = {
            "+": lambda x, y: x + y,
            "-": lambda x, y: x - y,
            "*": lambda x, y: x * y,
            "&": lambda x, y: x & y,
            "|": lambda x, y: x | y,
            "^": lambda x, y: x ^ y,
        }
        return f"({ls} {op} {rs})", table[op](lv, rv) & U32


def test_random_expressions_match_the_reference_evaluator():
    """코드 생성이 옳은지 -- 파이썬 평가기와 wasm 실행 결과를 맞춰 본다."""
    rng = random.Random(0x5EED)
    gen = ExprGen(rng)
    for _ in range(300):
        a = rng.randrange(0, 1 << 31)
        env = {"a": a}
        text, want = gen.make(4, env)
        src = f"fn f(a: i32) -> i32 {{ return {text}; }}"
        wasm = compile_ok(src)
        store = wasmtime.Store(ENGINE)
        inst = wasmtime.Instance(store, wasmtime.Module(ENGINE, wasm), [])
        got = inst.exports(store)["f"](store, a - 0x100000000 if a >= 0x80000000 else a)
        assert got & U32 == want, f"{text}\nwant {want}, got {got & U32}"


def test_random_control_flow_matches_the_reference_evaluator():
    """루프와 분기가 있는 프로그램. 누적값을 파이썬으로도 센다."""
    rng = random.Random(0x1DEA)
    for _ in range(120):
        n = rng.randrange(0, 12)
        m = rng.randrange(1, 6)
        skip = rng.randrange(0, 4)
        src = f"""
fn f(n: u32) -> u32 {{
    let mut acc: u32 = 0;
    for let mut i: u32 = 0; i < n; i += 1 {{
        if i % {m} == {skip} {{ continue; }}
        if i == 9 {{ break; }}
        acc += i * i;
    }}
    return acc;
}}
"""
        want = 0
        for i in range(n):
            if i % m == skip:
                continue
            if i == 9:
                break
            want = (want + i * i) & U32
        assert run_u32(src, "f", n) == want


def run_u32(src: str, name: str, *args) -> int:
    wasm = compile_ok(src)
    store = wasmtime.Store(ENGINE)
    inst = wasmtime.Instance(store, wasmtime.Module(ENGINE, wasm), [])
    return inst.exports(store)[name](store, *args) & U32


# --- 전역성: 어떤 입력에도 재귀로 죽지 않는다 (language.md §6 중첩 한계) ------------------


@pytest.mark.parametrize(
    "src",
    [
        "fn f() -> i32 { return " + "(" * 100000 + "1" + ")" * 100000 + "; }",
        "fn f() { " + "(" * 100000,  # 닫히지 않은 괄호
        "fn f(a: " + "*" * 100000 + "u8) { }",
        "fn f() { if true { } " + "else if true { } " * 5000 + "}",
        "fn f() { " + "g(" * 10000 + ")" * 10000 + "; }",
        "fn f() { let x = " + "a[" * 10000 + "0" + "]" * 10000 + "; }",
        "fn f() { let x = a" + ".b" * 100000 + "; }",
        "fn f() { let x = " + "-" * 100000 + "1; }",
        "fn f() { let x = " + "!" * 100000 + "true; }",
        "fn f() { let x = p" + ".^" * 100000 + "; }",
        "fn f() { let x = 1" + " as i32" * 100000 + "; }",
        "fn f() -> i32 { return " + "1+" * 100000 + "1; }",
        "fn f() { " + "if true { " * 5000 + "}" * 5000 + " }",
        "fn f() { " + "unsafe { " * 5000 + "}" * 5000 + " }",
        "fn f() { " + "for { " * 5000 + "}" * 5000 + " }",
        "fn f() { " + "{" * 100000,
    ],
    ids=range(16),
)
def test_deep_nesting_yields_a_diagnostic_not_a_crash(src):
    status, out = check_total(src.encode("ascii"))
    assert status == STATUS_ERR


def test_the_nesting_limit_is_generous_enough_for_real_code():
    """실제 코드가 걸릴 깊이가 아니다."""
    from conftest import compile_ok

    compile_ok("fn g(x: i32) -> i32 { return x; } fn f() -> i32 { return " + "g(" * 60 + "1" + ")" * 60 + "; }")
    compile_ok("fn f() -> i32 { return " + "(" * 60 + "1" + ")" * 60 + "; }")
    compile_ok("fn f() -> i32 { return " + "1+" * 60 + "1; }")
    compile_ok("fn f() { " + "if true { " * 60 + "}" * 60 + " }")


# --- 집합체까지 덮는 생성기 ---------------------------------------------------
#
# ProgramGen 은 스칼라와 if/for 만 만든다. 그 바깥 -- struct, enum, match, 슬라이스,
# 대여, unsafe -- 이 손으로 쓴 케이스에만 기대고 있었다. cool0.py 의 실제 버그 둘이
# 바로 거기서 나왔으므로, 무작위로도 때려 본다.


class RichGen:
    """cool0 의 거의 모든 문법을 쓰는 프로그램을 만든다.

    타입이 맞는 것을 목표로 하지만 완벽하지 않아도 된다 -- 검사기가 거절하면 그것도
    유효한 패리티 표본이다. 다만 충분히 많이 성공해야 방출 경로를 실제로 때린다.
    """

    FIELD_TYPES = ["i32", "u32", "bool", "u8", "[]u8", "[]mut u8", "*u8"]
    SCALARS = ["i32", "u32", "bool"]

    def __init__(self, rng):
        self.rng = rng

    def pick(self, xs):
        return self.rng.choice(xs)

    # --- 선언 ---------------------------------------------------------------

    def gen_decls(self):
        r = self.rng
        self.structs = {}
        self.enums = {}
        out = []
        for i in range(r.randrange(1, 4)):
            name = "S%d" % i
            n = r.randrange(0, 4)
            fields = [("f%d" % j, self.pick(self.FIELD_TYPES)) for j in range(n)]
            self.structs[name] = fields
            body = ", ".join("%s: %s" % (f, t) for f, t in fields)
            out.append("struct %s { %s }" % (name, body))
        for i in range(r.randrange(1, 3)):
            name = "E%d" % i
            variants = []
            for j in range(r.randrange(1, 4)):
                k = r.randrange(0, 3)
                variants.append(("V%d" % j, [self.pick(self.FIELD_TYPES) for _ in range(k)]))
            self.enums[name] = variants
            body = ", ".join(
                v if not p else "%s(%s)" % (v, ", ".join(p)) for v, p in variants
            )
            out.append("enum %s { %s }" % (name, body))
        out.append("const K0: u32 = 3;")
        out.append("const K1: i32 = -7 / 2;")
        return out

    def helper_fns(self):
        out = []
        for s in self.structs:
            out.append("fn ref_%s(p: &%s) -> u32 { return 1; }" % (s, s))
            out.append("fn mut_%s(p: &mut %s) { }" % (s, s))
        for e in self.enums:
            out.append("fn ref_%s(p: &%s) -> u32 { return 2; }" % (e, e))
            out.append("fn mut_%s(p: &mut %s) { }" % (e, e))
        out.append("fn scal(a: i32, b: u32, c: bool) -> u32 { return b; }")
        out.append("fn sl(s: []u8) -> u32 { return s.len; }")
        out.append("fn slm(s: []mut u8) -> u32 { return s.len; }")
        out.append("fn ptr(p: *u8) -> u32 { return 0; }")
        out.append("fn noop() { }")
        return out

    # --- 식 -----------------------------------------------------------------

    def scalar_expr(self, ty, env, depth):
        """ty 는 i32/u32/bool. env 는 name -> type."""
        r = self.rng
        pool = [v for v, t in env.items() if t == ty]
        # u8 필드/원소를 읽으면 u32 다
        if ty == "u32":
            for v, t in env.items():
                if t in self.structs:
                    pool += ["%s.%s" % (v, f) for f, ft in self.structs[t] if ft == "u8"]
                    pool += ["%s.%s.len" % (v, f) for f, ft in self.structs[t]
                             if ft in ("[]u8", "[]mut u8")]
            pool += [v + ".len" for v, t in env.items() if t in ("[]u8", "[]mut u8")]
            pool += ["%s[0]" % v for v, t in env.items() if t in ("[]u8", "[]mut u8")]
        for v, t in env.items():
            if t in self.structs:
                pool += ["%s.%s" % (v, f) for f, ft in self.structs[t] if ft == ty]
        if depth <= 0 or r.random() < 0.3:
            if pool and r.random() < 0.65:
                return self.pick(pool)
            if ty == "bool":
                return self.pick(["true", "false"])
            if ty == "u32":
                return self.pick([str(r.randrange(0, 1 << 20)), "K0", '"abc".len' ])
            return self.pick([str(r.randrange(0, 1 << 20)), "K1"])
        kind = r.random()
        if ty == "bool":
            if kind < 0.3:
                return "(!%s)" % self.scalar_expr("bool", env, depth - 1)
            if kind < 0.6:
                op = self.pick(["&&", "||"])
                return "(%s %s %s)" % (self.scalar_expr("bool", env, depth - 1), op,
                                       self.scalar_expr("bool", env, depth - 1))
            it = self.pick(["i32", "u32"])
            op = self.pick(["==", "!=", "<", ">", "<=", ">="])
            return "(%s %s %s)" % (self.scalar_expr(it, env, depth - 1), op,
                                   self.scalar_expr(it, env, depth - 1))
        if kind < 0.12:
            other = "u32" if ty == "i32" else "i32"
            return "(%s as %s)" % (self.scalar_expr(other, env, depth - 1), ty)
        if kind < 0.2:
            return "(%s << %s)" % (self.scalar_expr(ty, env, depth - 1),
                                   self.scalar_expr("u32", env, 0))
        if kind < 0.26:
            return "(0 - %s)" % self.scalar_expr(ty, env, depth - 1)
        if kind < 0.34 and ty == "u32":
            return self.call_expr(env, depth)
        op = self.pick(["+", "-", "*", "&", "|", "^"])
        return "(%s %s %s)" % (self.scalar_expr(ty, env, depth - 1), op,
                               self.scalar_expr(ty, env, depth - 1))

    def call_expr(self, env, depth):
        """u32 를 내는 호출. 대여 규칙을 지킨다 -- 인자에 같은 지역변수를 두 번 쓰지 않는다."""
        r = self.rng
        opts = []
        for v, t in env.items():
            if t in self.structs:
                opts.append("ref_%s(&%s)" % (t, v))
            if t in self.enums:
                opts.append("ref_%s(&%s)" % (t, v))
        opts += [v + ".len" for v, t in env.items() if t in ("[]u8", "[]mut u8")]
        opts.append('sl("hello")')
        for v, t in env.items():
            if t == "[]u8":
                opts.append("sl(%s)" % v)
            if t == "[]mut u8":
                opts.append("slm(%s)" % v)
            if t == "*u8":
                opts.append("ptr(%s)" % v)
        opts.append("scal(%s, %s, %s)" % (self.scalar_expr("i32", env, 0),
                                          self.scalar_expr("u32", env, 0),
                                          self.scalar_expr("bool", env, 0)))
        return self.pick(opts)

    def struct_lit(self, name, env, depth):
        parts = []
        for f, ft in self.structs[name]:
            parts.append("%s: %s" % (f, self.value_of(ft, env, depth)))
        return "%s{ %s }" % (name, ", ".join(parts))

    def enum_lit(self, name, env, depth):
        v, payload = self.pick(self.enums[name])
        if not payload:
            return "%s.%s" % (name, v)
        args = ", ".join(self.value_of(t, env, depth) for t in payload)
        return "%s.%s(%s)" % (name, v, args)

    def value_of(self, ty, env, depth):
        """필드/페이로드에 넣을 값."""
        if ty == "u8":
            return self.scalar_expr("u32", env, min(depth, 1))
        if ty in ("i32", "u32", "bool"):
            return self.scalar_expr(ty, env, min(depth, 1))
        if ty == "[]u8":
            pool = [v for v, t in env.items() if t == "[]u8"]
            return self.pick(pool + ['"lit"', '"abc"']) if pool else self.pick(['"lit"', '"abc"'])
        if ty == "[]mut u8":
            pool = [v for v, t in env.items() if t == "[]mut u8"]
            return self.pick(pool) if pool else "m"
        return "0 as *u8"

    # --- 문 -----------------------------------------------------------------

    def block(self, env, depth, in_loop, nvar):
        r = self.rng
        out = []
        env = dict(env)
        mut = set(v for v in env if v.startswith("v"))
        for _ in range(r.randrange(1, 5)):
            kinds = ["let", "assign", "if", "call", "unsafe"]
            if depth > 0:
                kinds += ["for", "match"]
            if in_loop:
                kinds += ["break", "continue"]
            kind = self.pick(kinds)
            if kind == "let":
                name = "v%d" % nvar[0]
                nvar[0] += 1
                choices = self.SCALARS + list(self.structs) + list(self.enums) + ["[]u8"]
                ty = self.pick(choices)
                if ty in self.structs:
                    out.append("let mut %s: %s = %s;" % (name, ty, self.struct_lit(ty, env, depth)))
                elif ty in self.enums:
                    out.append("let mut %s: %s = %s;" % (name, ty, self.enum_lit(ty, env, depth)))
                elif ty == "[]u8":
                    out.append('let mut %s: []u8 = "seed";' % name)
                else:
                    out.append("let mut %s: %s = %s;" % (name, ty, self.scalar_expr(ty, env, depth)))
                env[name] = ty
                mut.add(name)
            elif kind == "assign":
                pool = [v for v in mut if env[v] in self.SCALARS]
                fields = []
                for v in mut:
                    if env[v] in self.structs:
                        fields += [("%s.%s" % (v, f), ft) for f, ft in self.structs[env[v]]]
                for v, t in env.items():
                    if t == "[]mut u8":
                        fields.append(("%s[0]" % v, "u8"))
                if pool and r.random() < 0.5:
                    v = self.pick(pool)
                    ops = ["="] if env[v] == "bool" else ["=", "+=", "-=", "*=", "&=", "|=", "^="]
                    op = self.pick(ops)
                    out.append("%s %s %s;" % (v, op, self.scalar_expr(env[v], env, depth)))
                elif fields:
                    place, ft = self.pick(fields)
                    out.append("%s = %s;" % (place, self.value_of(ft, env, depth)))
                else:
                    out.append("noop();")
            elif kind == "if":
                cond = self.scalar_expr("bool", env, depth)
                then = self.block(env, depth - 1, in_loop, nvar) if depth > 0 else ["noop();"]
                if r.random() < 0.5:
                    els = self.block(env, depth - 1, in_loop, nvar) if depth > 0 else ["noop();"]
                    out.append("if %s { %s } else { %s }" % (cond, " ".join(then), " ".join(els)))
                else:
                    out.append("if %s { %s }" % (cond, " ".join(then)))
            elif kind == "for":
                body = self.block(env, depth - 1, True, nvar)
                form = r.random()
                if form < 0.4:
                    out.append("for let mut i%d: u32 = 0; i%d < 3; i%d += 1 { %s }"
                               % (nvar[0], nvar[0], nvar[0], " ".join(body)))
                    nvar[0] += 1
                elif form < 0.7:
                    out.append("for %s { %s break; }" % (self.scalar_expr("bool", env, 1),
                                                         " ".join(body)))
                else:
                    out.append("for { %s break; }" % " ".join(body))
            elif kind == "match":
                pool = [v for v, t in env.items() if t in self.enums]
                if not pool:
                    out.append("noop();")
                    continue
                v = self.pick(pool)
                en = env[v]
                arms = []
                variants = self.enums[en]
                use_wild = r.random() < 0.4 and len(variants) > 1
                shown = variants[:-1] if use_wild else variants
                for vn, payload in shown:
                    binds = ["b%d" % i for i in range(len(payload))]
                    henv = dict(env)
                    for b, t in zip(binds, payload):
                        henv[b] = "u32" if t == "u8" else t
                    body = " ".join(self.block(henv, depth - 1, in_loop, nvar))
                    head = vn if not binds else "%s(%s)" % (vn, ", ".join(binds))
                    arms.append("%s => { %s }" % (head, body))
                if use_wild:
                    arms.append("_ => { noop(); }")
                out.append("match %s { %s }" % (v, " ".join(arms)))
            elif kind == "unsafe":
                out.append("unsafe { let p%d: *u32 = 0x2000 as *u32; p%d.^ = %s; }"
                           % (nvar[0], nvar[0], self.scalar_expr("u32", env, 1)))
                nvar[0] += 1
            elif kind == "call":
                out.append("noop();")
                if r.random() < 0.6:
                    muts = [v for v, t in env.items() if t in self.structs or t in self.enums]
                    if muts:
                        v = self.pick(muts)
                        out.append("mut_%s(&mut %s);" % (env[v], v))
            elif kind == "break":
                out.append("break;")
                break
            elif kind == "continue":
                out.append("continue;")
                break
        return out or ["noop();"]

    def program(self) -> str:
        parts = self.gen_decls() + self.helper_fns()
        nvar = [0]
        env = {"a": "i32", "b": "u32", "c": "bool", "s": "[]u8", "m": "[]mut u8", "q": "*u8"}
        body = self.block(env, 3, False, nvar)
        parts.append(
            "fn f(a: i32, b: u32, c: bool, s: []u8, m: []mut u8, q: *u8) -> u32 {\n  "
            + "\n  ".join(body) + "\n  return b;\n}"
        )
        return "\n".join(parts) + "\n"
