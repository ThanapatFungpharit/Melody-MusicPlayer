"""Hardened entry point used only by packaged production builds."""

import asyncio

from musicplayer.runtime_environment import configure_production_runtime
from musicplayer.yt_dlp_updater import prepare_yt_dlp


def main() -> None:
    # This must happen before importing Flet's application graph: providers
    # and the downloader load yt-dlp lazily only after this preparation.
    asyncio.run(prepare_yt_dlp())
    configure_production_runtime()

    import flet as ft

    from musicplayer.app import main as app_main

    ft.run(app_main)


if __name__ == "__main__":
    main()
