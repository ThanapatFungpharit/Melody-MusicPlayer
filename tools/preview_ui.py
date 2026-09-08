"""Render isolated presentation fixtures: python tools/preview_ui.py --mobile.

Uses a temporary library and a silent backend. Never opens the user's data.
"""

import argparse
import sys
import tempfile
from pathlib import Path

import flet as ft

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.ui_support import make_app

parser = argparse.ArgumentParser()
parser.add_argument("--mobile", action="store_true")
parser.add_argument("--width", type=int)
parser.add_argument("--height", type=int)
parser.add_argument("--theme", choices=("light", "dark", "system"), default="system")
parser.add_argument("--empty", action="store_true")
parser.add_argument(
    "--screen",
    choices=(
        "home",
        "search",
        "library",
        "playlists",
        "playlist",
        "queue",
        "downloads",
        "settings",
        "player",
    ),
    default="home",
)
args = parser.parse_args()


def main(page: ft.Page):
    temporary = tempfile.TemporaryDirectory(prefix="melody-ui-preview-")
    app = make_app(
        page, Path(temporary.name), mobile=args.mobile, populated=not args.empty
    )
    page.theme_mode = ft.ThemeMode(args.theme)
    page.title = f"Melody UI Preview — {args.screen}"
    page.window.min_width = 0
    page.window.min_height = 0
    page.window.width = args.width or (390 if args.mobile else 1540)
    page.window.height = args.height or (844 if args.mobile else 960)
    if args.screen in ("home", "search", "library", "playlists"):
        app.navigate(("home", "search", "library", "playlists").index(args.screen))
    elif args.screen == "playlist":
        playlists = app.manager.list_playlists()
        if playlists:
            app._open_playlist(str(playlists[0].id))
        else:
            app.navigate(3)
    else:
        app.shell.open_panel(args.screen)
    page.on_close = lambda _: (app._close(None), temporary.cleanup())
    page.update()


ft.run(main)
