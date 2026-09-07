# Melody

Melody is a modern desktop music player and downloader built with Python 3.12+, Flet, `flet-audio`, and `yt-dlp`. It provides one continuous workflow for searching, previewing, downloading, organizing, queueing, and playing music.

## What is implemented

- Paged YouTube search with track-list and artwork-grid views; pasted playlist URLs load their tracks with playlist-aware pagination
- Optional manually uploaded cookie-file sessions for personalized YouTube search, signed-in playlists, stream previews, and downloads
- Stream previews directly from Search
- Individual, multi-YouTube-URL batch, and playlist downloads with concurrent workers, per-song progress, cancel, retry, isolated failure details, and persistent history
- Automatic import of completed audio into the existing binary `MusicManager` library
- Native multi-file import for MP3, M4A, Opus, WAV, FLAC, OGG, and AAC, with content-hash duplicate detection
- Persistent playlists with duplicate-aware YouTube playlist import, bulk multi-select track adding, ordered tracks, rename/delete, copy/move, and safe removal that does not delete audio files
- Searchable/sortable track library, favorites, title editing, explicit uploader/channel credits, duplicate-by-source detection, and integrity-aware file paths
- Persistent playback queue with play-next, append, reorder, remove, clear-upcoming, shuffle, repeat-track, and repeat-queue
- Background playback while Melody is minimized, another app is active, or a mobile device is locked
- Lock-screen, notification-panel, Control Center, and desktop system-media controls with synchronized artwork, title, artist, playback state, seeking, and previous/next actions
- Global playback bar with seek, previous/next, exact 1% volume buttons, 0–100 slider, mute restore, and media-key/keyboard handling
- Recent tracks, playback/search history, session queue state, light/dark/system themes, and configurable YouTube/download settings

## Architecture

The original `core/` remains authoritative:

- `core.concurrency.LazyBoundedExecutor` provides lazy, bounded worker pools with immediate overload backpressure and automatic idle retirement.
- `core.downloader.Downloader` owns bounded threaded `yt-dlp` execution, progress callbacks, cancellation, staged files, and collision-safe output names.
- `core.library.MusicManager` owns durable track records, hashes, duplicate source identity, library paths, integrity checks, and playlist order.

The new `application/` layer adapts those capabilities instead of replacing them:

- `providers.py` performs search and stream metadata extraction through a provider registry.
- `downloads.py` coordinates core jobs, persistent history, retry, and library import.
- `library_service.py` adds favorites/enriched metadata and explicit file-deletion semantics.
- `queue.py` models the temporary playback queue independently from playlists.
- `playback.py` coordinates queue behavior with an abstract audio backend.
- `store.py` atomically persists settings, enriched metadata, history, and session state as JSON.

The `ui/` package contains the Flet-specific audio adapter and visual theme; `app.py` composes views and delegates business operations to the application services.

## Performance characteristics

The latency-sensitive paths are deliberately bounded:

- Search, stream resolution, local imports, and managed-audio reads share a pool capped at two active and two pending tasks. The pool and its threads do not exist while idle; excess submissions are rejected immediately so UI actions never block behind or add to an unbounded queue.
- The downloader creates its own pool only on the first download. Active work is capped by the configured concurrency and pending work has the same cap, for a maximum of `2 × concurrent_downloads` accepted tasks. The pool retires after the final task, and overload is reported per song without cancelling an otherwise valid batch.
- Local audio files are read on the bounded I/O pool. Generation checks discard stale results when the user changes tracks before a read finishes, preventing both UI blocking and activation of obsolete audio bytes.
- Playback position and duration events update only the seek slider and time labels instead of rebuilding the complete player bar, re-reading track metadata, and recreating artwork on every tick.
- System media state is updated immediately for track, transport, queue-mode, and artwork changes, while position-only updates are throttled to once every five seconds. The operating system extrapolates the playhead between updates, preserving an accurate seek surface without waking the Python UI on every audio tick.
- Android holds the audio player's wake lock only while playback requires it; Melody deliberately leaves the media session's optional Wi-Fi and background keepalive locks disabled. iOS uses the native playback audio-session category, and the underlying player handles transient audio-focus interruptions such as calls before resuming when the operating system restores focus.
- Volume changes reach the audio backend continuously while dragging, but the durable setting is written only when the drag settles. Button and mute changes remain immediately durable.
- Starting a track commits play count, last-played time, recents, history, and the current session queue in one atomic state-file replacement. Private application state uses compact JSON to reduce serialization and disk traffic.
- Download callbacks notify the UI at most five times per second and checkpoint non-terminal progress at most once per second. Completion, failure, cancellation, and status transitions are still immediate, so the trade-off is only that a forced process termination can lose up to one second of transient progress.
- Library source/hash duplicate checks use in-memory indexes, newest-first ordering is cached until membership changes, shuffled queue selection uses constant auxiliary space, and library rows share one playlist snapshot per render. The indexes and ordering cache use O(n) additional memory to avoid repeated O(n) scans and O(n log n) sorts.
- Core task results, application download history, and shuffled previous-track history are each capped at 250 records. Temporary destination reservations are released immediately after a move, so repeated downloads cannot grow bookkeeping structures without bound. The shuffle cap means “previous” remembers the latest 250 shuffled transitions rather than an unlimited session lifetime.

