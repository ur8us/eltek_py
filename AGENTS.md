# AGENTS.md

## Project Purpose

This repository contains `eltek_py.py`, a small Python utility for reading telemetry from Eltek Flatpack2 power supplies over CAN bus.

Supported models:

- `Flatpack2 HE 48/2000`
- `Flatpack2 series`

The implementation reads telemetry from status frames broadcast by the PSU after login. It supports stored-voltage writes when explicitly requested on the command line. Output on/off is not implemented; `--set-output` only prints a not-implemented message. Do not add further write or control commands unless explicitly requested by the user.

## Protocol Reference

The Eltek Flatpack2 CAN protocol:

- CAN speed: 125 kbit/s
- CAN ID type: extended 29-bit
- Reference ground: PSU negative output rail

### Login TX: `0x050048XX`
Where XX = PSU ID * 4. Payload is 6-byte serial number + 0x00 padding.

### Login Request (RX): `0x05XX4400`
PSU introduces itself every ~15 seconds. XX = PSU ID. Payload bytes 0-3 = serial number.

### CAN Bus Intro (RX): `0x0500XXXX`
Sent every ~2 seconds. XXXX = last 4 digits of serial. Payload: byte 0 = 0x1B, bytes 1-6 = serial number.

### Status Frame (RX): `0x05XX40YY`
Sent while PSU is logged in.
- XX = PSU ID
- YY = state: 0x04=normal, 0x08=warning, 0x0C=alarm, 0x10=walk in
- Byte 0: intake temperature (int8, °C)
- Bytes 1-2: current (uint16 LE, deciamps)
- Bytes 3-4: output voltage (uint16 LE, centivolts)
- Bytes 5-6: AC input voltage (uint16 LE, volts)
- Byte 7: output temperature (int8, °C)

### Set Default Voltage TX: `0x05XX9C00`
XX = PSU ID. Payload: [0x29, 0x15, 0x00, centivolts_L, centivolts_H]. Voltage takes effect after logout.

## Primary Files

- `eltek_py.py`: main program
- `read.sh`: helper script for the default read command
- `on.sh`: placeholder helper script for output on
- `off.sh`: placeholder helper script for output off
- `set-48v.sh`: one-shot helper script for stored voltage `48.0` V
- `set-50v.sh`: one-shot helper script for stored voltage `50.0` V
- `set-53.5v.sh`: one-shot helper script for stored voltage `53.5` V
- `README.md`: GitHub-facing documentation and setup instructions
- `requirements.txt`: Python dependencies
- `99-canalystii.rules`: Linux udev rule for CANalyst-II USB adapter access

## Local Reference

Use the GitHub protocol reference as the implementation reference:

- `https://github.com/the6p4c/Flatpack2/blob/master/Protocol.md`
- `https://github.com/the6p4c/Flatpack2/blob/master/Arduino/fp2_control/fp2_control.ino`
- `https://github.com/the6p4c/Flatpack2/blob/master/Arduino/fp2_set_voltage/fp2_set_voltage.ino`

Do not invent frame IDs, payload layouts, scaling factors, or message meanings when the reference can be checked.

## Working Rules

- Keep the script Eltek Flatpack2 only.
- Treat the Flatpack2 series as the supported model set unless the user explicitly expands that scope.
- Preserve the current limited-write behavior unless the user explicitly asks for additional CAN write support.
- Keep `--set-output` unimplemented unless the user explicitly asks for on/off support again and provides a source-backed Eltek command or asks for another round of protocol research.
- Prefer small, direct changes over large refactors.
- Keep the script dependency-light.
- Support both backends already implemented:
  - `socketcan`
  - `canalystii`
- Assume Linux as the target platform.
- Keep setup instructions in `README.md` current when behavior changes.
- If adapter-specific permissions are relevant, keep `99-canalystii.rules` in sync with the documentation.

## Code Style

- Use straightforward Python.
- Avoid unnecessary abstraction.
- Keep output human-readable for terminal use.
- Add comments only when they clarify protocol handling or a non-obvious implementation detail.

## Validation

After code changes, at minimum run:

```bash
python3 -m py_compile eltek_py.py
python3 eltek_py.py --help
```

If hardware access is available, also test against the actual CAN adapter and PSU.

## Safety

- Treat CAN write operations as potentially dangerous.
- Do not add commands beyond the current stored-voltage support and placeholder output on/off command without explicit user approval.
- If a requested change could alter PSU behavior, call that out clearly.
- The Flatpack2 CAN bus is referenced to the PSU negative output rail. Connect CAN ground to PSU negative, not to PE/earth, to avoid damaging the CAN transceiver.
