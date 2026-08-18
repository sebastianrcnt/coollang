"""전사 진행 (README 의 부트스트랩 3단계).

`cool0c.wat` 은 `cool0c.cool0` 을 **손으로 옮겨 적은 것**이다. 새 설계가 아니므로
함수 하나하나가 짝을 갖고, 그래서 진행을 셀 수 있다.

셀 수 있게 만드는 이유는 하나다. 지금 전사는 함수 365 개짜리 작업이고, 그것을
"8천 줄 덩어리" 로 마주하면 어디까지 왔는지가 안 보인다. 여기서는 매번 한 줄로

    전사 232/365 (63%) -- 없는 함수 100, 서명이 어긋난 함수 33, 남은 옛 함수 6

를 스킵 사유로 찍는다. 셋이 다 0 이 되면 스킵이 사라지고 같은 세 가지가 강제된다.

**여기서 알 수 있는 것과 없는 것.** 이름과 서명은 텍스트로 볼 수 있지만 *본문이
낡았는지*는 볼 수 없다 -- 서명이 그대로인 채 속만 바뀐 함수가 많다. 그쪽은
패리티가 잡는다 (test_parity.py, test_milestone.py). 여기는 그 앞단, "무엇을 아직
안 건드렸나" 를 세는 자리다.

`wat2wasm` 이 받아주는지는 conftest 의 `wat_is_current()` 가 따로 본다.
"""

from __future__ import annotations

import pathlib
import re

import pytest

SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "cool0"
COOL0C = SRC_DIR / "cool0c.cool0"
COOL0C_WAT = SRC_DIR / "cool0c.wat"

NL = chr(10)

# WAT 에만 있어야 하는 함수들. cool0 은 슬라이스 첨자를 **언어가** 검사하지만
# (language.md §5) wasm 에는 그런 것이 없으므로, 손으로 쓴 판은 경계 검사를 하는
# 접근자를 따로 둔다. 빠진 전사가 아니라 전사에 딸린 배관이다.
WAT_ONLY = {
    "cg": "Ctx 의 워드 읽기",
    "cs": "Ctx 의 워드 쓰기",
    "rg": "아레나 레코드 읽기 (경계 검사)",
    "rs": "아레나 레코드 쓰기 (경계 검사)",
    "rec": "아레나 레코드 주소 (경계 검사). 표 열한 개가 이걸 쓴다",
    "nd": "노드 필드 읽기",
    "np": "노드 주소 (경계 검사)",
    "nset": "노드 필드 쓰기",
    "tk": "토큰 필드 읽기 (경계 검사)",
    "tok": "토큰 주소 (경계 검사)",
    "ty_at": "타입 아레나 읽기 (경계 검사)",
    "src_at": "소스 바이트 읽기 (경계 검사)",
    "src_len": "소스 길이",
    "scope_at": "스코프 스택 읽기 (경계 검사)",
}


def source_functions() -> dict[str, str]:
    """cool0c.cool0 의 함수 이름 -> 서명."""
    text = COOL0C.read_text("ascii")
    return {m.group(1): m.group(0)
            for m in re.finditer(r"^fn (\w+)\(([^)]*)\)(\s*->\s*[^{]+)?", text, re.M)}


def wat_functions() -> dict[str, str]:
    """cool0c.wat 의 함수 이름 -> 서명.

    서명이 한 줄에 안 들어가면 다음 줄로 이어진다. 이어진 줄까지 읽지 않으면
    `(result i32)` 를 놓쳐서 "반환이 없다" 로 잘못 읽는다.
    """
    lines = COOL0C_WAT.read_text("ascii").split(NL)
    out = {}
    for i, line in enumerate(lines):
        m = re.match(r"^  \(func \$(\w+)", line)
        if not m:
            continue
        sig, j = line, i + 1
        while j < len(lines) and re.match(r"^\s+\((param|result) ", lines[j]):
            sig += " " + lines[j].strip()
            j += 1
        out[m.group(1)] = sig
    return out


def expected_words(sig: str) -> tuple[int, int]:
    """cool0 서명에서 (wasm 매개변수 수, wasm 결과 수) (implementation.md §3).

    슬라이스 매개변수는 wasm 매개변수 **둘**로 펼쳐진다 (`ptr`, `len`). 나머지는
    하나. 반환은 값 하나 아니면 없음.
    """
    params = sig[sig.index("(") + 1: sig.index(")")]
    n = 0
    for p in params.split(","):
        if p.strip():
            n += 2 if p.split(":", 1)[1].strip().startswith("[]") else 1
    return n, (1 if "->" in sig else 0)


def wasm_words(decl: str) -> tuple[int, int]:
    return decl.count("(param "), decl.count("(result ")


SRC_FNS = source_functions()
WAT_FNS = wat_functions()

MISSING = sorted(set(SRC_FNS) - set(WAT_FNS))
EXTRA = sorted(set(WAT_FNS) - set(SRC_FNS) - set(WAT_ONLY))
STALE = sorted(
    name for name in set(SRC_FNS) & set(WAT_FNS)
    if expected_words(SRC_FNS[name]) != wasm_words(WAT_FNS[name])
)
DONE = len(SRC_FNS) - len(MISSING) - len(STALE)
PENDING = bool(MISSING or STALE or EXTRA)


def progress() -> str:
    return (f"전사 {DONE}/{len(SRC_FNS)} ({100 * DONE // len(SRC_FNS)}%)"
            f" -- 없는 함수 {len(MISSING)},"
            f" 서명이 어긋난 함수 {len(STALE)},"
            f" 남은 옛 함수 {len(EXTRA)}")


incomplete = pytest.mark.skipif(PENDING, reason=progress())


def test_the_source_and_the_transcription_can_both_be_read():
    """나머지가 정규식 둘에 기대고 있으니 그것부터 확인한다."""
    assert len(SRC_FNS) > 300, len(SRC_FNS)
    assert len(WAT_FNS) > 200, len(WAT_FNS)


@pytest.mark.parametrize("name, why", sorted(WAT_ONLY.items()))
def test_every_declared_wat_only_helper_really_exists(name, why):
    """WAT_ONLY 가 낡지 않게. 없는 이름을 면제해 두면 면제가 조용히 늘어난다."""
    assert name in WAT_FNS, f"WAT_ONLY 에 적힌 `{name}` ({why}) 이 WAT 에 없다"


@incomplete
def test_every_source_function_has_been_transcribed():
    assert not MISSING, f"아직 옮기지 않은 함수 {len(MISSING)} 개: {MISSING}"


@incomplete
def test_every_transcribed_signature_takes_the_right_number_of_words():
    """서명이 어긋나 있으면 그 함수는 옛 판이다."""
    assert not STALE, NL.join(
        f"{n}: cool0 는 {expected_words(SRC_FNS[n])}, "
        f"wasm 은 {wasm_words(WAT_FNS[n])}" for n in STALE
    )


@incomplete
def test_the_transcription_has_no_leftovers():
    """cool0c.cool0 에서 사라진 함수가 WAT 에 남아 있으면 안 된다.

    끝난 뒤에만 본다. 전사 도중에는 아직 안 옮긴 코드가 옛 함수를 부르고 있으므로
    지우면 wat2wasm 을 통과하지 못한다. 옛 함수는 마지막 호출자가 사라질 때 같이
    사라진다.
    """
    assert not EXTRA, (
        f"WAT 에 원본이 없는 함수 {len(EXTRA)} 개: {EXTRA}. "
        f"진짜 WAT 전용 배관이면 WAT_ONLY 에 이유와 함께 적어라"
    )
