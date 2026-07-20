# FlashESP32

A lightweight, native macOS GUI for flashing firmware onto ESP32 chips. Built with
Python + Tkinter, it wraps [`esptool`](https://github.com/espressif/esptool)
in-process (no shelling out) and can be packaged into a standalone,
double‑clickable `FlashESP32.app` that runs on both Intel and Apple Silicon Macs.

## Features

- **Universal chip support** — `esp32`, `esp32s2`, `esp32s3`, `esp32c3`,
  `esp32c6`, `esp32h2`, `esp32c2`, plus `auto` detection.
- **One‑click chip detection** — probes the connected device and selects the
  matching chip type automatically.
- **Serial port auto‑discovery** — scans `/dev/cu.*` and `/dev/tty.*` and
  prefers USB/UART adapters (WCH, CH34x, CP21x, …).
- **Multi‑file flashing** — add any number of `address → file` entries, edit them
  inline, and browse for `.bin` files.
- **Flash options** — configurable baud rate, flash mode, frequency, size, and an
  optional "erase before write".
- **Erase all flash** — wipe the entire flash with a confirmation prompt.
- **Live log** — real‑time, color‑coded output streamed straight from `esptool`,
  including progress bars.
- **Stop button** — cancel a running flash operation.
- **Universal2 build** — the packaged `.app` bundles a Universal2 Python so it
  runs natively on Intel and Apple Silicon (down to macOS Catalina 10.15).

## Requirements

- macOS 10.15 (Catalina) or newer
- Python 3.9+ (with Tkinter) — only needed to run from source or to build the app
- [`esptool`](https://pypi.org/project/esptool/) — bundled into the app; required
  in your environment when running from source

## Running from source

```bash
cd flashesp32
python3 -m venv venv
source venv/bin/activate
pip install esptool
python main.py
```

## Usage

1. **Connect** your ESP32 board via USB.
2. Pick the **Chip** (or click **Detect**) and the **Port** (click **Refresh** to
   rescan).
3. Choose a **Baud** rate and, if needed, adjust the flash **Mode / Freq / Size**.
4. In **Firmware Files**, set the flash **Address** and **File Path** for each
   binary. Use **+ Add Entry** to add more rows and **✕** to remove one.
5. Click **▶ Flash**. Watch progress in the **Log** panel; use **■ Stop** to
   cancel or **Erase All** to wipe the chip.

### Default firmware layout

If no `load.md` is present, the tool pre‑fills the standard Arduino/ESP‑IDF
layout:

| Address   | File               |
| --------- | ------------------ |
| `0x0`     | `bootloader.bin`   |
| `0x8000`  | `partitions.bin`   |
| `0xe000`  | `boot_app0.bin`    |
| `0x10000` | `firmware.bin`     |

### Customizing entries with `load.md`

Place a `load.md` file next to `main.py` (or next to the `.app` bundle) to define
the firmware entries. One entry per line, in `<file> <address>` order. `#` starts
a comment. Relative paths are resolved against the `load.md` directory.

```text
# <file>            <address>
bootloader.bin      0x0
partitions.bin      0x8000
boot_app0.bin       0xe000
firmware.bin        0x10000
```

## Building the macOS app

```bash
cd flashesp32
bash build_app.sh
```

The result is `dist/FlashESP32.app`.

The build script builds a Universal2 app so it runs natively on both Intel and
Apple Silicon. It looks for a Python that contains the Intel `x86_64` slice in
this order:

1. A portable Python previously provisioned into `.toolchain/`
2. A python.org Framework Python (usually Universal2)
3. Homebrew Intel Python at `/usr/local/bin/python3`
4. Whatever `python3` is on your `PATH`

By default the build runs **offline** using the bundled `venv` and `.toolchain`.
If no suitable Python or build dependency is found, allow network access to
download a Universal2 Python and install the dependencies:

```bash
ALLOW_NETWORK=1 bash build_app.sh
```

You can also point the script at a specific interpreter:

```bash
PYTHON_BIN=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 bash build_app.sh
```

## Project structure

```text
flashesp32/
├── main.py            # Tkinter GUI + in-process esptool runner
├── build_app.sh       # Universal2 .app build script (PyInstaller)
├── FlashESP32.spec    # PyInstaller spec
├── load.md            # (optional) custom firmware entries
├── venv/              # bundled build/runtime dependencies
└── dist/              # build output (FlashESP32.app)
```

## License

See the repository for license details.
