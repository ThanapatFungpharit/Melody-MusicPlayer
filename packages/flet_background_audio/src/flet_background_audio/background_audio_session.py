from __future__ import annotations

from dataclasses import dataclass

import flet as ft


@dataclass
class MediaSessionActionEvent(ft.Event["BackgroundAudioSession"]):
    """A transport command received from the operating-system media panel."""

    action: str
    seek_position_ms: int | None = None


@ft.control("BackgroundAudioSession")
class BackgroundAudioSession(ft.Service):
    """Native media-session bridge used by Melody's existing audio player."""

    on_action: ft.EventHandler[MediaSessionActionEvent] | None = None

    async def sync(
        self,
        *,
        title: str,
        artist: str = "",
        album: str = "",
        artwork_uri: str = "",
        duration_ms: int = 0,
        position_ms: int = 0,
        playing: bool = False,
        loading: bool = False,
        has_next: bool = True,
        has_previous: bool = True,
        repeat_mode: str = "off",
        shuffle: bool = False,
        keep_alive: bool = False,
        reattach: bool = False,
    ) -> None:
        """Activate the native session and publish one coherent player snapshot."""
        await self._invoke_method(
            "sync",
            {
                "title": title,
                "artist": artist,
                "album": album,
                "artwork_uri": artwork_uri,
                "duration_ms": max(0, int(duration_ms)),
                "position_ms": max(0, int(position_ms)),
                "playing": bool(playing),
                "loading": bool(loading),
                "has_next": bool(has_next),
                "has_previous": bool(has_previous),
                "repeat_mode": repeat_mode,
                "shuffle": bool(shuffle),
                "keep_alive": bool(keep_alive),
                "reattach": bool(reattach),
            },
        )

    async def deactivate(self) -> None:
        """Remove system controls and release the foreground playback service."""
        await self._invoke_method("deactivate")
