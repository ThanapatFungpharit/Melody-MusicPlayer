"""Hardened entry point used only by packaged production builds."""

from musicplayer.runtime_environment import configure_production_runtime


def main() -> None:
    configure_production_runtime()

    import flet as ft

    from musicplayer.app import main as app_main

    ft.run(app_main)

if __name__ == "__main__":
    main()
