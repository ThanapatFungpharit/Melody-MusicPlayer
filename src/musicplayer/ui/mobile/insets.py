"""Safe-area primitives shared by the mobile presentation layer.

The values used here come from Flet's platform ``MediaQuery`` data. Keeping the
policy in one place prevents individual screens from guessing Android status-bar,
cutout, keyboard, or navigation-bar dimensions.
"""

from __future__ import annotations

import flet as ft


def safe_area(content: ft.Control, *, expand: bool = False) -> ft.SafeArea:
    """Keep mobile content clear of every platform intrusion.

    ``maintain_bottom_view_padding`` is intentional: Android may report a
    temporary keyboard inset in addition to the persistent gesture or navigation
    inset. Preserving the bottom view padding keeps the last scrollable item and
    the bottom navigation reachable when that transient inset changes.
    """

    return ft.SafeArea(
        content=content,
        expand=expand,
        avoid_intrusions_left=True,
        avoid_intrusions_top=True,
        avoid_intrusions_right=True,
        avoid_intrusions_bottom=True,
        maintain_bottom_view_padding=True,
    )


def bottom_sheet(content: ft.Control, *, max_height: float) -> ft.BottomSheet:
    """Create an inset-aware mobile action sheet.

    Flet applies the current platform safe-area and view-inset data when these
    flags are enabled. The sheet content remains scrollable so its final action
    can never be hidden behind gesture navigation or an on-screen keyboard.
    """

    return ft.BottomSheet(
        content=content,
        show_drag_handle=True,
        use_safe_area=True,
        scrollable=True,
        maintain_bottom_view_insets_padding=True,
        size_constraints=ft.BoxConstraints(max_height=max_height),
    )
