import "package:flet/flet.dart";
import "package:flutter/widgets.dart";

import "background_audio_session.dart";

class Extension extends FletExtension {
  @override
  void ensureInitialized() {}

  @override
  FletService? createService(Control control) {
    return switch (control.type) {
      "BackgroundAudioSession" => BackgroundAudioSessionService(
        control: control,
      ),
      _ => null,
    };
  }

  @override
  Widget? createWidget(Key? key, Control control) => null;
}
