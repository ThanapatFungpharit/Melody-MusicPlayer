from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from musicplayer.platform_runtime import (
    MediaBinaryError,
    UnsupportedPlatformError,
    application_data_directory,
    current_architecture,
    current_platform,
    default_music_directory,
    media_binary_bundle,
)


class PlatformRuntimeTests(unittest.TestCase):
    def test_platform_names_are_normalized(self) -> None:
        self.assertEqual(current_platform("win32"), "windows")
        self.assertEqual(current_platform("linux"), "linux")
        self.assertEqual(current_platform("darwin"), "macos")
        self.assertEqual(current_platform("android"), "android")

    def test_flet_environment_detects_android_runtime(self) -> None:
        self.assertEqual(
            current_platform(environ={"FLET_PLATFORM": "android"}), "android"
        )

    def test_architecture_names_are_normalized(self) -> None:
        self.assertEqual(current_architecture("AMD64"), "x86_64")
        self.assertEqual(current_architecture("x86_64"), "x86_64")
        self.assertEqual(current_architecture("aarch64"), "arm64")
        self.assertEqual(current_architecture("arm64"), "arm64")
        self.assertEqual(current_architecture("arm64-v8a"), "arm64")

    def test_unsupported_runtime_has_an_actionable_error(self) -> None:
        with self.assertRaisesRegex(UnsupportedPlatformError, "freebsd"):
            current_platform("freebsd")
        with self.assertRaisesRegex(UnsupportedPlatformError, "riscv64"):
            current_architecture("riscv64")

    def test_windows_bundle_uses_executable_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = media_binary_bundle(
                platform_name="win32",
                architecture="AMD64",
                binary_root=directory,
            )
            self.assertEqual(bundle.directory, Path(directory) / "windows" / "x86_64")
            self.assertEqual(bundle.ffmpeg.name, "ffmpeg.exe")
            self.assertEqual(bundle.ffprobe.name, "ffprobe.exe")
            self.assertEqual(
                bundle.yt_dlp_options(), {"ffmpeg_location": str(bundle.directory)}
            )

    def test_unix_bundle_requires_both_executable_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "linux" / "arm64"
            target.mkdir(parents=True)
            ffmpeg = target / "ffmpeg"
            ffprobe = target / "ffprobe"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            ffmpeg.chmod(0o755)
            ffprobe.chmod(0o755)

            bundle = media_binary_bundle(
                platform_name="linux",
                architecture="aarch64",
                binary_root=directory,
                validate=True,
            )

            self.assertEqual(bundle.ffmpeg, ffmpeg)
            self.assertEqual(bundle.ffprobe, ffprobe)

    def test_android_bundle_uses_installed_native_library_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory)
            with mock.patch.dict(
                "os.environ",
                {
                    "FLET_PLATFORM": "android",
                    "ANDROID_NATIVE_LIBRARY_DIR": str(native),
                },
                clear=False,
            ):
                bundle = media_binary_bundle(architecture="aarch64")

            self.assertEqual(bundle.platform, "android")
            self.assertEqual(bundle.directory, native)
            self.assertEqual(bundle.ffmpeg, native / "libffmpeg.so")
            self.assertEqual(bundle.ffprobe, native / "libffprobe.so")
            self.assertEqual(
                bundle.yt_dlp_options(),
                {"ffmpeg_location": str(native / "libffmpeg.so")},
            )

    def test_yt_dlp_derives_android_ffprobe_from_exact_ffmpeg_path(self) -> None:
        from yt_dlp import YoutubeDL
        from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor

        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory)
            ffmpeg = native / "libffmpeg.so"
            ffprobe = native / "libffprobe.so"
            ffmpeg.touch()
            ffprobe.touch()

            with YoutubeDL(
                {"quiet": True, "ffmpeg_location": str(ffmpeg)}
            ) as downloader:
                postprocessor = FFmpegPostProcessor(downloader)

            self.assertEqual(postprocessor._paths["ffmpeg"], str(ffmpeg))
            self.assertEqual(postprocessor._paths["ffprobe"], str(ffprobe))

    def test_missing_binary_fails_validation(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(MediaBinaryError, "ffmpeg"),
        ):
            media_binary_bundle(
                platform_name="linux",
                architecture="x86_64",
                binary_root=directory,
                validate=True,
            )

    def test_user_data_paths_follow_each_operating_system(self) -> None:
        self.assertEqual(
            application_data_directory(
                platform_name="win32",
                environ={"LOCALAPPDATA": "C:/Users/test/AppData/Local"},
                home="C:/Users/test",
                create=False,
            ),
            Path("C:/Users/test/AppData/Local") / "MelodyPlayer",
        )
        self.assertEqual(
            application_data_directory(
                platform_name="darwin",
                environ={},
                home="/Users/test",
                create=False,
            ),
            Path("/Users/test/Library/Application Support/MelodyPlayer"),
        )
        self.assertEqual(
            application_data_directory(
                platform_name="linux",
                environ={"XDG_DATA_HOME": "/data/test"},
                home="/home/test",
                create=False,
            ),
            Path("/data/test/MelodyPlayer"),
        )
        self.assertEqual(
            application_data_directory(
                platform_name="android",
                environ={"FLET_APP_STORAGE_DATA": "/data/user/app/files/data"},
                home="/data/user/app/files",
                create=False,
            ),
            Path("/data/user/app/files/data"),
        )

    def test_android_default_music_directory_is_app_writable(self) -> None:
        self.assertEqual(
            default_music_directory(
                platform_name="android",
                environ={"FLET_APP_STORAGE_DATA": "/data/user/app/files/data"},
                home="/data/user/app/files",
            ),
            Path("/data/user/app/files/data/Music"),
        )

    def test_source_launch_fetches_and_validates_current_desktop_bundle(self) -> None:
        from musicplayer.__main__ import prepare_development_media_binaries

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch("musicplayer.__main__.current_platform", return_value="windows"),
            mock.patch(
                "musicplayer.__main__.current_architecture", return_value="x86_64"
            ),
            mock.patch("tools.fetch_media_binaries.fetch_bundle") as fetch,
            mock.patch("musicplayer.__main__.media_binary_bundle") as resolve,
            mock.patch(
                "musicplayer.__main__.validate_development_audio_encoders"
            ) as validate_encoders,
        ):
            root = Path(directory)
            expected_root = root / "src" / "musicplayer" / "bin"
            expected_bundle = mock.Mock()
            resolve.return_value = expected_bundle

            result = prepare_development_media_binaries(root)

            fetch.assert_called_once_with(
                "windows", "x86_64", binary_root=expected_root
            )
            resolve.assert_called_once_with(binary_root=expected_root, validate=True)
            validate_encoders.assert_called_once_with(expected_bundle)
            self.assertIs(result, expected_bundle)

    def test_source_launch_requires_encoders_for_every_settings_format(self) -> None:
        from musicplayer.__main__ import validate_development_audio_encoders

        bundle = mock.Mock(ffmpeg=Path("ffmpeg.exe"))
        encoder_listing = """
         A..... aac                  AAC (Advanced Audio Coding)
         A....D libmp3lame           libmp3lame MP3
         A....D libopus              libopus Opus
        """
        with mock.patch(
            "musicplayer.__main__.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=encoder_listing, stderr=""),
        ) as run:
            validate_development_audio_encoders(bundle)

        run.assert_called_once()

    def test_source_launch_rejects_ffmpeg_without_mp3_encoder(self) -> None:
        from musicplayer.__main__ import validate_development_audio_encoders

        bundle = mock.Mock(ffmpeg=Path("ffmpeg.exe"))
        encoder_listing = """
         A..... aac                  AAC (Advanced Audio Coding)
         A....D libopus              libopus Opus
        """
        with (
            mock.patch(
                "musicplayer.__main__.subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=encoder_listing, stderr=""),
            ),
            self.assertRaisesRegex(MediaBinaryError, r"MP3 \(libmp3lame\)"),
        ):
            validate_development_audio_encoders(bundle)

    def test_source_launch_does_not_fetch_android_executables(self) -> None:
        from musicplayer.__main__ import prepare_development_media_binaries

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch("musicplayer.__main__.current_platform", return_value="android"),
            mock.patch("tools.fetch_media_binaries.fetch_bundle") as fetch,
        ):
            result = prepare_development_media_binaries(Path(directory))

        self.assertIsNone(result)
        fetch.assert_not_called()

    def test_source_launch_only_applies_codec_gate_to_windows_manifest(self) -> None:
        from musicplayer.__main__ import prepare_development_media_binaries

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch("musicplayer.__main__.current_platform", return_value="linux"),
            mock.patch(
                "musicplayer.__main__.current_architecture", return_value="x86_64"
            ),
            mock.patch("tools.fetch_media_binaries.fetch_bundle"),
            mock.patch(
                "musicplayer.__main__.media_binary_bundle",
                return_value=mock.Mock(),
            ),
            mock.patch(
                "musicplayer.__main__.validate_development_audio_encoders"
            ) as validate_encoders,
        ):
            prepare_development_media_binaries(Path(directory))

        validate_encoders.assert_not_called()


if __name__ == "__main__":
    unittest.main()
