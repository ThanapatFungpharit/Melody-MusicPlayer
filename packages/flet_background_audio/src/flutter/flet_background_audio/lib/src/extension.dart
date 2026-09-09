import "package:flet/flet.dart";
import "package:flutter/widgets.dart";

import "background_audio_session.dart";
import "managed_audio.dart";

class Extension extends FletExtension {
  @override
  void ensureInitialized() {}

  @override
  FletService? createService(Control control) {
    return switch (control.type) {
      "ManagedAudio" => ManagedAudioService(control: control),
      "BackgroundAudioSession" => BackgroundAudioSessionService(
        control: control,
      ),
      _ => null,
    };
  }

  @override
  Widget? createWidget(Key? key, Control control) => null;
}