Atomic temporary-file replacement and `fsync` are retained for settled and terminal state. This favors correctness and crash safety while coalescing only high-frequency intermediate updates. The worker caps deliberately trade unlimited burst acceptance for predictable CPU/RAM usage and responsive overload behavior.

## Run

Install dependencies and launch:

```console
uv sync
uv run musicplayer
```

On the first source-tree launch, `uv run musicplayer` automatically downloads
the pinned FFmpeg archive for the current desktop platform and architecture,
verifies its SHA-256 checksum, and extracts only FFmpeg and FFprobe. Later
launches reuse the verified cached bundle. Android and packaged desktop builds
continue to prepare their native tools through their build wrappers. Melody
never selects a system installation from `PATH`.

Application state uses the native per-user data location on every system:

- Windows: `%LOCALAPPDATA%\MelodyPlayer`
- Linux: `$XDG_DATA_HOME/MelodyPlayer`, falling back to
  `~/.local/share/MelodyPlayer`
- macOS: `~/Library/Application Support/MelodyPlayer`
- Android: Flet's application-specific data directory

Downloaded audio defaults to `~/Music/Melody` on desktop systems. On Android it
uses the app-specific data directory under `Music`, so downloads remain writable
without broad storage permissions. The location can be changed in Settings.

YouTube access is anonymous by default. To use a signed-in session, export a UTF-8 Netscape-format `cookies.txt` file and upload it in Settings. Melody validates the file and copies it to the platform's private application-data directory. It never detects browsers or reads their cookie databases, so the same workflow is available on desktop and Android. Cookie files contain sensitive session credentials and should not be shared.

## Test and lint

```console
uv run python -m unittest discover -s tests -v
uv run ruff check src tests
uv run ruff format --check src tests
```

Network search/download tests are intentionally excluded; provider mapping and the threaded downloader lifecycle are tested deterministically without external requests.

## Desktop bundles

Use the build wrapper rather than invoking `flet build` directly. It downloads
the pinned FFmpeg archive for the requested target, verifies its SHA-256 hash,
extracts only `ffmpeg` and `ffprobe`, and then packages them with the app:

```console
uv sync --no-dev --group build
uv run python tools/build_desktop.py windows --architecture x86_64 -- --yes
uv run python tools/build_desktop.py linux --architecture x86_64 -- --yes
uv run python tools/build_desktop.py macos --architecture x86_64 -- --yes
uv run python tools/build_desktop.py macos --architecture arm64 -- --yes
```

The release workflow builds separate Intel and Apple Silicon macOS bundles and
uploads every desktop bundle with its architecture in the artifact name.
Generated binaries are deliberately kept out of Git; every distributable
artifact obtains them from the pinned, checksum-verified manifest in
`tools/fetch_media_binaries.py`.

## Android bundles

Android 10 and newer do not permit applications to execute programs copied
into writable app storage. Use the Android build wrapper, which packages each
position-independent FFmpeg executable as an ABI-specific native library and
enables installer extraction into Android's read-only native-library directory:

```console
uv sync --no-dev --group build
uv run python -m tools.build_android apk -- --yes --split-per-abi
uv run python -m tools.build_android aab -- --yes
```

The default Android build supports `arm64-v8a`, `armeabi-v7a`, and `x86_64`,
targets Android 10/API 29 or newer, and bundles both `ffmpeg` and `ffprobe`.
Use `--architecture arm64`, `--architecture arm`, or `--architecture x86_64`
before `--` to build a single ABI. The release workflow uploads each
side-loadable APK as an architecture-specific artifact and uploads the Google
Play App Bundle separately.

## Production release safeguards

The build wrappers are release-only. They reject development/staging
environments, enabled debug flags, verbose/profile builds, disabled Python
compilation, and caller-selected embedded Python versions. Release packaging
uses optimized Python bytecode, removes non-runtime source files, source maps,
test helpers, cache directories, and the source-checkout bootstrap, and embeds
a validated `production` configuration with debug, mock data, and source maps
disabled. The audit permits only the two Flet bootstrap sources required by the
embedded Python runtime.
Python 3.13 is pinned for packaged releases so the optional remote-debugging
module added to the Python 3.14 runtime is not shipped.

Local Android builds must provide the four `FLET_ANDROID_SIGNING_*` variables
accepted by Flet (`KEY_STORE`, `KEY_STORE_PASSWORD`, `KEY_PASSWORD`, and
`KEY_ALIAS`). The wrapper verifies that the key-store file exists and rejects
obvious debug-key configurations before doing any build work.

After a local build, run the same recursive artifact audit used by CI:

```console
uv run python tools/verify_production_artifact.py build/windows
uv run python tools/verify_production_artifact.py build/apk build/aab
```

The audit opens nested APK/AAB/Python archives and fails if it finds a package
from any non-runtime dependency group, known test/dev tooling, non-runtime
Python sources, source maps, debug symbols, local tool output, test directories,
environment files, or a missing/non-production release manifest. CI installs the build
toolchain without the `dev` group, runs this audit before upload, and uses the
protected `production` GitHub environment. Android releases additionally
require `ANDROID_KEY_STORE_BASE64`, `ANDROID_KEY_STORE_PASSWORD`,
`ANDROID_KEY_PASSWORD`, and `ANDROID_KEY_ALIAS` secrets; the workflow stops
instead of falling back to Flet's debug signing key when any is missing.
