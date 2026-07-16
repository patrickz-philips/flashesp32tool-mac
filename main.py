#!/usr/bin/env python3
"""
ESP32 Flash Tool — Universal macOS App
Supports: auto-detect, esp32, esp32s2, esp32s3, esp32c3, esp32c6, esp32h2, esp32c2
Built with PyInstaller for Intel + Apple Silicon.
"""
import glob
import io
import logging
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

SUPPORTED_CHIPS = [
    "auto",
    "esp32",
    "esp32s2",
    "esp32s3",
    "esp32c3",
    "esp32c6",
    "esp32h2",
    "esp32c2",
]

BAUD_RATES = ["115200", "230400", "460800", "921600", "1152000", "2000000"]
FLASH_MODES = ["keep", "dio", "qio", "dout", "qout"]
FLASH_FREQS = ["keep", "20m", "26m", "40m", "80m", "120m"]
FLASH_SIZES = ["keep", "256KB", "512KB", "1MB", "2MB", "4MB", "8MB", "16MB"]


class StreamToLog:
    """Redirect stream writes line-by-line to a callback (handles \\r progress bars)."""

    def __init__(self, callback):
        self._cb = callback
        self._buf = ""

    def write(self, text: str) -> None:
        self._buf += text
        while "\n" in self._buf or "\r" in self._buf:
            newline = self._buf.find("\n")
            carriage = self._buf.find("\r")
            indexes = [index for index in (newline, carriage) if index >= 0]
            split_at = min(indexes)
            line, self._buf = self._buf[:split_at], self._buf[split_at + 1:]
            line = line.strip()
            if line:
                self._cb(line)

    def flush(self) -> None:
        line = self._buf.strip()
        if line:
            self._cb(line)
        self._buf = ""


class FirmwareRow:
    """One editable row: [Address] [File Path] […] [✕]."""

    def __init__(self, parent: tk.Widget, addr: str, path: str, on_remove):
        self.frame = ttk.Frame(parent)
        self.addr_var = tk.StringVar(value=addr)
        self.path_var = tk.StringVar(value=path)

        ttk.Entry(self.frame, textvariable=self.addr_var, width=10,
                  font=("Menlo", 12)).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(self.frame, textvariable=self.path_var, width=42).pack(
            side=tk.LEFT, padx=(0, 4), fill=tk.X, expand=True)
        ttk.Button(self.frame, text="…", width=3,
                   command=self._browse).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(self.frame, text="✕", width=3,
                   command=lambda: on_remove(self)).pack(side=tk.LEFT)

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Select firmware file",
            filetypes=[("Binary", "*.bin"), ("All files", "*.*")],
        )
        if path:
            self.path_var.set(path)

    def pack(self, **kwargs) -> None:
        self.frame.pack(**kwargs)

    def destroy(self) -> None:
        self.frame.destroy()

    @property
    def addr(self) -> str:
        return self.addr_var.get().strip()

    @property
    def path(self) -> str:
        return self.path_var.get().strip()


