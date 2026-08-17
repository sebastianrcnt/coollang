"""두 구현이 반드시 같은 바이트를 내야 하는 프로그램들 (implementation.md §8).

test_parity.py 가 cool0.py 와 cool0c.wasm 을 이것으로 맞춰 본다. 아직 cool0c 가
없으므로 지금은 참조 구현이 이 전부를 컴파일해 내는지만 본다.
"""

# (이름, 소스). 성공해야 하는 것들
PROGRAMS = [
    ("empty", ""),
    ("noop", "fn f() { }"),
    ("add", "fn add(a: i32, b: i32) -> i32 { return a + b; }"),
    ("consts", "const A: i32 = 1 + 2; const B: u32 = A as u32 * 3; fn f() -> u32 { return B; }"),
    (
        "all_operators",
        """
fn arith(a: i32, b: i32) -> i32 { return a + b - a * b / (b | 1) % (b | 1); }
fn bits(a: u32, b: u32) -> u32 { return (a & b) ^ (a | b) << 3 >> 2; }
fn cmps(a: i32, b: i32) -> bool { return a == b || a != b && a < b || a > b || a <= b || a >= b; }
fn unary(a: i32, c: bool) -> i32 { if !c { return -a; } return a; }
fn casts(a: i32) -> u32 { return a as u32; }
""",
    ),
    (
        "control_flow",
        """
fn f(n: u32) -> u32 {
    let mut acc: u32 = 0;
    for let mut i: u32 = 0; i < n; i += 1 {
        if i % 2 == 0 { continue; }
        if i > 100 { break; }
        acc += i;
    }
    for { break; }
    let mut j: u32 = 0;
    for j < n { j += 1; }
    if n == 0 { acc = 0; } else if n == 1 { acc = 1; } else { acc += j; }
    return acc;
}
""",
    ),
    (
        "aggregates",
        """
struct Point { x: i32, y: i32 }
struct Mixed { flag: bool, byte: u8, n: u32, s: []u8 }
enum Shape { Dot, Line(i32), Rect(i32, i32) }

fn move_by(p: &mut Point, dx: i32, dy: i32) { p.^.x += dx; p.^.y += dy; }

fn area(s: &Shape) -> i32 {
    match s.^ {
        Dot => { return 0; }
        Line(n) => { return n; }
        Rect(w, h) => { return w * h; }
    }
}

fn f() -> i32 {
    let mut p: Point = Point{ x: 1, y: 2 };
    move_by(&mut p, 3, 4);
    let mut m: Mixed = Mixed{ flag: true, byte: 7, n: 9, s: "hi" };
    m.byte = 255;
    let mut s: Shape = Shape.Rect(3, 4);
    let a: i32 = area(&s);
    s = Shape.Line(m.n as i32);
    return p.x + a + area(&s) + m.s.len as i32;
}
""",
    ),
    (
        "slices",
        """
fn fill(a: []mut u8, v: u32) { for let mut i: u32 = 0; i < a.len; i += 1 { a[i] = v; } }
fn sum(a: []u8) -> u32 {
    let mut t: u32 = 0;
    for let mut i: u32 = 0; i < a.len; i += 1 { t += a[i]; }
    return t;
}
fn words(a: []mut u32, i: u32, v: u32) { a[i] = v; }
fn literal() -> u32 { let s: []u8 = "cool0"; return s.len + s[0]; }
""",
    ),
    (
        "unsafe_pointers",
        """
fn poke(addr: u32, v: u32) { unsafe { let p: *u32 = addr as *u32; p.^ = v; } }
fn peek(addr: u32) -> u32 { unsafe { let p: *u32 = addr as *u32; return p.^; } }
fn is_null(a: u32) -> bool { let p: *u8 = a as *u8; return p == 0 as *u8; }
""",
    ),
    (
        "recursion",
        """
fn fib(n: u32) -> u32 { if n < 2 { return n; } return fib(n - 1) + fib(n - 2); }
fn even(n: u32) -> bool { if n == 0 { return true; } return odd(n - 1); }
fn odd(n: u32) -> bool { if n == 0 { return false; } return even(n - 1); }
""",
    ),
    (
        "borrows",
        """
struct S { a: i32 }
fn read2(x: &S, y: &S) -> i32 { return x.^.a + y.^.a; }
fn write1(x: &mut S) { x.^.a += 1; }
fn forward(x: &mut S) { write1(x); write1(x); }
fn incr(n: &mut u32) { n.^ += 1; }
fn f() -> i32 {
    let mut s: S = S{ a: 1 };
    forward(&mut s);
    let mut n: u32 = 0;
    incr(&mut n);
    return read2(&s, &s) + n as i32;
}
""",
    ),
    (
        "abi_shape",
        """
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
""",
    ),
]

# (이름, 소스). 같은 진단이 나와야 하는 것들
DIAGNOSTICS = [
    ("non_ascii", b"fn f() { }\n// \xed\x95\x9c"),
    ("bad_token", b"fn f() { @ }"),
    ("unterminated_string", b'fn f() { let s: []u8 = "abc; }'),
    ("bad_number", b"fn f() { let x = 0x; }"),
    ("missing_semicolon", b"fn f() { let x: i32 = 1 }"),
    ("chained_comparison", b"fn f() { let x = 1 < 2 < 3; }"),
    ("unknown_name", b"fn f() { let x = y; }"),
    ("type_mismatch", b"fn f() { let a: i32 = 1; let b: u32 = a; }"),
    ("aggregate_by_value", b"struct S { a: i32 } fn f(s: S) { }"),
    ("slice_return", b"fn f() -> []u8 { }"),
    ("missing_return", b"fn f() -> i32 { }"),
    ("non_exhaustive", b"enum E { A, B } fn f() { let e: E = E.A; match e { A => { } } }"),
    ("aliasing", b"struct S { a: i32 }\nfn g(x: &mut S, y: &mut S) { }\n"
                 b"fn f() { let mut s: S = S{ a: 1 }; g(&mut s, &mut s); }"),
    ("borrow_escapes", b"struct S { a: i32 } fn f() { let mut s: S = S{ a: 1 }; let r = &mut s; }"),
    ("unsafe_required", b"fn f() { let p: *u32 = 0 as *u32; let x = p.^; }"),
    ("u8_local", b"fn f() { let b: u8 = 0; }"),
    ("break_outside_loop", b"fn f() { break; }"),
    ("duplicate_name", b"fn f() { } fn f() { }"),
    ("const_cycle", b"const A: i32 = B; const B: i32 = A;"),
    ("deep_nesting", b"fn f() -> i32 { return " + b"(" * 200 + b"1" + b")" * 200 + b"; }"),
]
