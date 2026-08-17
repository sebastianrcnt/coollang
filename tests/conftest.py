"""테스트 공용 도구.

cool0 컴파일러는 순수 함수라 검사가 전부 바이트 비교거나 wasmtime 실행이다.
wat2wasm 바이너리는 필요 없다 -- wasmtime 파이썬 패키지가 WAT 파서를 갖고 있다.
"""

from __future__ import annotations

import pytest
import wasmtime

from cool0.cool0 import STATUS_ERR, STATUS_OK, compile_text

ENGINE = wasmtime.Engine()


def compile_ok(src: str) -> bytes:
    """컴파일이 성공해야 한다. wasm 바이트를 돌려준다."""
    status, out = compile_text(src)
    if status != STATUS_OK:
        pytest.fail("expected success, got diagnostic: " + out.decode("ascii").strip())
    return out


def compile_err(src: str) -> str:
    """컴파일이 실패해야 한다. `줄:칸: 문구` 한 줄을 돌려준다."""
    status, out = compile_text(src)
    if status != STATUS_ERR:
        pytest.fail("expected a diagnostic, but compilation succeeded")
    text = out.decode("ascii")
    assert text.endswith("\n"), "진단은 개행으로 끝난다 (§12)"
    assert text.count("\n") == 1, "첫 오류에서 멈춘다 (§12)"
    return text.strip()


def validate(wasm: bytes) -> wasmtime.Module:
    """wasm 검증기를 통과해야 한다."""
    return wasmtime.Module(ENGINE, wasm)


class Instance:
    """인스턴스 하나와 그 export 들."""

    def __init__(self, wasm: bytes):
        self.wasm = wasm
        self.store = wasmtime.Store(ENGINE)
        self.module = wasmtime.Module(ENGINE, wasm)
        self.instance = wasmtime.Instance(self.store, self.module, [])
        self.exports = self.instance.exports(self.store)

    def call(self, name: str, *args):
        return self.exports[name](self.store, *args)

    @property
    def memory(self) -> wasmtime.Memory:
        return self.exports["memory"]

    def read(self, addr: int, length: int) -> bytes:
        return bytes(self.memory.read(self.store, addr, addr + length))

    def write(self, addr: int, data: bytes):
        self.memory.write(self.store, data, addr)


def instantiate(src: str) -> Instance:
    return Instance(compile_ok(src))


def run_compiler(compiler_wasm: bytes, src: bytes) -> tuple[int, bytes]:
    """어떤 cool0 컴파일러든 §8 의 ABI 로 부른다.

    호스트가 하는 일이 이것뿐이다 -- 소스를 0x1000 에 놓고, `compile(src_len)` 을
    부르고, `out_ptr`/`out_len` 이 가리키는 바이트를 꺼낸다. 파일도 인자도 없다.
    """
    from cool0.cool0 import SRC_ADDR

    store = wasmtime.Store(ENGINE)
    inst = wasmtime.Instance(store, wasmtime.Module(ENGINE, compiler_wasm), [])
    ex = inst.exports(store)
    mem = ex["memory"]
    mem.write(store, src, SRC_ADDR)
    status = ex["compile"](store, len(src))
    ptr = int.from_bytes(bytes(mem.read(store, 0, 4)), "little")
    length = int.from_bytes(bytes(mem.read(store, 4, 8)), "little")
    return status, bytes(mem.read(store, ptr, ptr + length))


def run(src: str, fn: str, *args):
    """컴파일하고, 인스턴스를 만들고, 함수 하나를 부른다."""
    return instantiate(src).call(fn, *args)


def traps(inst: Instance, fn: str, *args) -> bool:
    try:
        inst.call(fn, *args)
        return False
    except wasmtime.Trap:
        return True
