import contextlib
import os
import select
import shutil
import sys
import threading
import time

with contextlib.suppress(ImportError):
    import termios
    import tty


class StartupLiveDisplay:
    def __init__(self, enabled: bool = True, base_steps: int = 6):
        self.enabled = bool(enabled)
        self.base_steps = base_steps
        self.total_steps = base_steps
        self.completed_steps = 0
        self.module_total = 0
        self.module_completed = 0
        self.current_label = "boot"
        self.recent_files = []
        self.started_at = time.time()
        self._stage = "boot"
        self._mode = "progress"
        self._boot_ping = "n/a"
        self._live_ping = "n/a"
        self._final_username = "unknown"
        self._rendered = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._keyboard_stop_event = threading.Event()
        self._keyboard_thread = None
        self._stdin_fd = None
        self._stdin_attrs = None
        self._spinner_index = 0
        self._spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._hint = "логи сохраняються в ratko.log в корне ратко юзербот"
        self._last_row = None
        self._occupied_rows = set()

    def _term_size(self):
        return shutil.get_terminal_size((100, 24))

    def _fit(self, text: str) -> str:
        width = max(self._term_size().columns - 1, 20)
        if len(text) <= width:
            return text.ljust(width)
        if width <= 3:
            return text[:width]
        return (text[: width - 3] + "...").ljust(width)

    def _bar(self) -> str:
        width = max(min(self._term_size().columns // 5, 24), 12)
        if self._mode == "final":
            return f"[{'=' * width}]"

        ratio = min(self.completed_steps / max(self.total_steps, 1), 0.99)
        filled = min(int(ratio * width), width - 1)
        head = self._spinner[self._spinner_index % len(self._spinner)]
        return f"[{'=' * filled}{head}{' ' * (width - filled - 1)}]"

    def _render_line(self, line: str):
        if not self.enabled:
            return
        rows = max(self._term_size().lines, 2)

        clear_rows = set(self._occupied_rows)
        clear_rows.add(rows)

        if self._last_row is not None:
            clear_rows.update({self._last_row - 1, self._last_row, self._last_row + 1})

        clear_rows.update({rows - 1, rows, rows + 1})

        commands = ["\0337"]
        for row in sorted(r for r in clear_rows if r >= 1):
            commands.append(f"\033[{row};1H\033[2K")

        commands.append(f"\033[{rows};1H{self._fit(line)}\0338")
        sys.stdout.write("".join(commands))
        sys.stdout.flush()
        self._rendered = True
        self._last_row = rows
        self._occupied_rows = {rows}

    def _with_hint(self, text: str) -> str:
        width = max(self._term_size().columns - 1, 20)
        for hint in (self._hint, ""):
            candidate = f"{text} | {hint}" if hint else text
            if len(candidate) <= width:
                return candidate
        return text

    def _render_progress(self):
        percent = min(int((self.completed_steps / max(self.total_steps, 1)) * 100), 99)
        recent = f" | {' • '.join(self.recent_files[-2:])}" if self.recent_files else ""
        stage = f" | {self._stage}" if self._stage else ""
        self._render_line(
            self._with_hint(
                f"{self._bar()} {percent:>3}% | load {self.current_label}{recent}{stage}"
            )
        )

    def _render_final(self):
        uptime_seconds = max(int(time.time() - self.started_at), 0)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        now = time.localtime()
        self._render_line(
            self._with_hint(
                "[ {} {} | {} | uptime {}:{:02d}:{:02d} | ping {} | liveping {} ]".format(
                    time.strftime("%H:%M", now),
                    time.strftime("%d.%m.%y", now),
                    self._final_username,
                    hours,
                    minutes,
                    seconds,
                    self._boot_ping,
                    self._live_ping,
                )
            )
        )

    def _disable(self):
        self.enabled = False
        self._stop_event.set()
        rows = max(self._term_size().lines, 2)
        sys.stdout.write(f"\0337\033[{rows};1H\033[2K\0338")
        sys.stdout.flush()

    def _keyboard_loop(self):
        if not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
            return
        if "termios" not in globals() or "tty" not in globals():
            return
        try:
            self._stdin_fd = sys.stdin.fileno()
            self._stdin_attrs = termios.tcgetattr(self._stdin_fd)
            tty.setcbreak(self._stdin_fd)
        except Exception:
            return
        try:
            while not self._keyboard_stop_event.is_set() and not self._stop_event.is_set():
                try:
                    ready, _, _ = select.select([self._stdin_fd], [], [], 0.2)
                except Exception:
                    break
                if not ready:
                    continue
                try:
                    char = os.read(self._stdin_fd, 1).decode("utf-8", errors="ignore")
                except Exception:
                    break
                if char.lower() == "f":
                    self._disable()
                    break
        finally:
            if self._stdin_fd is not None and self._stdin_attrs is not None:
                with contextlib.suppress(Exception):
                    termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._stdin_attrs)

    def _loop(self):
        while not self._stop_event.is_set():
            with self._lock:
                self._spinner_index += 1
                if self._mode == "final":
                    self._render_final()
                else:
                    self._render_progress()
            time.sleep(1 if self._mode == "final" else 0.12)

    def start(self):
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        # Keyboard shortcut is intentionally disabled.

    def stop(self):
        self._stop_event.set()
        self._keyboard_stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
            self._thread = None
        if self._keyboard_thread is not None:
            self._keyboard_thread.join(timeout=0.2)
            self._keyboard_thread = None
        if self._stdin_fd is not None and self._stdin_attrs is not None:
            with contextlib.suppress(Exception):
                termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._stdin_attrs)
            self._stdin_fd = None
            self._stdin_attrs = None
        if self.enabled and self._rendered:
            rows = max(self._term_size().lines, 2)

            clear_rows = set(self._occupied_rows)
            clear_rows.add(rows)
            if self._last_row is not None:
                clear_rows.update({self._last_row - 1, self._last_row, self._last_row + 1})
            clear_rows.update({rows - 1, rows, rows + 1})

            commands = ["\0337"]
            for row in sorted(r for r in clear_rows if r >= 1):
                commands.append(f"\033[{row};1H\033[2K")
            commands.append("\0338")

            sys.stdout.write("".join(commands))
            sys.stdout.flush()

    def set_module_total(self, count: int):
        with self._lock:
            self.module_total = max(count, 0)
            self.total_steps = self.base_steps + self.module_total
            self._stage = "scan"
            self._render_progress()

    def stage(self, label: str, *, advance: bool = False, stage: str | None = None):
        with self._lock:
            self.current_label = label
            self._stage = stage.lower() if stage else ""
            if advance:
                self.completed_steps += 1
            self._render_progress()

    def module_started(self, filename: str):
        with self._lock:
            self.current_label = f"/{filename}"
            self._stage = f"{self.module_completed + 1}/{self.module_total}" if self.module_total else "mods"
            self._render_progress()

    def module_finished(self, filename: str, ok: bool = True):
        with self._lock:
            self.module_completed += 1
            self.completed_steps += 1
            self.current_label = f"/{filename}"
            self.recent_files.append(filename if ok else f"!{filename}")
            self.recent_files = self.recent_files[-2:]
            self._stage = f"{self.module_completed}/{self.module_total}" if self.module_total else "mods"
            self._render_progress()

    def finalize(self, username: str, live_ping: str | None = None):
        with self._lock:
            boot_ms = max(int((time.time() - self.started_at) * 1000), 1)
            self._mode = "final"
            self._final_username = username
            self._boot_ping = f"{boot_ms}ms" if boot_ms < 1000 else f"{boot_ms / 1000:.2f}s"
            self._live_ping = live_ping or "n/a"
            self._render_final()

    def update_live_ping(self, live_ping: str | None):
        with self._lock:
            if live_ping:
                self._live_ping = live_ping
            if self._mode == "final":
                self._render_final()
