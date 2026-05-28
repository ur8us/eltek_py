# eltek_py

This repository contains `eltek_py.py`, a small Python program for reading live telemetry from Eltek Flatpack2 power supplies over CAN bus and, when explicitly requested, writing the default (stored) voltage setting.

Supported Eltek models:

- `Flatpack2 HE 48/2000 (241115.105)`
- `Flatpack2 series`

## Telemetry

The script logs into the PSU periodically and parses status frames. It prints:

- AC input voltage
- Output voltage
- Output current
- Intake temperature
- Output temperature

## Write Support

The script supports:

- `--set-stored-voltage VOLTS`

When used, the script sends the default-voltage write command (`0x05XX9C00`) to the PSU. The voltage is encoded in centivolts (little-endian) and takes effect when the PSU logs out (approximately 15 seconds after the last login message).

Stored voltage is validated to the range `43.5` to `57.6` V (the Flatpack2 48/2000 HE adjustable range).

Output on/off commands are not implemented. If you run `--set-output on` or `--set-output off`, the script prints a not-implemented message and exits.

## Files

- `eltek_py.py`: main telemetry reader script
- `read.sh`: run the default CANalyst-II telemetry read command
- `on.sh`: placeholder output-on helper that prints a not-implemented message
- `off.sh`: placeholder output-off helper that prints a not-implemented message
- `set-48v.sh`: set default voltage to `48.0` V
- `set-50v.sh`: set default voltage to `50.0` V
- `set-53.5v.sh`: set default voltage to `53.5` V
- `set-58v.sh`: set default voltage to `58.0` V
- `requirements.txt`: Python dependencies
- `99-canalystii.rules`: udev rule for USB access to CANalyst-II adapters on Linux
- `AGENTS.md`: instructions for coding agents working on this repository

## Supported CAN Backends

- `socketcan`: for Linux CAN interfaces such as `can0`
- `canalystii`: for USB adapters like `04d8:0053 Microchip Technology, Inc. Chuangxin Tech USBCAN/CANalyst-II`

## Requirements

- Linux
- Python 3.10 or newer
- CAN bus bitrate set to `125000`
- An Eltek Flatpack2 power supply connected to the CAN bus
- One of:
  - a working SocketCAN interface
  - a CANalyst-II compatible USB adapter

**⚠ Bus grounding warning:** The Flatpack2 CAN bus is referenced to the PSU
negative output rail. Connect CAN ground to PSU negative, **not** to PE/earth,
or you will likely destroy the CAN transceiver.

## Setup

### 1. Install Python dependencies

```bash
python3 -m pip install -r requirements.txt
```

### 2. If you use a CANalyst-II USB adapter

Install the provided udev rule so the adapter can be accessed without running the script as root:

```bash
sudo cp 99-canalystii.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

After that, unplug and reconnect the adapter.

### 3. If you use SocketCAN

Bring the interface up at the correct bitrate:

```bash
sudo ip link set can0 up type can bitrate 125000
```

## Usage

### CANalyst-II

Run on channel 0:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --bitrate 125000
```

If channel 0 is quiet, try channel 1:

```bash
python3 eltek_py.py --backend canalystii --channel 1 --bitrate 125000
```

For a quick probe, run with a timeout:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --bitrate 125000 --timeout 10
```

### SocketCAN

Run with `can0`:

```bash
python3 eltek_py.py can0
```

## Commands

These commands change PSU behavior. Use them carefully.

Output off is not implemented:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --bitrate 125000 --set-output off --timeout 3
```

Output on is not implemented:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --bitrate 125000 --set-output on --timeout 3
```

Set the default (stored) voltage:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --bitrate 125000 --set-stored-voltage 53.5
```

With a short receive window for raw debugging:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --bitrate 125000 --set-stored-voltage 53.5 --timeout 1 --raw
```

## Helper Scripts

Run the default telemetry reader:

```bash
./read.sh
```

Set the default voltage to `48.0` V:

```bash
./set-48v.sh
```

Set the default voltage to `50.0` V:

```bash
./set-50v.sh
```

Set the default voltage to `53.5` V:

```bash
./set-53.5v.sh
```

`on.sh` and `off.sh` now print the same not-implemented message as `--set-output`.

## Useful Options

Show help:

```bash
python3 eltek_py.py --help
```

Poll once per second:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --interval 1.0
```

Stop after 10 seconds:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --timeout 10
```

Print every raw CAN frame:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --raw
```

Record undecoded frames:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --unknown
```

Specify the PSU ID (default: 1):

```bash
python3 eltek_py.py --backend canalystii --channel 0 --psu-id 2
```

## Example Output

```text
AC Input: 230.00 V
Output:   53.40 V  21.20 A
Intake:   25.00 C  Output: 35.00 C
```

## Troubleshooting

### `Cannot find device "can0"`

Your adapter is not exposed as a SocketCAN interface. Use:

```bash
python3 eltek_py.py --backend canalystii --channel 0 --bitrate 125000
```

### `Access denied (insufficient permissions)`

The USB device permissions are too restrictive. Install `99-canalystii.rules`, reload udev, and reconnect the adapter.

### No frames received

- Confirm the PSU is powered
- Confirm CAN H and CAN L are wired correctly, with CAN ground connected to PSU negative output
- Confirm the bitrate is `125000`
- Try the other CANalyst-II channel
- Use `--raw --unknown` to inspect bus traffic
