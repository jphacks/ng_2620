"""Tray icon for the Windows launcher: shows that vvlog is running and lets the user open / copy the URL.

  * the tooltip carries the URL, so it can be typed into any browser when the automatic open did not work
  * double-click / "ブラウザで開く" opens it, "URL をコピー" puts it on the clipboard, "終了" stops the launcher
  * Windows only (pystray runs detached on a thread there); anywhere else, or when pystray is missing, start() returns
    None and the launcher keeps working without it
"""
from __future__ import annotations

import logging
import os
import subprocess
import webbrowser
from typing import Callable

log = logging.getLogger("vvlog.launcher")


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["clip"], input=text.encode("utf-16le"), check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except (OSError, subprocess.SubprocessError, AttributeError):
        return False


def make_image(size: int = 64):
    """A plain green disc with a white 'v' - drawn here so the package needs no icon file."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 3, size - 3), fill=(34, 160, 90, 255))
    w = max(2, size // 9)
    d.line([(size * 0.27, size * 0.33), (size * 0.5, size * 0.70), (size * 0.73, size * 0.33)], fill="white", width=w,
           joint="curve")
    return img


class Tray:
    def __init__(self, icon) -> None:
        self._icon = icon

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:  # noqa: BLE001 - the tray must never break shutdown
            pass


def start(url: str, on_quit: Callable[[], None], opener: Callable[[str], object] = webbrowser.open) -> Tray | None:
    if os.name != "nt":
        return None
    try:
        import pystray

        def open_url(*_):
            opener(url)

        def copy_url(icon, *_):
            if copy_to_clipboard(url):
                icon.notify(f"{url} をコピーしました", "vvlog")

        def quit_(icon, *_):
            on_quit()

        menu = pystray.Menu(
            pystray.MenuItem("ブラウザで開く", open_url, default=True),
            pystray.MenuItem(url, None, enabled=False),
            pystray.MenuItem("URL をコピー", copy_url),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("終了", quit_),
        )
        icon = pystray.Icon("vvlog", make_image(), f"vvlog 起動中  {url}", menu)
        icon.run_detached()
        return Tray(icon)
    except Exception as e:  # noqa: BLE001 - missing pystray / no shell tray: carry on without it
        log.warning("トレイアイコンを出せませんでした（なくても使えます）: %s", e)
        return None
