"""Simulated device with a register map that emits read/write events.

Registers are reached as ``dev.map.<block>.<field>``; reading or writing a field
calls the user's ``on_read`` / ``on_write`` callbacks, like a real driver would.
"""

from __future__ import annotations

from typing import Any, Callable

Hook = Callable[["Register", Any], None]


class Register:
    def __init__(self, dev: "Device", path: str, reset: Any = 0) -> None:
        self.dev = dev
        self.path = path
        self.value = reset


class Block:
    def __init__(self, dev: "Device", path: str, fields: dict[str, Any]) -> None:
        object.__setattr__(self, "_regs", {n: Register(dev, f"{path}.{n}", v) for n, v in fields.items()})

    def __getattr__(self, name: str) -> Any:
        try:
            reg = self._regs[name]
        except KeyError:
            raise AttributeError(name) from None
        return reg.dev._read(reg)

    def __setattr__(self, name: str, value: Any) -> None:
        reg = self._regs[name]
        reg.dev._write(reg, value)


class RegisterMap:
    def __init__(self, dev: "Device", layout: dict[str, dict[str, Any]]) -> None:
        for block, fields in layout.items():
            setattr(self, block, Block(dev, f"dev.map.{block}", fields))


LAYOUT = {
    "ctrl": {"mode": 0, "enable": 0},
    "dac": {"level": 0},
    "pulse": {"width": 0, "period": 0},
    "status": {"pll_locked": False},
    "adc": {"value": 0},
}


class Device:
    def __init__(self, lock_after: int = 3) -> None:
        self._on_read: list[Hook] = []
        self._on_write: list[Hook] = []
        self._lock_after = lock_after
        self._polls = 0
        self.map = RegisterMap(self, LAYOUT)

    @classmethod
    def simulated(cls, **kw: Any) -> "Device":
        return cls(**kw)

    def on_read(self, hook: Hook) -> None:
        self._on_read.append(hook)

    def on_write(self, hook: Hook) -> None:
        self._on_write.append(hook)

    # Method-style access too, so call sites (not only attribute stores) are covered.
    def write(self, path: str, value: Any) -> None:
        block, field = path.split(".")
        setattr(getattr(self.map, block), field, value)

    def _read(self, reg: Register) -> Any:
        if reg.path == "dev.map.status.pll_locked":
            self._polls += 1
            reg.value = self._polls >= self._lock_after
        if reg.path == "dev.map.adc.value":
            reg.value = self.map.dac._regs["level"].value // 2
        for hook in self._on_read:
            hook(reg, reg.value)
        return reg.value

    def _write(self, reg: Register, value: Any) -> None:
        reg.value = value
        for hook in self._on_write:
            hook(reg, value)
