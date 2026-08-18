"""커버리지가 비어 있던 자리를 직접 때린다.

여기 있는 소스 하나하나는 참조 구현에서 한 번도 실행된 적이 없던 줄을 겨냥한다.
그런 줄은 "두 구현이 일치한다"가 아직 증명되지 않은 곳이다.

소스 자체는 fixtures/cool0/valid/ 와 fixtures/cool0/invalid/ 에 파일로 있다
(tests/fixtures.py 가 읽는다). 여기서는 그 전체 집합 중 이 모듈이 맡아 온
이름만 골라 옛 인터페이스(GAPS, GAP_PROGRAMS) 그대로 내보낸다 -- corpus.py 가
고른 이름은 그쪽이 맡는다.
"""

from __future__ import annotations

from fixtures import load_invalid, load_valid

# fixtures/cool0/invalid/ 에서 이 모듈이 맡는 이름들 (원래 gaps.py 의 GAPS)
GAP_NAMES = [
    "underscore-after-hex-prefix", "underscore-after-binary-prefix",
    "two-underscores-after-hex-prefix", "non-digit-after-hex-prefix",
    "integer-literal-far-out-of-range",
    "unterminated-char-at-eof", "unterminated-char-after-escape",
    "unterminated-string-after-escape", "print-a-raw-pointer-type",
    "return-a-raw-pointer-type", "not-a-constant", "const-cast-from-bool",
    "const-and-on-non-bool", "const-or-on-non-bool", "const-arith-on-bool",
    "const-shift-by-bool", "const-arith-on-slice", "const-of-an-enum-literal",
    "local-of-type-void", "unknown-struct-in-a-literal",
    "unknown-variant-in-a-literal", "enum-literal-where-a-struct-is-wanted",
    "struct-literal-where-a-scalar-is-wanted", "struct-literal-inside-an-expression",
    "enum-literal-inside-an-expression", "enum-call-inside-an-expression",
    "negate-a-bool", "cast-to-bool", "shift-a-bool", "arith-on-bools",
    "callee-is-not-a-function", "unknown-struct-field", "index-a-scalar",
    "assign-to-a-non-place", "callee-is-a-call", "compound-shift-on-a-bool",
    "compound-shift-on-a-struct-place", "scalar-where-a-struct-is-wanted",
    "enum-used-as-a-struct-literal", "the-len-probe-fails",
    "a-type-used-as-a-place", "borrow-of-an-unknown-field", "else-if-chain-limit",
]

# fixtures/cool0/valid/ 에서 이 모듈이 맡는 이름들 (원래 gaps.py 의 GAP_PROGRAMS)
GAP_PROGRAM_NAMES = [
    "address-taken-scalar-parameter", "address-taken-slice-parameter",
    "address-taken-bool-parameter", "address-taken-pointer-parameter",
    "two-address-taken-parameters", "break-inside-a-match-inside-a-loop",
    "break-inside-unsafe-inside-a-loop", "match-without-break-keeps-the-loop-infinite",
    "slice-of-structs-borrow-of-an-element", "match-on-an-indexed-enum",
    "enum-with-a-slice-payload", "empty-struct", "compound-shift-assignment",
    "compound-shift-on-a-struct-field", "const-bool-equality", "const-bool-inequality",
    "a-shift-that-has-not-settled", "deref-of-a-cast-no-root-local",
    "enum-with-no-variants", "signature-dedup-and-string-sharing",
    "left-associative-operator-chains",
    "pointer-comparison-through-casts", "a-call-as-the-for-post-statement",
    "nested-match", "aggregate-local-plus-recursion",
]

_valid_by_name = {name: src for name, src in load_valid()}
_invalid_by_name = {name: src for name, src, _diag in load_invalid()}

# (소스, 설명). 같은 진단이 나와야 하는 것들
GAPS = [(_invalid_by_name[name], name.replace("-", " ")) for name in GAP_NAMES]

# (소스, 설명). 컴파일에 성공해야 하는 것들
GAP_PROGRAMS = [
    (_valid_by_name[name].decode("ascii"), name.replace("-", " "))
    for name in GAP_PROGRAM_NAMES
]
