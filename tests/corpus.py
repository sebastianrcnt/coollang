"""두 구현이 반드시 같은 바이트를 내야 하는 프로그램들 (implementation.md §8).

test_parity.py 가 cool0.py 와 cool0c.wasm 을 이것으로 맞춰 본다.

소스 자체는 fixtures/cool0/valid/ 와 fixtures/cool0/invalid/ 에 파일로 있다
(tests/fixtures.py 가 읽는다). 여기서는 그 전체 집합 중 이 모듈이 맡아 온 이름만
골라 옛 인터페이스(PROGRAMS, DIAGNOSTICS) 그대로 내보낸다 -- gaps.py 가 고른
이름은 그쪽이 맡는다.
"""

from __future__ import annotations

from fixtures import load_invalid, load_valid

# fixtures/cool0/valid/ 에서 이 모듈이 맡는 이름들 (원래 corpus.py 의 PROGRAMS)
PROGRAM_NAMES = [
    "empty", "noop", "add", "consts", "all-operators", "control-flow",
    "aggregates", "slices", "unsafe-pointers", "recursion", "borrows", "abi-shape",
]

# fixtures/cool0/invalid/ 에서 이 모듈이 맡는 이름들 (원래 corpus.py 의 DIAGNOSTICS)
DIAGNOSTIC_NAMES = [
    "non-ascii", "bad-token", "unterminated-string", "bad-number",
    "missing-semicolon", "chained-comparison", "unknown-name", "type-mismatch",
    "aggregate-by-value", "slice-return", "missing-return", "non-exhaustive",
    "aliasing", "borrow-escapes", "unsafe-required", "u8-local",
    "break-outside-loop", "duplicate-name", "const-cycle", "deep-nesting",
]

_valid_by_name = {name: src for name, src in load_valid()}
_invalid_by_name = {name: (src, diag) for name, src, diag in load_invalid()}

# (이름, 소스). 성공해야 하는 것들
PROGRAMS = [(name, _valid_by_name[name].decode("ascii")) for name in PROGRAM_NAMES]

# (이름, 소스). 같은 진단이 나와야 하는 것들
DIAGNOSTICS = [(name, _invalid_by_name[name][0]) for name in DIAGNOSTIC_NAMES]

# 이름 -> 정확한 진단 한 줄 (implementation.md §9). fixtures/cool0/invalid/ 의
# `// expect: error ...` 머리글에서 온다 -- 세 구현이 서로 일치하는지가 아니라,
# 오라클이 명세가 정한 그 문구를 정확히 내는지를 본다.
DIAGNOSTIC_TEXT = {name: diag for name, (_src, diag) in _invalid_by_name.items()}
