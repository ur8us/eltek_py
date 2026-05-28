#!/usr/bin/env python3
"""Read telemetry from Eltek Flatpack2 power supplies over CAN.

Supported models:
- Flatpack2 HE 48/2000 (241115.105)
- Flatpack2 series

Protocol reference:
https://github.com/the6p4c/Flatpack2/blob/master/Protocol.md
"""

from __future__ import annotations

import argparse
import select
import signal
import socket
import struct
import sys
import time
from dataclasses import dataclass, field


CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_EFF_MASK = 0x1FFFFFFF
CAN_FRAME_FORMAT = "=IB3x8s"
CAN_FRAME_SIZE = struct.calcsize(CAN_FRAME_FORMAT)

# Eltek Flatpack2 CAN ID matching
# Status:  0x05XX40YY (XX=PSU ID, YY=state)
ELTEK_STATUS_MASK = 0xFF00FF00
ELTEK_STATUS_PATTERN = 0x05004000
# Login request (PSU -> bus): 0x05XX4400
ELTEK_LOGIN_PLEASE_MASK = 0xFF00FF00
ELTEK_LOGIN_PLEASE_PATTERN = 0x05004400
# CAN intro: 0x0500XXXX
ELTEK_INTRO_MASK = 0xFFFF0000
ELTEK_INTRO_PATTERN = 0x05000000

# Login TX:      0x050048 | (psu_id * 4)
# Set voltage:   0x05 | (psu_id << 16) | 0x9C00


STORED_VOLTAGE_MIN_V = 43.5
STORED_VOLTAGE_MAX_V = 57.6

SUPPORTED_MODELS_TEXT = "Eltek Flatpack2"


