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
    assert text.endswith("\n"), "진단은 개행으로 끝난다 (implementation.md §9)"
    assert text.count("\n") == 1, "첫 오류에서 멈춘다 (implementation.md §9)"
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
    """어떤 cool0 컴파일러든 implementation.md §7 의 ABI 로 부른다.

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


def run_compiler_n(compiler_wasm: bytes, sources: list[bytes]) -> tuple[int, bytes]:
    """소스 여럿으로 부른다 (gh #5 B, implementation.md §7).

    호스트가 버퍼들을 아무 데나 놓고 `SRCTAB` 에 (ptr, len) 쌍을 적은 뒤
    `compile_n(nsrc)` 을 부른다. 여기서는 0x1000 부터 4 바이트 정렬로 나란히
    놓는다 -- 붙여 놓을 의무는 없다는 것을 보이려고 사이를 띄운다.
    """
    from cool0.cool0 import SRC_ADDR, SRCTAB

    store = wasmtime.Store(ENGINE)
    inst = wasmtime.Instance(store, wasmtime.Module(ENGINE, compiler_wasm), [])
    ex = inst.exports(store)
    mem = ex["memory"]

    at = SRC_ADDR
    for i, src in enumerate(sources):
        mem.write(store, src, at)
        mem.write(store, at.to_bytes(4, "little"), SRCTAB + i * 8)
        mem.write(store, len(src).to_bytes(4, "little"), SRCTAB + i * 8 + 4)
        at = (at + len(src) + 4 + 3) // 4 * 4       # 사이를 띄운다

    status = ex["compile_n"](store, len(sources))
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


# --- cool0c.wat 이 cool0c.cool0 을 따라잡았는가 -------------------------------


def wat_is_current() -> tuple[bool, str]:
    """손으로 쓴 WAT 이 지금의 cool0c.cool0 을 컴파일할 수 있는가.

    언어에 기능을 더하면 세 구현이 한동안 어긋난다 -- 참조 구현과 자기 호스팅
    컴파일러가 먼저 가고 전사는 뒤에 온다. 그동안 WAT 에 기대는 시험이 전부
    실패하면 진짜 회귀가 그 소음에 묻힌다.

    그래서 "전사가 뒤처졌다"를 한 번만 판정하고, 그 시험들을 그 이유로 건너뛴다.
    전사가 끝나면 저절로 다시 켜진다 -- 표시를 지울 일이 없다.
    """
    import pathlib

    src_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "cool0"
    wat, cool0c = src_dir / "cool0c.wat", src_dir / "cool0c.cool0"
    if not wat.exists():
        return False, "cool0c.wat 이 아직 없다"
    from cool0.cool0 import STATUS_OK, compile as _ref

    src = cool0c.read_bytes()
    try:
        a = bytes(wasmtime.wat2wasm(wat.read_text("ascii")))
        status, b = run_compiler(a, src)
    except Exception as e:  # 트랩도 "따라잡지 못했다" 의 한 모습이다
        return False, f"cool0c.wat 이 cool0c.cool0 을 컴파일하지 못한다 ({type(e).__name__})"
    if status != STATUS_OK:
        return False, ("cool0c.wat 이 cool0c.cool0 을 거절한다: "
                       + b.decode("ascii", "replace").strip())
    if b != _ref(src)[1]:
        return False, "cool0c.wat 이 오라클과 다른 바이트를 낸다 -- 전사가 뒤처졌다"
    return True, ""


_WAT_OK, _WAT_WHY = wat_is_current()
needs_current_wat = pytest.mark.skipif(not _WAT_OK, reason=_WAT_WHY or "wat")
