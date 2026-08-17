"""커버리지가 비어 있던 자리를 직접 때린다.

여기 있는 소스 하나하나는 참조 구현에서 한 번도 실행된 적이 없던 줄을 겨냥한다.
그런 줄은 "두 구현이 일치한다"가 아직 증명되지 않은 곳이다.
"""

GAPS = [
    # --- 렉서의 남은 오류 경로 ---
    (b"'", "unterminated char at eof"),
    (b"'" + bytes([92]), "unterminated char after escape"),
    (bytes([34, 92]), "unterminated string after escape"),
    # --- ty_str 가 *T 를 찍는 자리 ---
    (b"fn f(p: *u8) { let x: i32 = p; }", "print a raw pointer type"),
    (b"fn f(p: *u8) -> i32 { return p; }", "return a raw pointer type"),
    # --- 상수식의 오류들 ---
    (b"const C: i32 = zz;", "not a constant"),
    (b"const C: i32 = true as i32;", "const cast from bool"),
    (b"const C: bool = 1 && true;", "const && on non-bool"),
    (b"const C: bool = 1 || true;", "const || on non-bool"),
    (b"const C: i32 = true + 1;", "const arith on bool"),
    (b"const C: i32 = 1 << true;", "const shift by bool"),
    (b'const C: i32 = 1 + "x";', "const arith on slice"),
    (b"enum E { A } const C: i32 = E.A;", "const of an enum literal"),
    # --- 지역변수 타입 ---
    (b"fn g() { } fn f() { let x = g(); }", "local of type void"),
    # --- 집합체 리터럴 ---
    (b"fn f() { let s: Zork = Zork{ a: 1 }; }", "unknown struct in a literal"),
    (b"enum E { A } fn f() { let e: E = E.Zork; }", "unknown variant in a literal"),
    (b"struct S { a: i32 } enum E { A } fn f() { let s: S = E.A; }",
     "enum literal where a struct is wanted"),
    (b"struct S { a: i32 } fn f() { let x: i32 = S{ a: 1 }; }",
     "struct literal where a scalar is wanted"),
    (b"struct S { a: i32 } fn f() { let x = 1 + S{ a: 1 }; }",
     "struct literal inside an expression"),
    (b"enum E { A } fn f() { let x = 1 + E.A; }", "enum literal inside an expression"),
    (b"enum E { A(i32) } fn f() { let x = 1 + E.A(1); }", "enum call inside an expression"),
    # --- 식 검사 ---
    (b"fn f() { let a = -true; }", "negate a bool"),
    (b"fn f() { let a: i32 = 1; let b = a as bool; }", "cast to bool"),
    (b"fn f() { let a = true; let b = a << 1; }", "shift a bool"),
    (b"fn f() { let a = true; let b = true; let c = a * b; }", "arith on bools"),
    (b"fn f() { let a: i32 = 1; a(); }", "callee is not a function"),
    (b"struct S { a: i32 } fn f() { let s: S = S{ a: 1 }; let x = s.zz; }",
     "unknown struct field"),
    (b"fn f() { let a: i32 = 1; let b = a[0]; }", "index a scalar"),
    (b"fn f() { 1 = 2; }", "assign to a non-place"),
    (b"fn g() -> u32 { return 0; } fn f() { g()(); }", "callee is a call"),
    (b"fn f() { let mut a = true; a <<= 1; }", "compound shift on a bool"),
    (b"struct S { a: i32 } fn f() { let mut s: S = S{ a: 1 }; s <<= 1; }",
     "compound shift on a struct place"),
    (b"struct S { a: i32 } fn f() { let s: S = 1; }", "scalar where a struct is wanted"),
    (b"enum E { A } fn f() { let e: E = E{ a: 1 }; }", "enum used as a struct literal"),
    (b"fn f() { let x = zz.len; }", "the .len probe fails"),
    (b"struct S { a: i32 } fn f() { let x = S.a; }", "a type used as a place"),
    (b"struct S { a: i32 } fn g(p: &S) { } fn f() { let s: S = S{ a: 1 }; g(&s.zz); }",
     "borrow of an unknown field"),
    # --- else-if 사슬의 블록 한계 ---
    (b"fn f() { if true { } " + b"else if true { } " * 70 + b"}", "else-if chain limit"),
]

