from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from musicplayer.runtime_environment import (
    EXPECTED_RELEASE_CONFIG,
    configure_production_runtime,
    load_release_config,
    public_error_message,
)
from tools import build_android, build_desktop
from tools.production_build import (
    RELEASE_PYTHON_VERSION,
    hardened_flet_arguments,
    production_build_environment,
    production_process_environment,
    validate_android_release_signing,
)
from tools.verify_production_artifact import (
    audit_artifacts,
    dependency_group_packages,
)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
    return output.getvalue()


class ProductionBuildTests(unittest.TestCase):
    def test_all_build_and_dev_only_transitive_packages_are_forbidden(self) -> None:
        packages = dependency_group_packages(Path("pyproject.toml"))

        self.assertIn("flet-cli", packages)
        self.assertIn("flet-desktop", packages)
        self.assertIn("ruff", packages)
        self.assertIn("cookiecutter", packages)
        self.assertIn("rich", packages)
        self.assertNotIn("flet", packages)

    def test_release_configuration_is_production_only(self) -> None:
        self.assertEqual(load_release_config(), EXPECTED_RELEASE_CONFIG)

    def test_release_runtime_removes_development_overrides_and_logging(self) -> None:
        previous_disable_level = logging.root.manager.disable
        previous_raise_exceptions = logging.raiseExceptions
        try:
            with mock.patch.dict(
                os.environ,
                {
                    "MELODY_ENV": "staging",
                    "MELODY_DEBUG": "1",
                    "FLET_FORCE_WEB_SERVER": "1",
                    "FLET_VIEW_PATH": "development-client",
                },
                clear=True,
            ):
                configure_production_runtime()

                self.assertEqual(os.environ["MELODY_ENV"], "production")
                self.assertEqual(os.environ["MELODY_DEBUG"], "0")
                self.assertNotIn("FLET_FORCE_WEB_SERVER", os.environ)
                self.assertNotIn("FLET_VIEW_PATH", os.environ)
                self.assertEqual(logging.root.manager.disable, logging.CRITICAL)
                self.assertFalse(logging.raiseExceptions)
        finally:
            logging.disable(previous_disable_level)
            logging.raiseExceptions = previous_raise_exceptions

    def test_public_errors_do_not_expose_paths_urls_or_traces(self) -> None:
        generic = "The operation could not be completed. Please try again."
        for message in (
            r"[WinError 5] Access denied: C:\Users\person\private.json",
            "Request failed for https://example.test/?token=secret",
            'Traceback (most recent call last): File "app.py", line 1',
            "x" * 241,
        ):
            with self.subTest(message=message):
                self.assertEqual(public_error_message(message), generic)

        self.assertEqual(
            public_error_message("Only YouTube URLs are supported."),
            "Only YouTube URLs are supported.",
        )

    def test_build_environment_forces_production_and_optimized_python(self) -> None:
        environment = production_build_environment({"PATH": "test"})

        self.assertEqual(environment["MELODY_ENV"], "production")
        self.assertEqual(environment["MELODY_DEBUG"], "0")
        self.assertEqual(environment["PYTHONDEVMODE"], "0")
        self.assertEqual(environment["PYTHONOPTIMIZE"], "2")

    def test_build_environment_rejects_staging_and_debug_flags(self) -> None:
        for environment in (
            {"MELODY_ENV": "staging"},
            {"MELODY_DEBUG": "true"},
            {"PYTHONDEVMODE": "1"},
            {"PYTHONASYNCIODEBUG": "yes"},
            {"FLET_FORCE_WEB_SERVER": "1"},
            {"FLET_VIEW_PATH": "development-client"},
        ):
            with self.subTest(environment=environment), self.assertRaises(RuntimeError):
                production_build_environment(environment)

    def test_android_build_requires_release_signing(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_android_release_signing({})

        with tempfile.TemporaryDirectory() as directory:
            key_store = Path(directory) / "release.jks"
            key_store.write_bytes(b"key store")
            environment = {
                "FLET_ANDROID_SIGNING_KEY_STORE": str(key_store),
                "FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD": "store password",
                "FLET_ANDROID_SIGNING_KEY_PASSWORD": "key password",
                "FLET_ANDROID_SIGNING_KEY_ALIAS": "release",
            }

            validate_android_release_signing(environment)

            environment["FLET_ANDROID_SIGNING_KEY_ALIAS"] = "androiddebugkey"
            with self.assertRaises(RuntimeError):
                validate_android_release_signing(environment)

    def test_in_process_environment_is_restored(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"MELODY_ENV": "production", "MELODY_DEBUG": "0"},
            clear=True,
        ):
            with production_process_environment():
                self.assertEqual(os.environ["PYTHONOPTIMIZE"], "2")
                self.assertEqual(os.environ["FLET_DEBUG"], "0")
            self.assertNotIn("PYTHONOPTIMIZE", os.environ)
            self.assertNotIn("FLET_DEBUG", os.environ)

    def test_flet_arguments_enforce_release_packaging(self) -> None:
        arguments = hardened_flet_arguments(["--yes"])

        self.assertIn("--compile-app", arguments)
        self.assertIn("--compile-packages", arguments)
        self.assertIn("--cleanup-app", arguments)
        self.assertIn("--cleanup-packages", arguments)
        version_index = arguments.index("--python-version")
        self.assertEqual(arguments[version_index + 1], RELEASE_PYTHON_VERSION)

    def test_flet_arguments_reject_release_weakening_options(self) -> None:
        for argument in (
            "--debug",
            "--profile",
            "--verbose",
            "-vv",
            "--no-compile-app",
            "--no-compile-packages",
            "--python-version",
            "--python-version=3.12",
            "--flutter-build-args=--debug",
            "--flutter-build-args=--dart-define=DEBUG=true",
            "--source-maps",
            "--split-debug-info",
        ):
            with self.subTest(argument=argument), self.assertRaises(ValueError):
                hardened_flet_arguments([argument])

    def test_wrappers_reject_unsafe_configuration_before_fetching(self) -> None:
        with (
            mock.patch.object(
                os,
                "environ",
                {"MELODY_ENV": "production", "MELODY_DEBUG": "0"},
            ),
            mock.patch(
                "sys.argv",
                [
                    "build_desktop.py",
                    "windows",
                    "--architecture",
                    "x86_64",
                    "--",
                    "--debug",
                ],
            ),
            mock.patch("tools.build_desktop.fetch_bundle") as desktop_fetch,
            self.assertRaises(ValueError),
        ):
            build_desktop.main()
        desktop_fetch.assert_not_called()

        with (
            mock.patch("sys.argv", ["build_android.py", "apk", "--", "--yes"]),
            mock.patch("tools.build_android.fetch_bundle") as android_fetch,
            self.assertRaises(RuntimeError),
        ):
            build_android.main()
        android_fetch.assert_not_called()

    def test_artifact_audit_accepts_compiled_production_archive(self) -> None:
        release = json.dumps(EXPECTED_RELEASE_CONFIG).encode()
        app_zip = _zip_bytes(
            {
                "main.pyc": b"compiled",
                "musicplayer/app.pyc": b"compiled",
                "musicplayer/release.json": release,
            }
        )
        packages_zip = _zip_bytes({"flet/__init__.pyc": b"compiled"})

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "melody.apk"
            artifact.write_bytes(
                _zip_bytes(
                    {
                        "assets/app.zip": app_zip,
                        "assets/sitepackages.zip": packages_zip,
                    }
                )
            )

            audit = audit_artifacts([artifact], Path("pyproject.toml"))

        self.assertEqual(audit.violations, [])

    def test_artifact_audit_rejects_dev_packages_sources_and_test_data(self) -> None:
        release = json.dumps(EXPECTED_RELEASE_CONFIG).encode()
        app_zip = _zip_bytes(
            {
                "musicplayer/release.json": release,
                "musicplayer/__main__.pyc": b"development bootstrap",
                ".flet/storage/temp/index.html": b"tool cache",
            }
        )
        packages_zip = _zip_bytes(
            {
                "ruff/__init__.pyc": b"dev package",
                "certifi/tests/test_certify.pyc": b"tests",
                "bundle.js.map": b"source map",
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "melody.apk"
            artifact.write_bytes(
                _zip_bytes(
                    {
                        "assets/app.zip": app_zip,
                        "assets/sitepackages.zip": packages_zip,
                    }
                )
            )

            audit = audit_artifacts([artifact], Path("pyproject.toml"))

        violations = "\n".join(audit.violations)
        self.assertIn("source-checkout development entry point", violations)
        self.assertIn("development/tooling directory '.flet'", violations)
        self.assertIn("development-only package 'ruff'", violations)
        self.assertIn("development/tooling directory 'tests'", violations)
        self.assertIn("non-runtime source/debug artifact (.map)", violations)

    def test_artifact_audit_rejects_remote_debugging_runtime(self) -> None:
        release = json.dumps(EXPECTED_RELEASE_CONFIG).encode()
        artifact_contents = _zip_bytes(
            {
                "assets/app.zip": _zip_bytes({"musicplayer/release.json": release}),
                "assets/stdlib.zip": _zip_bytes(
                    {"_remote_debugging.soref": b"lib_remote_debugging.so"}
                ),
                "lib/arm64-v8a/lib_remote_debugging.so": b"debugger",
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "melody.apk"
            artifact.write_bytes(artifact_contents)
            audit = audit_artifacts([artifact], Path("pyproject.toml"))

        violations = "\n".join(audit.violations)
        self.assertIn("_remote_debugging.soref: remote-debugging runtime", violations)
        self.assertIn("lib_remote_debugging.so: remote-debugging runtime", violations)

    def test_artifact_audit_requires_production_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "bundle.zip"
            artifact.write_bytes(_zip_bytes({"main.pyc": b"compiled"}))

            audit = audit_artifacts([artifact], Path("pyproject.toml"))

        self.assertIn("missing musicplayer/release.json", "\n".join(audit.violations))


if __name__ == "__main__":
    unittest.main()
