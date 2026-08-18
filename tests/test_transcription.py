"""전사 진행 (README 의 부트스트랩 3단계).

`cool0c.wat` 은 `cool0c.cool0` 을 **손으로 옮겨 적은 것**이다. 새 설계가 아니므로
함수 하나하나가 짝을 갖고, 그래서 진행을 셀 수 있다.

셀 수 있게 만드는 이유는 하나다. 지금 전사는 함수 365 개짜리 작업이고, 그것을
"8천 줄 덩어리" 로 마주하면 어디까지 왔는지가 안 보인다. 여기서는 매번 한 줄로

    전사 232/365 (63%) -- 없는 함수 100, 서명이 어긋난 함수 33, 남은 옛 함수 6

를 스킵 사유로 찍는다. 셋이 다 0 이 되면 스킵이 사라지고 같은 세 가지가 강제된다.

**본문이 낡은 것도 여기서 본다.** 처음에는 이름과 서명만 셌다. 그런데 서명이
그대로인 채 속만 낡은 함수가 서른 개 넘게 있었고, 그것들은 패리티가 잡아 주지
못했다 -- 전사가 끝나기 전에는 패리티 시험이 통째로 스킵되니까. 그래서 텍스트만
보고 확인할 수 있는 네 가지를 여기서 강제한다.

    검증      wat2wasm 은 파싱만 한다. 인자 수가 안 맞는 호출도 통과시킨다
    인자 수   모든 `(call $f ...)` 이 정의된 매개변수 수와 맞는가
    리터럴    박아 둔 (주소, 길이) 가 소스 리터럴 하나와 정확히 겹치는가
    노드 필드 함수마다 만지는 노드 필드가 원본과 같은가

넷 다 "다르면 언제나 버그" 인 것들이다. 완전하지는 않다 -- 헬퍼를 거치거나
필드 번호가 변수면 안 보인다. 나머지는 패리티가 잡는다 (test_parity.py,
test_milestone.py).
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
    "nmem_at": "인턴된 이름 바이트 읽기 (경계 검사)",
    "nmem_set": "인턴된 이름 바이트 쓰기 (경계 검사)",
    "ntab_at": "이름 표 읽기 (경계 검사)",
    "ntab_set": "이름 표 쓰기 (경계 검사)",
    "nhash_at": "이름 해시 읽기 (경계 검사)",
    "nhash_set": "이름 해시 쓰기 (경계 검사)",
    "nameset_at": "이름 집합 읽기 (경계 검사). taken/tynames/scalars 셋이 쓴다",
    "nameset_set": "이름 집합 쓰기 (경계 검사)",
    "scan_named": "아레나를 1 부터 훑어 이름으로 찾기",
    "walk_named": "next 사슬을 따라가며 이름으로 찾기",
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


# ---------------------------------------------------------------------------
# 본문이 낡았는지 -- 텍스트만 보고 확인할 수 있는 네 가지
# ---------------------------------------------------------------------------

WAT_TEXT = COOL0C_WAT.read_text("ascii")
RODATA = 0x1000_0000

NODE_FIELDS = {
    "kind": 0, "line": 4, "col": 8, "a": 12, "b": 16, "c": 20, "d": 24,
    "e": 28, "next": 32, "ty": 36, "aux": 40, "aux2": 44, "depth": 48, "op": 52,
}


def sub_exprs(text: str, at: int) -> list[str]:
    """`(` 에서 시작하는 s-식의 최상위 부분식들. 문자열 안의 괄호는 건너뛴다."""
    depth, i, out, start = 0, at, [], None
    while i < len(text):
        ch = text[i]
        if ch == chr(34):
            i += 1
            while text[i] != chr(34):
                i += 2 if text[i] == chr(92) else 1
        elif ch == "(":
            depth += 1
            if depth == 2:
                start = i
        elif ch == ")":
            depth -= 1
            if depth == 1 and start is not None:
                out.append(text[start:i + 1])
                start = None
            elif depth == 0:
                return out
        i += 1
    raise AssertionError("괄호가 안 닫힌다")


def wat_body(name: str) -> str:
    at = WAT_TEXT.index("  (func $" + name + " ")
    depth, i = 0, at
    while True:
        if WAT_TEXT[i] == chr(34):
            i += 1
            while WAT_TEXT[i] != chr(34):
                i += 2 if WAT_TEXT[i] == chr(92) else 1
        elif WAT_TEXT[i] == "(":
            depth += 1
        elif WAT_TEXT[i] == ")":
            depth -= 1
            if depth == 0:
                return WAT_TEXT[at:i + 1]
        i += 1


def literal_blob() -> tuple[bytes, dict[int, bytes]]:
    """소스 리터럴을 첫 등장 순서로 이은 것과, 각 리터럴이 시작하는 자리."""
    from cool0.cool0 import lex

    blob, seen, starts = bytearray(), set(), {}
    for t in lex(COOL0C.read_bytes()):
        if t.kind == "str" and t.value not in seen:
            seen.add(t.value)
            starts[len(blob)] = t.value
            blob += t.value
    return bytes(blob), starts


@incomplete
def test_the_transcription_is_a_valid_wasm_module():
    """`wat2wasm` 은 파싱만 한다.

    한때 매개변수 넷짜리 함수를 다섯 개로 부르는 호출이 있었고 wat2wasm 은 그걸
    그대로 통과시켰다. 타입 검사는 검증기가 한다.
    """
    import wasmtime

    wasmtime.Module.validate(wasmtime.Engine(), wasmtime.wat2wasm(WAT_TEXT))


@incomplete
def test_every_call_passes_the_right_number_of_arguments():
    """인터닝(gh #5 A) 으로 이름이 한 워드가 됐을 때 32 자리가 어긋나 있었다.

    받는 쪽만 고치고 부르는 쪽을 안 고쳐도 서명 대조는 통과한다.
    """
    nparams = {}
    for m in re.finditer(r"\(func \$(\w+)((?:[^\n]|\n(?!  \())*)", WAT_TEXT):
        nparams[m.group(1)] = m.group(0).split("(local ")[0].count("(param ")

    cur, bad = None, []
    for m in re.finditer(r"\(func \$(\w+)|\(call \$(\w+)[ \n)]", WAT_TEXT):
        if m.group(1):
            cur = m.group(1)
            continue
        callee = m.group(2)
        if callee not in nparams:
            bad.append(f"{cur} -> {callee}: 정의가 없다")
            continue
        got = len(sub_exprs(WAT_TEXT, m.start()))
        if got != nparams[callee]:
            bad.append(f"{cur} -> {callee}: 인자 {got} 개, 매개변수 {nparams[callee]} 개")
    assert not bad, NL.join(bad)


@incomplete
def test_every_string_address_lands_on_a_literal():
    """리터럴은 소스의 첫 등장 순서로 RODATA 부터 붙어 있다 (implementation.md §4).

    그러니 박아 둔 (주소, 길이) 는 **어떤 리터럴의 시작과 끝에** 맞아야 한다.
    한 글자만 어긋나도 진단 문구가 조용히 섞인다 -- "truct fieldecannot have..."
    가 실제로 나왔다.
    """
    blob, starts = literal_blob()
    cur, bad = None, []
    for m in re.finditer(
        r"  \(func \$(\w+)|\(i32\.const (0x1[0-9A-Fa-f]{7})\)\s*\(i32\.const (\d+)\)",
        WAT_TEXT,
    ):
        if m.group(1):
            cur = m.group(1)
            continue
        off, n = int(m.group(2), 16) - RODATA, int(m.group(3))
        if off < 0 or off + n > len(blob):
            bad.append(f"{cur}: {m.group(2)} +{n} 은 문자열 표 밖이다")
        elif starts.get(off) is None or len(starts[off]) != n:
            bad.append(f"{cur}: {m.group(2)} +{n} 은 {blob[off:off + n]!r} -- 리터럴이 아니다")
    assert not bad, NL.join(bad)


@incomplete
def test_every_function_touches_the_same_node_fields():
    """`.op` 가 새 칸으로 옮겨 갔는데 아홉 함수가 여전히 `.a` 를 읽고 있었다.

    함수마다 만지는 노드 필드의 **집합**을 양쪽에서 뽑아 견준다. 헬퍼를 거치거나
    필드 번호가 변수면 안 보이지만, 칸이 옮겨 간 것은 대부분 여기서 걸린다.
    """
    names = {v: k for k, v in NODE_FIELDS.items()}
    text = COOL0C.read_text("ascii")
    bad = []
    for chunk in re.split(NL + r"(?=fn )", text):
        m = re.match(r"fn (\w+)\(", chunk)
        if not m or m.group(1) not in WAT_FNS:
            continue
        # 주석에도 `nodes[base].kind` 같은 말이 나온다. 코드만 본다
        code = re.sub(r"//[^" + NL + "]*", "", chunk)
        want = {NODE_FIELDS[f] for f in re.findall(r"nodes\[[^\]]+\]\.(\w+)", code)
                if f in NODE_FIELDS}
        if not want:
            continue
        body, got = wat_body(m.group(1)), set()
        for c in re.finditer(r"\(call \$(nd|nset) ", body):
            args = sub_exprs(body, c.start())
            hit = re.fullmatch(r"\(i32\.const (\d+)\)", args[2].strip())
            if hit:
                got.add(int(hit.group(1)))
        if want != got:
            miss = ",".join(names.get(o, str(o)) for o in sorted(want - got))
            more = ",".join(names.get(o, str(o)) for o in sorted(got - want))
            bad.append(f"{m.group(1)}: 원본만 [{miss}], WAT 만 [{more}]")
    assert not bad, NL.join(bad)
