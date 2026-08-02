"""Barrier / gate control.

Only a software simulation is wired up for now. Real hardware (USB relay,
TCP relay board, Arduino on a COM port, ...) can be added later by subclassing
``GateController`` and swapping the instance the UI holds - nothing else has to
change.
"""

from __future__ import annotations

import time
from typing import Callable


class GateController:
    """Interface every barrier backend implements."""

    def open(self, reason: str = "") -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self, reason: str = "") -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def is_open(self, now: float | None = None) -> bool:  # pragma: no cover
        raise NotImplementedError


class SimulatedGate(GateController):
    """Opens a virtual barrier that closes itself after a few seconds."""

    def __init__(
        self,
        open_seconds: float = 4.0,
        on_change: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.open_seconds = max(0.5, float(open_seconds))
        self.on_change = on_change
        self._clock = clock
        self._opened_at: float | None = None
        self.last_reason = ""
        self.last_plate = ""

    def open(self, reason: str = "", plate: str = "") -> None:
        self._opened_at = self._clock()
        self.last_reason = reason
        self.last_plate = plate
        if self.on_change is not None:
            self.on_change()

    def close(self, reason: str = "") -> None:
        self._opened_at = None
        self.last_reason = reason
        if self.on_change is not None:
            self.on_change()

    def is_open(self, now: float | None = None) -> bool:
        if self._opened_at is None:
            return False
        now = self._clock() if now is None else now
        if now - self._opened_at >= self.open_seconds:
            return False
        return True

    def seconds_left(self, now: float | None = None) -> float:
        if self._opened_at is None:
            return 0.0
        now = self._clock() if now is None else now
        return max(0.0, self.open_seconds - (now - self._opened_at))


class TcpRelayGate(GateController):
    """Sends an open pulse to a network relay board over TCP.

    Failures never propagate to the recognition loop; the last error is kept for
    display instead so a dead barrier cannot crash the app.
    """

    def __init__(
        self, host: str, port: int, command: bytes = b"OPEN\n",
        timeout: float = 2.0, sender=None, close_command: bytes = b"CLOSE\n",
    ):
        self.host = host
        self.port = int(port)
        self.command = command
        self.close_command = close_command
        self.timeout = timeout
        self.last_error = ""
        self._sender = sender  # injectable for tests

    def _send(self, command: bytes) -> None:
        if self._sender is not None:
            self._sender(command)
            return
        import socket

        with socket.create_connection((self.host, self.port), timeout=self.timeout) as connection:
            connection.sendall(command)

    def open(self, reason: str = "", plate: str = "") -> None:
        try:
            self._send(self.command)
            self.last_error = ""
        except OSError as exc:  # pragma: no cover - real network path
            self.last_error = str(exc)

    def close(self, reason: str = "") -> None:
        try:
            self._send(self.close_command)
            self.last_error = ""
        except OSError as exc:  # pragma: no cover - real network path
            self.last_error = str(exc)

    def is_open(self, now: float | None = None) -> bool:
        return False


class SerialRelayGate(GateController):
    """Pulses a relay wired to a serial (USB/COM) port."""

    def __init__(
        self, port: str, baudrate: int = 9600, command: bytes = b"OPEN\n",
        writer=None, close_command: bytes = b"CLOSE\n",
    ):
        self.port = port
        self.baudrate = int(baudrate)
        self.command = command
        self.close_command = close_command
        self.last_error = ""
        self._writer = writer  # injectable for tests

    def open(self, reason: str = "", plate: str = "") -> None:
        self._write(self.command)

    def close(self, reason: str = "") -> None:
        self._write(self.close_command)

    def _write(self, command: bytes) -> None:
        try:
            if self._writer is not None:
                self._writer(command)
            else:  # pragma: no cover - real hardware path
                import serial

                with serial.Serial(self.port, self.baudrate, timeout=1) as connection:
                    connection.write(command)
            self.last_error = ""
        except Exception as exc:  # pragma: no cover
            self.last_error = str(exc)

    def is_open(self, now: float | None = None) -> bool:
        return False


class CompositeGate(GateController):
    """Drives the on-screen simulation and any real hardware together.

    Visual state (``is_open``/countdown/last plate) always comes from the
    simulated barrier so the UI keeps animating even if hardware is offline.
    """

    def __init__(self, primary: SimulatedGate, extras: list[GateController] | None = None):
        self.primary = primary
        self.extras = extras or []

    def open(self, reason: str = "", plate: str = "") -> None:
        self.primary.open(reason, plate=plate)
        for controller in self.extras:
            controller.open(reason, plate=plate)

    def close(self, reason: str = "") -> None:
        self.primary.close(reason)
        for controller in self.extras:
            controller.close(reason)

    def is_open(self, now: float | None = None) -> bool:
        return self.primary.is_open(now)

    def seconds_left(self, now: float | None = None) -> float:
        return self.primary.seconds_left(now)

    @property
    def last_plate(self) -> str:
        return self.primary.last_plate

    @property
    def last_reason(self) -> str:
        return self.primary.last_reason


def build_gate(config, on_change=None) -> GateController:
    """Assemble the gate controller described by the app config."""
    simulated = SimulatedGate(open_seconds=config.gate_open_seconds, on_change=on_change)
    backend = getattr(config, "gate_backend", "simulated")
    command = getattr(config, "gate_command", "OPEN").encode("utf-8") + b"\n"
    close_command = getattr(config, "gate_close_command", "CLOSE").encode("utf-8") + b"\n"
    if backend == "tcp" and getattr(config, "gate_host", ""):
        hardware: GateController = TcpRelayGate(
            config.gate_host, config.gate_port, command, close_command=close_command
        )
        return CompositeGate(simulated, [hardware])
    if backend == "serial" and getattr(config, "gate_serial_port", ""):
        hardware = SerialRelayGate(
            config.gate_serial_port, config.gate_baudrate, command,
            close_command=close_command,
        )
        return CompositeGate(simulated, [hardware])
    return simulated
