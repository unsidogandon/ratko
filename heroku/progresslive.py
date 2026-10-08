import shutil
import sys
import threading


class StartupLiveDisplay:
    def __init__(self, enabled: bool = True, base_steps: int = 6):
        self.enabled = bool(enabled)
        self.total_steps = base_steps
        self.completed_steps = 0
        self.current_label = "boot"
        self._stage = "boot"
        self._rendered = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
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
        return (text[: width - 3] + "...").ljust(width)

    def _bar(self) -> str:
        width = max(min(self._term_size().columns // 5, 24), 12)
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
        stage = f" | {self._stage}" if self._stage else ""
        self._render_line(
            self._with_hint(
                f"{self._bar()} {percent:>3}% | load {self.current_label}{stage}"
            )
        )

    def _loop(self):
        while not self._stop_event.is_set():
            with self._lock:
                self._spinner_index += 1
                self._render_progress()
            if self._stop_event.wait(0.12):
                break

    def start(self):
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
            self._thread = None
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
            self._rendered = False
            self._last_row = None
            self._occupied_rows.clear()

    def stage(self, label: str, *, advance: bool = False, stage: str | None = None):
        with self._lock:
            self.current_label = label
            self._stage = stage.lower() if stage else ""
            if advance:
                self.completed_steps += 1
            self._render_progress()

    def finalize(self):
        self.stop()
