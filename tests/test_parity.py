"""패리티 (implementation.md §8).

    cool0.py(src)      -> (status, bytes)
    cool0c.wasm(src)   -> (status, bytes)      완전히 같아야 한다

cool0c.wat 을 wat2wasm 없이 wasmtime 이 바로 읽는다. 신뢰 사슬의 wat2wasm 이
여기서는 wasmtime 의 WAT 파서다.

최종 목표는 test_milestone.py 에 등식 하나로 적혀 있다. 여기는 그것이 깨졌을 때
프로그램 단위로 어디서 깨졌는지 짚어 주는 자리다.
"""

from __future__ import annotations

import pathlib

import pytest
import wasmtime

from conftest import ENGINE, run_compiler
from corpus import DIAGNOSTIC_TEXT, DIAGNOSTICS, PROGRAMS
from cool0.cool0 import STATUS_OK, compile as reference_compile

SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "cool0"
COOL0C_WAT = SRC_DIR / "cool0c.wat"
COOL0C_WASM = SRC_DIR / "cool0c.wasm"


def load_cool0c():
    """cool0c 를 wasm 바이트로. 없으면 None.

    WAT 를 그대로 읽는다 -- 신뢰 사슬의 wat2wasm 이 여기서는 wasmtime 의 WAT
    파서다. 별도 바이너리가 필요 없다.
    """
    if COOL0C_WASM.exists():
        return COOL0C_WASM.read_bytes()
    if COOL0C_WAT.exists():
        return bytes(wasmtime.wat2wasm(COOL0C_WAT.read_text("ascii")))
    return None


WASM = load_cool0c()
needs_cool0c = pytest.mark.skipif(
    WASM is None, reason="cool0c.wat / cool0c.wasm 이 아직 없다 (부트스트랩 2·3단계)"
)


def cool0c_compile(src: bytes) -> tuple[int, bytes]:
    """implementation.md §7 의 ABI 그대로 호출한다."""
    return run_compiler(WASM, src)


# --- 픽스처가 하나도 새지 않는지 --------------------------------------------


def test_every_fixture_is_claimed_by_exactly_one_module():
    """fixtures/cool0/ 의 모든 파일이 corpus.py 나 gaps.py 중 정확히 한쪽에 속한다.

    두 모듈은 이름 목록으로 자기 몫을 고른다. 새 픽스처를 넣고 목록에 안 적으면
    어느 테스트도 그 파일을 돌리지 않는다 -- 늘어난 커버리지처럼 보이지만 실제로는
    죽은 파일이다. 그 자리를 여기서 막는다.
    """
    import corpus
    import gaps
    from fixtures import load_invalid, load_valid

    for kind, on_disk, claims in [
        ("valid", {n for n, _ in load_valid()},
         [("corpus", corpus.PROGRAM_NAMES), ("gaps", gaps.GAP_PROGRAM_NAMES)]),
        ("invalid", {n for n, _, _ in load_invalid()},
         [("corpus", corpus.DIAGNOSTIC_NAMES), ("gaps", gaps.GAP_NAMES)]),
    ]:
        (mod_a, names_a), (mod_b, names_b) = claims
        both = set(names_a) & set(names_b)
        assert not both, f"{kind}: {sorted(both)} 를 {mod_a} 와 {mod_b} 가 겹쳐 맡았다"

        claimed = set(names_a) | set(names_b)
        orphans = on_disk - claimed
        assert not orphans, (
            f"fixtures/cool0/{kind}/ 의 {sorted(orphans)} 를 아무도 맡지 않았다 -- "
            f"{mod_a}.py 나 {mod_b}.py 의 이름 목록에 넣어라"
        )

        missing = claimed - on_disk
        assert not missing, (
            f"{mod_a}/{mod_b} 가 없는 파일 {sorted(missing)} 를 맡고 있다"
        )


# --- 참조 구현이 코퍼스를 감당하는지 ----------------------------------------


@pytest.mark.parametrize("name,src", PROGRAMS, ids=[n for n, _ in PROGRAMS])
def test_reference_compiles_the_corpus(name, src):
    status, out = reference_compile(src.encode("ascii"))
    assert status == STATUS_OK, out.decode("ascii")
    wasmtime.Module(ENGINE, out)


@pytest.mark.parametrize("name,src", DIAGNOSTICS, ids=[n for n, _ in DIAGNOSTICS])
def test_reference_rejects_the_bad_corpus(name, src):
    status, out = reference_compile(src)
    assert status != STATUS_OK
    assert out.decode("ascii").endswith("\n")


def test_the_corpus_is_deterministic():
    for _, src in PROGRAMS:
        b = src.encode("ascii")
        assert reference_compile(b) == reference_compile(b)


@pytest.mark.parametrize("name,src", DIAGNOSTICS, ids=[n for n, _ in DIAGNOSTICS])
def test_reference_matches_the_exact_fixture_diagnostic(name, src):
    """fixtures/cool0/invalid/*.cool0 이 적어 둔 `// expect: error ...` 그대로.

    implementation.md §9 는 진단 문구 자체를 명세로 정한다. 이 단언은 세 구현이
    서로 일치하는지가 아니라, 오라클이 그 문구를 정확히 내는지를 본다.
    """
    status, out = reference_compile(src)
    assert status != STATUS_OK
    assert out == DIAGNOSTIC_TEXT[name]


# --- 진짜 패리티. cool0c 가 생기면 켜진다 ------------------------------------


@needs_cool0c
@pytest.mark.parametrize("name,src", PROGRAMS, ids=[n for n, _ in PROGRAMS])
def test_bytes_are_identical(name, src):
    assert cool0c_compile(src.encode("ascii")) == reference_compile(src.encode("ascii"))


@needs_cool0c
@pytest.mark.parametrize("name,src", DIAGNOSTICS, ids=[n for n, _ in DIAGNOSTICS])
def test_diagnostics_are_identical(name, src):
    assert cool0c_compile(src) == reference_compile(src)