# 컴파일에 성공해야 하는 것들. 방출기의 빈 경로를 때린다
GAP_PROGRAMS = [
    # 주소를 취한 매개변수 -- 프롤로그가 프레임으로 복사한다 (implementation.md S6)
    ("""
fn peek(x: &u32) -> u32 { return x.^; }
fn f(n: u32) -> u32 { return peek(&n); }
""", "address-taken scalar parameter"),
    ("""
fn slen(s: &[]u8) -> u32 { return s.^.len; }
fn f(a: []u8) -> u32 { return slen(&a); }
""", "address-taken slice parameter"),
    ("""
fn peek(x: &bool) -> bool { return x.^; }
fn f(c: bool) -> bool { return peek(&c); }
""", "address-taken bool parameter"),
    ("""
fn peek(x: &*u8) -> u32 { return 0; }
fn f(p: *u8) -> u32 { return peek(&p); }
""", "address-taken pointer parameter"),
    ("""
fn two(x: &u32, y: &u32) -> u32 { return x.^ + y.^; }
fn f(a: u32, b: u32) -> u32 { return two(&a, &b); }
""", "two address-taken parameters"),
    # has_break 가 match 와 unsafe 를 통과해야 한다
    ("""
enum E { A, B }
fn f() -> u32 {
    let e: E = E.A;
    for { match e { A => { break; } B => { } } }
    return 1;
}
""", "break inside a match inside a loop"),
    ("""
fn f() -> u32 { for { unsafe { break; } } return 1; }
""", "break inside unsafe inside a loop"),
    ("""
enum E { A, B }
fn f() -> u32 { let e: E = E.A; for { match e { A => { } B => { } } } }
""", "match without break keeps the loop infinite"),
    # 슬라이스 원소가 집합체인 경우
    ("""
struct R { id: u32, n: i32 }
fn take(p: &mut R) { p.^.id += 1; }
fn f(a: []mut R) -> u32 { take(&mut a[0]); return a[1].id; }
""", "slice of structs, borrow of an element"),
    ("""
enum C { E, F(u32) }
fn f(a: []C) -> u32 { match a[0] { E => { return 0; } F(v) => { return v; } } }
""", "match on an indexed enum"),
    # 열거 페이로드가 슬라이스
    ("""
enum T { End, W([]u8, u32) }
fn f() -> u32 { let t: T = T.W("abcd", 7); match t { End => { return 0; } W(s, n) => { return s.len + n + s[0]; } } }
""", "enum with a slice payload"),
    # 빈 집합체
    ("struct S { } fn f() { let s: S = S{ }; }", "empty struct"),
    ("""
fn f(a: u32, n: u32) -> u32 { let mut x: u32 = a; x <<= n; x >>= 1; return x; }
fn g(a: i32, n: u32) -> i32 { let mut x: i32 = a; x >>= n; x <<= 1; return x; }
""", "compound shift assignment"),
    ("""
struct S { a: u32, b: i32 }
fn f() -> u32 { let mut s: S = S{ a: 8, b: 0 - 16 }; s.a <<= 2; s.b >>= 2; return s.a; }
""", "compound shift on a struct field"),
    ("const C: bool = true == false; fn f() -> bool { return C; }", "const bool equality"),
    ("const C: bool = true != false; fn f() -> bool { return C; }", "const bool inequality"),
    ("fn f(x: u32) -> bool { return 1 << 2 == x; }", "a shift that has not settled"),
    ("fn f() -> u32 { unsafe { let x: u32 = (0x3000 as *u32).^; return x; } }",
     "deref of a cast, no root local"),
    ("enum E { } fn f(p: &E) { match p.^ { } }", "enum with no variants"),
    # 타입 섹션의 중복 제거와 문자열 공유
    ("""
fn a(x: i32) -> i32 { return x; }
fn b(y: u32) -> u32 { return y; }
fn c() { }
fn d() { }
fn e() -> u32 { let s: []u8 = "shared"; return s.len; }
fn g() -> u32 { let s: []u8 = "shared"; return s.len; }
""", "signature dedup and string sharing"),
    # 포인터 비교와 캐스팅
    ("""
fn f(p: *u8, q: *u32) -> bool { return p == (q as u32) as *u8; }
""", "pointer comparison through casts"),
    # for 후처리가 호출인 경우
    ("""
fn step(a: []mut u32) { a[0] += 1; }
fn f(a: []mut u32) -> u32 { for let mut i: u32 = 0; a[0] < 3; step(a) { i += 1; } return a[0]; }
""", "a call as the for post statement"),
    # 중첩 match
    ("""
enum E { A, B(u32) }
fn f(x: &E, y: &E) -> u32 {
    match x.^ {
        A => { match y.^ { A => { return 1; } B(v) => { return v; } } }
        B(v) => { match y.^ { A => { return v; } B(w) => { return v + w; } } }
    }
}
""", "nested match"),
    # 깊은 재귀 + 집합체 지역변수 (섀도 스택)
    ("""
struct Acc { total: u32, depth: u32 }
fn step(a: &mut Acc, n: u32) { a.^.depth += 1; a.^.total += n; if n > 0 { step(a, n - 1); } }
fn f(n: u32) -> u32 { let mut a: Acc = Acc{ total: 0, depth: 0 }; step(&mut a, n); return a.total; }
""", "aggregate local plus recursion"),
]