class FlashToolApp(tk.Tk):
    DEFAULT_FIRMWARE = [
        ("0x0",      "bootloader.bin"),
        ("0x8000",   "partitions.bin"),
        ("0xe000",   "boot_app0.bin"),
        ("0x10000",  "firmware.bin"),
    ]

    def __init__(self):
        super().__init__()
        self.title("ESP32 Flash Tool")
        self.geometry("800x720")
        self.minsize(640, 560)
        self.resizable(True, True)
        try:
            ttk.Style(self).theme_use("aqua")
        except Exception:
            pass
        self._rows: list[FirmwareRow] = []
        self._stop_event = threading.Event()
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._build_ui()
        self.after(30, self._drain_ui_queue)
        self.after(50, self.refresh_ports)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 16, "pady": 6}

        # Connection
        cfg = ttk.LabelFrame(self, text="Connection", padding=10)
        cfg.pack(fill=tk.X, **pad)

        ttk.Label(cfg, text="Chip:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        self.chip_var = tk.StringVar(value="esp32s3")
        ttk.Combobox(cfg, textvariable=self.chip_var, width=14, state="readonly",
                     values=SUPPORTED_CHIPS).grid(row=0, column=1, sticky=tk.W, padx=(0, 6))
        ttk.Button(cfg, text="Detect", command=self._detect_chip).grid(
            row=0, column=2, padx=(0, 6))

        ttk.Label(cfg, text="Port:").grid(row=1, column=0, sticky=tk.W, padx=(0, 6), pady=(8, 0))
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(
            cfg, textvariable=self.port_var, width=34, state="readonly")
        self.port_combo.grid(row=1, column=1, columnspan=2, sticky=tk.EW,
                             padx=(0, 6), pady=(8, 0))
        ttk.Button(cfg, text="Refresh", command=self.refresh_ports).grid(
            row=1, column=3, pady=(8, 0))

        ttk.Label(cfg, text="Baud:").grid(row=2, column=0, sticky=tk.W, padx=(0, 6), pady=(8, 0))
        self.baud_var = tk.StringVar(value="921600")
        ttk.Combobox(cfg, textvariable=self.baud_var, width=12, state="readonly",
                     values=BAUD_RATES).grid(row=2, column=1, sticky=tk.W, pady=(8, 0))
        cfg.columnconfigure(1, weight=1)

        # Flash Options
        opt = ttk.LabelFrame(self, text="Flash Options", padding=10)
        opt.pack(fill=tk.X, **pad)

        self.erase_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Erase flash before writing",
                        variable=self.erase_var).grid(
            row=0, column=0, columnspan=6, sticky=tk.W, pady=(0, 6))

        ttk.Label(opt, text="Mode:").grid(row=1, column=0, sticky=tk.W, padx=(0, 4))
        self.flash_mode_var = tk.StringVar(value="keep")
        ttk.Combobox(opt, textvariable=self.flash_mode_var, width=8, state="readonly",
                     values=FLASH_MODES).grid(row=1, column=1, sticky=tk.W, padx=(0, 16))

        ttk.Label(opt, text="Freq:").grid(row=1, column=2, sticky=tk.W, padx=(0, 4))
        self.flash_freq_var = tk.StringVar(value="keep")
        ttk.Combobox(opt, textvariable=self.flash_freq_var, width=8, state="readonly",
                     values=FLASH_FREQS).grid(row=1, column=3, sticky=tk.W, padx=(0, 16))

        ttk.Label(opt, text="Size:").grid(row=1, column=4, sticky=tk.W, padx=(0, 4))
        self.flash_size_var = tk.StringVar(value="keep")
        ttk.Combobox(opt, textvariable=self.flash_size_var, width=8, state="readonly",
                     values=FLASH_SIZES).grid(row=1, column=5, sticky=tk.W)

        # Firmware Files
        fw_outer = ttk.LabelFrame(self, text="Firmware Files", padding=10)
        fw_outer.pack(fill=tk.X, **pad)
        header = ttk.Frame(fw_outer)
        header.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(header, text="Address", width=10, font=("Menlo", 11),
                  foreground="#888888").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(header, text="File Path",
                  foreground="#888888").pack(side=tk.LEFT)
        ttk.Separator(fw_outer, orient="horizontal").pack(fill=tk.X, pady=(0, 6))
        self._fw_container = ttk.Frame(fw_outer)
        self._fw_container.pack(fill=tk.X)
        firmware_entries = self._load_firmware_entries()
        for addr, path in firmware_entries:
            self._add_row(addr, path)
        ttk.Button(fw_outer, text="+ Add Entry",
                   command=lambda: self._add_row("0x0", "")).pack(
            anchor=tk.W, pady=(8, 0))

        # Action bar
        action = ttk.Frame(self)
        action.pack(fill=tk.X, padx=16, pady=(8, 4))
        self.flash_btn = ttk.Button(action, text="▶  Flash", command=self._start_flash)
        self.flash_btn.pack(side=tk.LEFT, ipadx=18, ipady=3)
        self.stop_btn = ttk.Button(action, text="■  Stop", command=self._stop_flash,
                                   state="disabled")
        self.stop_btn.pack(side=tk.LEFT, padx=(6, 0), ipadx=12, ipady=3)
        self.erase_btn = ttk.Button(action, text="Erase All", command=self._start_erase)
        self.erase_btn.pack(side=tk.LEFT, padx=(12, 0), ipadx=10, ipady=3)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(action, textvariable=self.status_var,
                  foreground="#888888").pack(side=tk.LEFT, padx=12)
        self.progress = ttk.Progressbar(action, mode="indeterminate", length=140)
        self.progress.pack(side=tk.RIGHT)

        # Log
        log_frame = ttk.LabelFrame(self, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 16))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, font=("Menlo", 11), bg="#1c1c1e", fg="#e5e5ea",
            insertbackground="white", state="disabled", wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("ok",   foreground="#30d158")
        self.log_text.tag_configure("err",  foreground="#ff453a")
        self.log_text.tag_configure("info", foreground="#64a0ff")
        self.log_text.tag_configure("dim",  foreground="#8e8e93")

    # ── Row management ────────────────────────────────────────────────────────

    def _add_row(self, addr: str, path: str) -> None:
        row = FirmwareRow(self._fw_container, addr, path, self._remove_row)
        row.pack(fill=tk.X, pady=2)
        self._rows.append(row)

    def _remove_row(self, row: FirmwareRow) -> None:
        if len(self._rows) <= 1:
            messagebox.showwarning("Warning", "At least one firmware entry is required.")
            return
        self._rows.remove(row)
        row.destroy()

    # ── Port refresh ──────────────────────────────────────────────────────────

    def refresh_ports(self) -> None:
        ports = sorted(set(glob.glob("/dev/cu.*") + glob.glob("/dev/tty.*")))
        self.port_combo["values"] = ports
        if ports:
            preferred = [p for p in ports if any(
                k in p.lower() for k in ("usb", "uart", "wch", "ch34", "cp21", "serial"))]
            self.port_var.set(preferred[0] if preferred else ports[0])

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _firmware_base_dir(self) -> str:
        if getattr(sys, "frozen", False):
            app_bundle = os.path.dirname(
                os.path.dirname(os.path.dirname(sys.executable)))
            return os.path.dirname(app_bundle)
        return os.path.dirname(os.path.abspath(__file__))

    def _load_firmware_entries(self) -> list[tuple[str, str]]:
        base = self._firmware_base_dir()
        load_path = os.path.join(base, "load.md")
        if os.path.isfile(load_path):
            entries: list[tuple[str, str]] = []
            try:
                with open(load_path, encoding="utf-8-sig") as load_file:
                    for line_number, raw_line in enumerate(load_file, 1):
                        line = raw_line.split("#", 1)[0].strip()
                        if not line:
                            continue
                        parts = line.split()
                        if len(parts) != 2:
                            raise ValueError(
                                f"line {line_number}: expected '<file> <address>'")
                        filename, addr = parts
                        int(addr, 0)
                        path = filename if os.path.isabs(filename) else os.path.join(base, filename)
                        entries.append((addr, path))
                if entries:
                    return entries
            except (OSError, ValueError) as exc:
                messagebox.showwarning(
                    "Invalid load.md",
                    f"Could not load {load_path}:\n{exc}\n\nUsing default entries.",
                )

        entries = []
        for addr, filename in self.DEFAULT_FIRMWARE:
            path = os.path.join(base, filename)
            entries.append((addr, path if os.path.isfile(path) else ""))
        return entries

    def _append_log(self, text: str, tag: str = "") -> None:
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, text + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _busy(self, on: bool) -> None:
        state = "disabled" if on else "normal"
        self.flash_btn.config(state=state)
        self.erase_btn.config(state=state)
        self.stop_btn.config(state="normal" if on else "disabled")
        if on:
            self.progress.start(10)
        else:
            self.progress.stop()

    # ── Thread-safe UI queue ──────────────────────────────────────────────────

    def _ui(self, fn) -> None:
        """Queue a callable for the main thread. Safe from any thread."""
        self._ui_queue.put(fn)

    def _drain_ui_queue(self) -> None:
        """Drain pending UI callbacks every 30 ms on the main thread."""
        try:
            while True:
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(30, self._drain_ui_queue)

    # ── Chip detection ────────────────────────────────────────────────────────

    def _detect_chip(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Please select a serial port first.")
            return
        self._busy(True)
        self._set_status("Detecting chip…")
        threading.Thread(target=self._do_detect, args=(port,), daemon=True).start()

    def _do_detect(self, port: str) -> None:
        args = ["--chip", "auto", "--port", port, "--baud", "115200",
                "--before", "default_reset", "read-mac"]
        detected: list[str] = []

        def log_cb(line: str) -> None:
            self._ui(lambda l=line: self._append_log(l))
            lower = line.lower()
            if "chip is" in lower:
                fragment = lower.split("chip is", 1)[1].strip()
                for known in SUPPORTED_CHIPS[1:]:
                    if known in fragment.replace("-", ""):
                        detected.append(known)
                        break

        try:
            success = self._run_esptool(args, log_cb)
            if detected:
                self._ui(lambda d=detected: self.chip_var.set(d[-1]))
                self._ui(lambda d=detected: self._append_log(
                    f"Detected chip: {d[-1]}", "ok"))
            elif not success:
                self._ui(lambda: self._append_log(
                    "Could not identify chip. Check connection.", "err"))
        except Exception as exc:
            self._ui(lambda e=exc: self._append_log(f"Detection error: {e}", "err"))
        finally:
            self._ui(lambda: self._busy(False))
            self._ui(lambda: self._set_status("Ready"))

    # ── Erase all flash ───────────────────────────────────────────────────────

    def _start_erase(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Please select a serial port.")
            return
        if not messagebox.askyesno(
                "Erase Flash",
                f"Erase ALL flash on device at {port}?\nThis cannot be undone."):
            return
        # Read tkinter vars in main thread
        chip = self.chip_var.get().strip() or "auto"
        baud = self.baud_var.get() or "921600"
        self._busy(True)
        self._set_status("Erasing…")
        self._stop_event = threading.Event()
        threading.Thread(
            target=self._do_erase, args=(port, chip, baud), daemon=True).start()

    def _do_erase(self, port: str, chip: str, baud: str) -> None:
        args = [
            "--chip", chip, "--port", port, "--baud", baud,
            "--before", "default_reset", "--after", "hard_reset",
            "erase-flash",
        ]
        self._ui(lambda a=args: self._append_log("$ esptool " + " ".join(a), "info"))
        self._ui(lambda: self._append_log("─" * 60, "dim"))
        try:
            success = self._run_esptool(args)
            self._ui(lambda: self._append_log("─" * 60, "dim"))
            if success:
                self._ui(lambda: self._append_log("✓ Erase complete!", "ok"))
                self._ui(lambda: self._set_status("Erase complete ✓"))
            else:
                self._ui(lambda: self._append_log("✗ Erase failed.", "err"))
                self._ui(lambda: self._set_status("Erase failed"))
        except Exception as exc:
            self._ui(lambda e=exc: self._append_log(f"✗ {e}", "err"))
            self._ui(lambda: self._set_status("Error"))
        finally:
            self._ui(lambda: self._busy(False))

    # ── Flash ─────────────────────────────────────────────────────────────────

    def _start_flash(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Please select a serial port.")
            return
        fw_args: list[str] = []
        for row in self._rows:
            if not row.addr:
                messagebox.showerror("Error", "One or more entries have an empty address.")
                return
            if not row.path or not os.path.isfile(row.path):
                messagebox.showerror("Error", f"File not found:\n{row.path or '(empty)'}")
                return
            fw_args.extend([row.addr, row.path])
        # Read ALL tkinter vars in main thread (thread-safe)
        flash_cfg = (
            self.baud_var.get() or "921600",
            self.chip_var.get().strip() or "auto",
            self.flash_mode_var.get(),
            self.flash_freq_var.get(),
            self.flash_size_var.get(),
            self.erase_var.get(),
        )
        self._busy(True)
        self._set_status("Flashing…")
        self._stop_event = threading.Event()
        threading.Thread(
            target=self._do_flash,
            args=(port, fw_args, flash_cfg),
            daemon=True,
        ).start()

    def _stop_flash(self) -> None:
        self._stop_event.set()
        self._set_status("Stopping…")
        self.stop_btn.config(state="disabled")

    def _do_flash(self, port: str, fw_args: list[str], flash_cfg: tuple) -> None:
        baud, chip, mode, freq, size, erase = flash_cfg
        try:
            write_flags: list[str] = ["-z"]
            if mode and mode != "keep":
                write_flags += ["--flash-mode", mode]
            if freq and freq != "keep":
                write_flags += ["--flash-freq", freq]
            if size and size != "keep":
                write_flags += ["--flash-size", size]
            if erase:
                write_flags.append("--erase-all")
            esptool_args: list[str] = [
                "--chip", chip, "--port", port, "--baud", baud,
                "--before", "default_reset", "--after", "hard_reset",
            ]
            esptool_args += ["write-flash"] + write_flags + fw_args
            self._ui(lambda a=esptool_args: self._append_log(
                "$ esptool " + " ".join(a), "info"))
            self._ui(lambda: self._append_log("─" * 60, "dim"))
            success = self._run_esptool(esptool_args)
            self._ui(lambda: self._append_log("─" * 60, "dim"))
            if self._stop_event.is_set():
                self._ui(lambda: self._append_log("⚠ Stopped by user.", "err"))
                self._ui(lambda: self._set_status("Stopped"))
            elif success:
                self._ui(lambda: self._append_log("✓ Flash complete!", "ok"))
                self._ui(lambda: self._set_status("Flash complete ✓"))
            else:
                self._ui(lambda: self._append_log(
                    "✗ Flash failed. Check connection and port.", "err"))
                self._ui(lambda: self._set_status("Flash failed"))
        except Exception as exc:
            self._ui(lambda e=exc: self._append_log(f"✗ Unexpected error: {e}", "err"))
            self._ui(lambda: self._set_status("Error"))
        finally:
            self._ui(lambda: self._busy(False))

    # ── esptool runner (in-process, no subprocess) ─────────────────────────

    def _run_esptool(self, esptool_args: list[str],
                     log_callback=None) -> bool:
        """Run esptool in-process (bundled via PyInstaller). Thread-safe."""
        try:
            import esptool
        except ImportError as exc:
            self._ui(lambda e=exc: self._append_log(
                f"✗ Cannot import esptool: {e}", "err"))
            return False

        log_cb = log_callback or (lambda line: self._ui(
            lambda l=line: self._append_log(l)))

        class _StdCapture:
            def __init__(self) -> None:
                self._buf = ""
            def write(self, text: str) -> None:
                self._buf += text
                if "\r" in self._buf:
                    self._buf = self._buf.split("\r")[-1]
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        log_cb(line.strip())
            def flush(self) -> None:
                if self._buf.strip():
                    log_cb(self._buf.strip())
                self._buf = ""
            def fileno(self) -> int:
                raise io.UnsupportedOperation("no fileno")

        class _LogCapture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    log_cb(self.format(record))
                except Exception:
                    pass

        log_handler = _LogCapture()
        log_handler.setFormatter(logging.Formatter("%(message)s"))
        target_loggers = [
            logging.getLogger(),
            logging.getLogger("esptool"),
            logging.getLogger("esptool.cmds"),
            logging.getLogger("esptool.loader"),
            logging.getLogger("esptool.bin_image"),
        ]
        saved_levels: dict[logging.Logger, int] = {}
        for lg in target_loggers:
            saved_levels[lg] = lg.level
            lg.addHandler(log_handler)
            if lg.level == logging.NOTSET or lg.level > logging.DEBUG:
                lg.setLevel(logging.DEBUG)

        capture = _StdCapture()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = capture  # type: ignore[assignment]

        success = False
        try:
            try:
                esptool.main(esptool_args)
            except TypeError:
                saved_argv = sys.argv[:]
                sys.argv = ["esptool"] + esptool_args
                try:
                    esptool.main()
                finally:
                    sys.argv = saved_argv
            success = True
        except SystemExit as exc:
            success = exc.code in (0, None)
        except Exception as exc:
            self._ui(lambda e=exc: self._append_log(
                f"✗ {type(e).__name__}: {e}", "err"))
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            capture.flush()
            for lg in target_loggers:
                lg.removeHandler(log_handler)
                lg.setLevel(saved_levels[lg])

        return success and not self._stop_event.is_set()


def main() -> None:
    app = FlashToolApp()
    app.mainloop()


if __name__ == "__main__":
    main()
