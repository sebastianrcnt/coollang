"""패리티 (SPEC.md §9).

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
from corpus import DIAGNOSTICS, PROGRAMS
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
    """§8 의 ABI 그대로 호출한다."""
    return run_compiler(WASM, src)


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


# --- 진짜 패리티. cool0c 가 생기면 켜진다 ------------------------------------


@needs_cool0c
@pytest.mark.parametrize("name,src", PROGRAMS, ids=[n for n, _ in PROGRAMS])
def test_bytes_are_identical(name, src):
    assert cool0c_compile(src.encode("ascii")) == reference_compile(src.encode("ascii"))


@needs_cool0c
@pytest.mark.parametrize("name,src", DIAGNOSTICS, ids=[n for n, _ in DIAGNOSTICS])
def test_diagnostics_are_identical(name, src):
    assert cool0c_compile(src) == reference_compile(src)
