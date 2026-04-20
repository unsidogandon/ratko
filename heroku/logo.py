import os
import shutil
import sys


RATKO_ART = [
    r":::::::::      ::: ::::::::::: :::    ::: :::::::: ",
    r":+:    :+:   :+: :+:   :+:     :+:   :+: :+:    :+: ",
    r"+:+    +:+  +:+   +:+  +:+     +:+  +:+  +:+    +:+ ",
    r"+#++:++#:  +#++:++#++: +#+     +#++:++   +#+    +:+ ",
    r"+#+    +#+ +#+     +#+ +#+     +#+  +#+  +#+    +#+ ",
    r"#+#    #+# #+#     #+# #+#     #+#   #+# #+#    #+# ",
    r"###    ### ###     ### ###     ###    ### ########  ",

]


def _supports_truecolor() -> bool:
    if os.environ.get("NO_COLOR"):
        return False

    term = os.environ.get("COLORTERM", "").lower()
    if term in {"truecolor", "24bit"}:
        return True

    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _terminal_width() -> int:
    return shutil.get_terminal_size((100, 20)).columns


def _center_fit_plain(line: str) -> str:
    width = max(_terminal_width() - 1, 20)
    if len(line) <= width:
        return line.center(width)

    if width <= 3:
        return line[:width]

    return line[: width - 3] + "..."


def _interpolate(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float):
    return tuple(
        int(start[channel] + (end[channel] - start[channel]) * ratio)
        for channel in range(3)
    )


def _paint(text: str, rgb: tuple[int, int, int], enabled: bool) -> str:
    if not enabled:
        return text

    r, g, b = rgb
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"


def _render_art(lines: list[str], enabled: bool) -> str:
    width = max(len(_center_fit_plain(line)) for line in lines)
    start = (100, 100, 100)
    end = (255, 255, 255)
    rendered = []

    for line in lines:
        padded = _center_fit_plain(line).ljust(width)
        row = []
        for index, char in enumerate(padded):
            if char == " ":
                row.append(char)
                continue

            ratio = index / max(width - 1, 1)
            row.append(_paint(char, _interpolate(start, end, ratio), enabled))

        rendered.append("".join(row).rstrip())

    return "\n".join(rendered)


def _render_info(lines: list[str], enabled: bool) -> str:
    start = (255, 255, 255)
    end = (100, 100, 100)
    rendered = []

    for line in lines:
        line = _center_fit_plain(line)
        if not enabled:
            rendered.append(line)
            continue

        pieces = []
        for index, char in enumerate(line):
            ratio = index / max(len(line) - 1, 1)
            pieces.append(_paint(char, _interpolate(start, end, ratio), True))

        rendered.append("".join(pieces))

    return "\n".join(rendered)


def build_startup_logo(build: str, version: str, update: str) -> str:
    enabled = _supports_truecolor()
    info = [
        f"Version : {version} | coden: ELDA",
        "made by @h_ae_256, @kot_ewik, @squeeare (пидр) ",
    ]
    return (
        "\n"
        + _render_art(RATKO_ART, enabled)
        + "\n\n"
        + _render_info(info, enabled)
        + "\n\n"
    )
