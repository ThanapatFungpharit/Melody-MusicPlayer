"""Isolated presentation fixture with real library, storage and playback logic."""

from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import flet as ft

from musicplayer.app import MusicPlayerApp
from musicplayer.application.models import AppSettings, DownloadRecord, SearchResult
from musicplayer.application.store import ApplicationStore
from musicplayer.core.library import MusicManager


class TestPage:
    __test__ = False

    def __init__(self, width=390, height=844, platform=ft.PagePlatform.ANDROID):
        self.width, self.height, self.platform = width, height, platform
        self.window = SimpleNamespace(width=width, height=height)
        self.services = []
        self.views = [ft.View()]
        self.dialogs = []
        self.updated = []

    def add(self, control):
        self.views[0].controls.append(control)

    def update(self, *controls):
        self.updated.append(controls)

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        return self.dialogs.pop() if self.dialogs else None


def make_app(page, directory: Path, *, mobile=True, populated=True) -> MusicPlayerApp:
    directory.mkdir(parents=True, exist_ok=True)
    music = directory / "music"
    music.mkdir(exist_ok=True)
    settings = AppSettings(download_directory=str(music), notifications=False)
    store = ApplicationStore(directory / "state.json")
    store.save_settings(settings)
    manager = MusicManager(directory / "library.mmdb", music)
    if populated:
        for i, title in enumerate(
            (
                "A very long track title to test clipping and comfortable touch navigation",
                "Ocean Drive",
                "Evening Light",
            )
        ):
            path = music / f"track-{i}.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\0\0" * (8000 + i))
            manager.add_track(path, title=title)
        ids = [str(t.id) for t in manager.list_tracks()]
        playlist = manager.create_playlist(
            "A playlist with a long name for the weekend"
        )
        manager.add_tracks_to_playlist(playlist, ids)
        store.set("recent_tracks", ids)
        store.set("playback", {"queue": ids, "current_index": 0})
    backend = Mock()
    with (
        patch("musicplayer.app._application_data_directory", return_value=directory),
        patch("musicplayer.app.FletAudioBackend", return_value=backend),
        patch.object(MusicPlayerApp, "_is_mobile_platform", return_value=mobile),
    ):
        app = MusicPlayerApp(page)
    app.search_results = [
        SearchResult(
            id="preview",
            title="A search result with a long title to check truncation",
            uploader="Demo artist",
            duration=180,
            thumbnail="",
            url="https://youtu.be/preview",
            source="YouTube",
        )
    ]
    records = (
        [
            DownloadRecord(
                id="active",
                url="https://youtu.be/a",
                title="A track downloading in the background",
                status="downloading",
                progress=0.4,
            ),
            DownloadRecord(
                id="failed",
                url="https://youtu.be/b",
                title="Interrupted download",
                status="failed",
                error="Connection interrupted. Try again.",
            ),
            DownloadRecord(
                id="done",
                url="https://youtu.be/c",
                title="Downloaded track",
                status="completed",
                progress=1,
            ),
        ]
        if populated
        else []
    )
    app.downloads.list = Mock(return_value=tuple(records))
    app.downloads.active_count = Mock(return_value=1 if populated else 0)
    app._refresh_download_badge()
    return app


def walk(control):
    if isinstance(control, ft.Control):
        yield control
    for attribute in ("controls", "destinations", "actions"):
        for child in getattr(control, attribute, None) or []:
            yield from walk(child)
    for attribute in ("content", "leading", "trailing", "title", "subtitle"):
        child = getattr(control, attribute, None)
        if isinstance(child, ft.Control):
            yield from walk(child)