@dataclass
class PowerSupplyState:
    intake_temperature_c: float | None = None
    output_voltage_v: float | None = None
    output_current_a: float | None = None
    input_voltage_v: float | None = None
    output_temperature_c: float | None = None
    psu_id: int | None = None
    serial_number: bytes | None = None
    cycle_has_data: bool = False
    unknown_frames: dict[int, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class WriteRequest:
    description: str
    can_id: int
    payload: bytes


def open_can_socket(interface: str) -> socket.socket:
    sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    sock.bind((interface,))
    return sock


def pack_frame(can_id: int, data: bytes) -> bytes:
    payload = data.ljust(8, b"\x00")
    return struct.pack(CAN_FRAME_FORMAT, can_id | CAN_EFF_FLAG, len(data), payload)


def unpack_frame(frame: bytes) -> tuple[int, bool, bool, bytes]:
    raw_can_id, can_dlc, payload = struct.unpack(CAN_FRAME_FORMAT, frame)
    return (
        raw_can_id & CAN_EFF_MASK,
        bool(raw_can_id & CAN_EFF_FLAG),
        bool(raw_can_id & CAN_RTR_FLAG),
        payload[:can_dlc],
    )


def send_frame(sock: socket.socket, can_id: int, data: bytes) -> None:
    sock.send(pack_frame(can_id, data))


def open_python_can_bus(backend: str, channel: int, device: int, bitrate: int):
    try:
        import can
    except ImportError as exc:
        raise RuntimeError(
            "python-can is not installed. For this adapter use: "
            'python3 -m pip install "python-can[canalystii]"'
        ) from exc

    try:
        return can.Bus(
            interface=backend,
            channel=channel,
            device=device,
            bitrate=bitrate,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to open {backend} channel {channel}: {exc}\n"
            "If this is a USB permission error, install the udev rule from "
            "`99-canalystii.rules` and reconnect the adapter."
        ) from exc


def send_frame_python_can(bus, can_id: int, data: bytes) -> None:
    import can

    bus.send(
        can.Message(
            arbitration_id=can_id,
            is_extended_id=True,
            data=data,
        )
    )


def close_python_can_bus(bus) -> None:
    shutdown = getattr(bus, "shutdown", None)
    if callable(shutdown):
        shutdown()
        return

    close = getattr(bus, "close", None)
    if callable(close):
        close()


def login_can_id(psu_id: int) -> int:
    return 0x05004800 | (psu_id * 4)


def set_voltage_can_id(psu_id: int) -> int:
    return 0x05009C00 | (psu_id << 16)


def make_login_frame(serial: bytes) -> bytes:
    return serial.ljust(8, b"\x00")[:8]


def make_set_voltage_payload(voltage_v: float) -> bytes:
    centivolts = int(round(voltage_v * 100.0))
    return bytes([0x29, 0x15, 0x00, centivolts & 0xFF, (centivolts >> 8) & 0xFF])


def validate_stored_voltage(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid voltage: {text!r}") from exc

    if not STORED_VOLTAGE_MIN_V <= value <= STORED_VOLTAGE_MAX_V:
        raise argparse.ArgumentTypeError(
            "stored voltage must be between "
            f"{STORED_VOLTAGE_MIN_V:.1f} V and {STORED_VOLTAGE_MAX_V:.1f} V"
        )
    return value


def build_write_requests(args: argparse.Namespace) -> list[WriteRequest]:
    requests: list[WriteRequest] = []
    if args.set_stored_voltage is not None:
        can_id = set_voltage_can_id(args.psu_id)
        payload = make_set_voltage_payload(args.set_stored_voltage)
        requests.append(WriteRequest(
            description=f"default voltage -> {args.set_stored_voltage:.2f} V",
            can_id=can_id,
            payload=payload,
        ))
    return requests


def handle_frame(
    state: PowerSupplyState,
    can_id: int,
    is_extended: bool,
    is_remote: bool,
    data: bytes,
    show_raw: bool,
    show_unknown: bool,
) -> bool:
    if show_raw:
        hex_data = " ".join(f"{byte:02X}" for byte in data)
        print(f"0x{can_id:08X} [{len(data)}] {hex_data}")

    if not is_extended or is_remote:
        return False

    # Status frame: 0x05XX40YY
    if (can_id & ELTEK_STATUS_MASK) == ELTEK_STATUS_PATTERN and len(data) >= 8:
        psu_id = (can_id >> 16) & 0xFF
        state.psu_id = psu_id

        state.intake_temperature_c = float(struct.unpack("b", bytes([data[0]]))[0])
        current_raw = struct.unpack("<H", data[1:3])[0]
        state.output_current_a = current_raw / 10.0
        voltage_raw = struct.unpack("<H", data[3:5])[0]
        state.output_voltage_v = voltage_raw / 100.0
        input_raw = struct.unpack("<H", data[5:7])[0]
        state.input_voltage_v = float(input_raw)
        state.output_temperature_c = float(struct.unpack("b", bytes([data[7]]))[0])
        state.cycle_has_data = True
        return True

    # Login request: 0x05XX4400
    if (can_id & ELTEK_LOGIN_PLEASE_MASK) == ELTEK_LOGIN_PLEASE_PATTERN and len(data) >= 6:
        psu_id = (can_id >> 16) & 0xFF
        state.psu_id = psu_id
        state.serial_number = data[0:4]
        return False

    # CAN bus intro: 0x0500XXXX
    if (can_id & ELTEK_INTRO_MASK) == ELTEK_INTRO_PATTERN and len(data) >= 7:
        state.serial_number = data[1:7]
        return False

    if show_unknown:
        state.unknown_frames[can_id] = data
    return False


def format_value(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f} {unit}"


def print_summary(state: PowerSupplyState) -> None:
    lines = [
        f"AC Input: {format_value(state.input_voltage_v, 'V')}",
        f"Output:   {format_value(state.output_voltage_v, 'V')}  "
        f"{format_value(state.output_current_a, 'A')}",
        f"Intake:   {format_value(state.intake_temperature_c, 'C')}  "
        f"Output: {format_value(state.output_temperature_c, 'C')}",
    ]
    print("\n".join(lines))
    print()


def print_unknown_frames(state: PowerSupplyState) -> None:
    if not state.unknown_frames:
        return

    print("Unknown frames seen:")
    for can_id in sorted(state.unknown_frames):
        data = state.unknown_frames[can_id]
        hex_data = " ".join(f"{byte:02X}" for byte in data)
        print(f"  0x{can_id:08X} [{len(data)}] {hex_data}")


def send_login(sock: socket.socket, state: PowerSupplyState) -> None:
    if state.serial_number is not None and state.psu_id is not None:
        can_id = login_can_id(state.psu_id)
        payload = make_login_frame(state.serial_number)
        send_frame(sock, can_id, payload)


def send_login_python_can(bus, state: PowerSupplyState) -> None:
    if state.serial_number is not None and state.psu_id is not None:
        can_id = login_can_id(state.psu_id)
        payload = make_login_frame(state.serial_number)
        send_frame_python_can(bus, can_id, payload)


def wait_for_socketcan_frames(
    sock: socket.socket,
    state: PowerSupplyState,
    *,
    show_raw: bool,
    show_unknown: bool,
    duration: float,
) -> None:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        timeout = max(0.0, deadline - time.monotonic())
        readable, _, _ = select.select([sock], [], [], min(0.2, timeout))
        if not readable:
            continue

        frame = sock.recv(CAN_FRAME_SIZE)
        can_id, is_extended, is_remote, data = unpack_frame(frame)
        handle_frame(state, can_id, is_extended, is_remote, data, show_raw, show_unknown)


def wait_for_python_can_frames(
    bus,
    state: PowerSupplyState,
    *,
    show_raw: bool,
    show_unknown: bool,
    duration: float,
) -> None:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        timeout = min(0.2, max(0.0, deadline - time.monotonic()))
        msg = bus.recv(timeout=timeout)
        if msg is None:
            continue

        handle_frame(
            state,
            msg.arbitration_id,
            msg.is_extended_id,
            msg.is_remote_frame,
            bytes(msg.data),
            show_raw,
            show_unknown,
        )


SAFETY_NOTICE = (
    "WARNING: The Flatpack2 CAN bus is referenced to the PSU negative output rail.\n"
    "Connect CAN ground to PSU negative, NOT to PE/earth, or you will\n"
    "likely destroy the CAN transceiver."
)


def main() -> int:
    print(SAFETY_NOTICE, file=sys.stderr)
    print(file=sys.stderr)

    parser = argparse.ArgumentParser(
        description=(
            "Read telemetry from Eltek Flatpack2 power supplies over CAN "
            f"({SUPPORTED_MODELS_TEXT}) and optionally set the default output "
            "voltage. Output on/off commands are not implemented."
        )
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="can0",
        help="SocketCAN interface name when using socketcan, default: can0",
    )
    parser.add_argument(
        "--backend",
        choices=("socketcan", "canalystii"),
        default="socketcan",
        help="CAN backend, default: socketcan",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=0,
        help="CANalyst-II channel number, default: 0",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=0,
        help="CANalyst-II USB device index, default: 0",
    )
    parser.add_argument(
        "--bitrate",
        type=int,
        default=125000,
        help="CAN bitrate in bit/s, default: 125000",
    )
    parser.add_argument(
        "--psu-id",
        type=int,
        default=1,
        help="PSU ID (1-63), default: 1",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds, default: 1.0",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Stop after this many seconds",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print every received CAN frame",
    )
    parser.add_argument(
        "--unknown",
        action="store_true",
        help="Remember and print non-decoded frame IDs on exit",
    )
    parser.add_argument(
        "--set-output",
        choices=("on", "off"),
        help="Not implemented: print a message and exit",
    )
    parser.add_argument(
        "--set-stored-voltage",
        type=validate_stored_voltage,
        metavar="VOLTS",
        help=(
            "Set the default (stored) output voltage "
            f"({STORED_VOLTAGE_MIN_V:.1f}-{STORED_VOLTAGE_MAX_V:.1f} V)"
        ),
    )
    args = parser.parse_args()

    state = PowerSupplyState(psu_id=args.psu_id)
    if args.set_output is not None:
        print(
            f"Output {args.set_output} commands are not implemented for Eltek power supplies.",
            file=sys.stderr,
        )
        if args.set_stored_voltage is None:
            return 1

    write_requests = build_write_requests(args)
    write_only = bool(write_requests)
    deadline = time.monotonic() + args.timeout if args.timeout else None
    next_login = 0.0
    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    if args.backend == "socketcan":
        try:
            sock = open_can_socket(args.target)
        except OSError as exc:
            print(
                f"Failed to open SocketCAN interface {args.target!r}: {exc}",
                file=sys.stderr,
            )
            print(
                "Either use a real SocketCAN interface, or install python-can and "
                f"run `python3 {sys.argv[0]} --backend canalystii --channel 0`.",
                file=sys.stderr,
            )
            return 1

        try:
            # Listen for PSU to introduce itself before writing.
            if write_only and state.serial_number is None:
                wait_for_socketcan_frames(
                    sock, state, show_raw=args.raw, show_unknown=args.unknown, duration=2.0
                )
                if state.serial_number is not None:
                    send_login(sock, state)
                    time.sleep(0.1)

            for request in write_requests:
                try:
                    send_frame(sock, request.can_id, request.payload)
                except OSError as exc:
                    print(
                        f"Failed to send {request.description}: {exc}",
                        file=sys.stderr,
                    )
                    return 1
                print(f"Sent {request.description}")

            if write_only:
                receive_window = args.timeout or (0.2 if (args.raw or args.unknown) else 0.0)
                if receive_window > 0.0:
                    wait_for_socketcan_frames(
                        sock,
                        state,
                        show_raw=args.raw,
                        show_unknown=args.unknown,
                        duration=receive_window,
                    )
                if args.unknown:
                    print_unknown_frames(state)
                return 0

            while running:
                now = time.monotonic()
                if now >= next_login:
                    if state.serial_number is not None:
                        try:
                            send_login(sock, state)
                        except OSError as exc:
                            print(f"Failed to send login: {exc}", file=sys.stderr)
                            return 1
                    next_login = now + args.interval

                if deadline and now >= deadline:
                    break

                timeout = 0.2
                if deadline:
                    timeout = min(timeout, max(0.0, deadline - now))

                readable, _, _ = select.select([sock], [], [], timeout)
                if not readable:
                    continue

                frame = sock.recv(CAN_FRAME_SIZE)
                can_id, is_extended, is_remote, data = unpack_frame(frame)
                if handle_frame(
                    state,
                    can_id,
                    is_extended,
                    is_remote,
                    data,
                    args.raw,
                    args.unknown,
                ):
                    print_summary(state)
        finally:
            sock.close()
    else:
        try:
            bus = open_python_can_bus(
                args.backend,
                channel=args.channel,
                device=args.device,
                bitrate=args.bitrate,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            print(
                "This adapter is expected to work through `--backend canalystii`.",
                file=sys.stderr,
            )
            return 1

        try:
            if write_only and state.serial_number is None:
                wait_for_python_can_frames(
                    bus, state, show_raw=args.raw, show_unknown=args.unknown, duration=2.0
                )
                if state.serial_number is not None:
                    send_login_python_can(bus, state)
                    time.sleep(0.1)

            for request in write_requests:
                try:
                    send_frame_python_can(bus, request.can_id, request.payload)
                except Exception as exc:
                    print(
                        f"Failed to send {request.description}: {exc}",
                        file=sys.stderr,
                    )
                    return 1
                print(f"Sent {request.description}")

            if write_only:
                receive_window = args.timeout or (0.2 if (args.raw or args.unknown) else 0.0)
                if receive_window > 0.0:
                    wait_for_python_can_frames(
                        bus,
                        state,
                        show_raw=args.raw,
                        show_unknown=args.unknown,
                        duration=receive_window,
                    )
                if args.unknown:
                    print_unknown_frames(state)
                return 0

            while running:
                now = time.monotonic()
                if now >= next_login:
                    if state.serial_number is not None:
                        try:
                            send_login_python_can(bus, state)
                        except Exception as exc:
                            print(f"Failed to send login: {exc}", file=sys.stderr)
                            return 1
                    next_login = now + args.interval

                if deadline and now >= deadline:
                    break

                timeout = 0.2
                if deadline:
                    timeout = min(timeout, max(0.0, deadline - now))

                msg = bus.recv(timeout=timeout)
                if msg is None:
                    continue

                if handle_frame(
                    state,
                    msg.arbitration_id,
                    msg.is_extended_id,
                    msg.is_remote_frame,
                    bytes(msg.data),
                    args.raw,
                    args.unknown,
                ):
                    print_summary(state)
        finally:
            close_python_can_bus(bus)

    if args.unknown:
        print_unknown_frames(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
