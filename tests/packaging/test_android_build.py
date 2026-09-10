from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_android import (
    configure_single_task_activity,
    stage_android_native_libraries,
)


class AndroidBuildTests(unittest.TestCase):
    def test_launcher_activity_is_single_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            flutter = Path(directory)
            manifest = flutter / "android/app/src/main/AndroidManifest.xml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application>
    <activity android:name=".MainActivity" android:launchMode="singleTop">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
    </activity>
  </application>
</manifest>
""",
                encoding="utf-8",
            )

            configure_single_task_activity(flutter)

            configured = manifest.read_text(encoding="utf-8")
            self.assertIn('android:launchMode="singleTask"', configured)
            self.assertIn('android:documentLaunchMode="never"', configured)

    def test_programs_are_staged_as_abi_native_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / "native"
            flutter = root / "flutter"
            for architecture in ("arm64", "x86_64"):
                source = binaries / "android" / architecture
                source.mkdir(parents=True)
                (source / "ffmpeg").write_bytes(f"{architecture}-ffmpeg".encode())
                (source / "ffprobe").write_bytes(f"{architecture}-ffprobe".encode())

            stage_android_native_libraries(
                flutter,
                ["arm64", "x86_64"],
                binary_root=binaries,
            )

            jni = flutter / "android/app/src/main/jniLibs"
            self.assertEqual(
                (jni / "arm64-v8a/libffmpeg.so").read_bytes(), b"arm64-ffmpeg"
            )
            self.assertEqual(
                (jni / "arm64-v8a/libffprobe.so").read_bytes(), b"arm64-ffprobe"
            )
            self.assertEqual(
                (jni / "x86_64/libffmpeg.so").read_bytes(), b"x86_64-ffmpeg"
            )
            self.assertEqual(
                (jni / "x86_64/libffprobe.so").read_bytes(), b"x86_64-ffprobe"
            )


if __name__ == "__main__":
    unittest.main()
