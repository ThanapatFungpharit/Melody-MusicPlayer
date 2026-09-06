import "dart:async";

import "package:audioplayers/audioplayers.dart";
import "package:flet/flet.dart";
import "package:flutter/foundation.dart";
import "package:flutter_media_session/flutter_media_session.dart";
import "package:flutter_media_session/flutter_media_session_platform_interface.dart";

class BackgroundAudioSessionService extends FletService {
  BackgroundAudioSessionService({required super.control});

  final FlutterMediaSession _session = FlutterMediaSession();
  bool _active = false;
  bool _disposed = false;

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(_invokeMethod);
    _installActionHandler();
    // Apply the music audio context before Flet's Audio service creates its
    // native player. Android's player then owns audio focus and automatically
    // pauses/resumes for calls; iOS uses a playback session that survives lock.
    unawaited(_configureAudioContext());
  }

  void _installActionHandler() {
    _session.setActionHandler(
      onPlay: () => _sendAction("play"),
      onPause: () => _sendAction("pause"),
      onSkipToNext: () => _sendAction("skipToNext"),
      onSkipToPrevious: () => _sendAction("skipToPrevious"),
      onStop: () => _sendAction("stop"),
      onSeekTo: (position) => _sendAction("seekTo", seekPosition: position),
      onRewind: () => _sendAction("rewind"),
      onFastForward: () => _sendAction("fastForward"),
    );
  }

  Future<void> _configureAudioContext() async {
    try {
      await AudioPlayer.global.setAudioContext(
        AudioContext(
          android: const AudioContextAndroid(
            stayAwake: true,
            contentType: AndroidContentType.music,
            usageType: AndroidUsageType.media,
            audioFocus: AndroidAudioFocus.gain,
          ),
          iOS: AudioContextIOS(category: AVAudioSessionCategory.playback),
        ),
      );
    } catch (error) {
      debugPrint("BackgroundAudioSession: audio context unavailable: $error");
    }
  }

  Future<void> _ensureActive() async {
    if (_active || _disposed) {
      return;
    }
    await _configureAudioContext();
    // Windows needs its identity before SMTC is initialized, otherwise the
    // first media flyout can label an unpackaged build as an unknown app.
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.windows) {
      await _session.setWindowsAppUserModelId(
        "Melody.MusicPlayer",
        displayName: "Melody",
      );
    }
    // FlutterMediaSession.deactivate() intentionally clears this subscription,
    // so reinstall it whenever an inactive session is brought back.
    _installActionHandler();
    await _session.activate();
    // audioplayers already owns and handles audio focus. Enabling the media
    // shim's focus handler as well would make the two native focus requests
    // interrupt one another.
    await _session.setAutoHandleInterruptions(false);
    await _session.setBackgroundKeepAlive(false);
    _active = true;
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    switch (name) {
      case "sync":
        await _sync(Map<String, dynamic>.from(args as Map));
        return null;
      case "deactivate":
        await _deactivate();
        return null;
      default:
        throw Exception("Unknown BackgroundAudioSession method: $name");
    }
  }

  Future<void> _sync(Map<String, dynamic> data) async {
    if (_disposed) {
      return;
    }
    await _ensureActive();
    final title = (data["title"] as String?)?.trim() ?? "";
    final artist = (data["artist"] as String?)?.trim();
    final album = (data["album"] as String?)?.trim();
    final artworkUri = (data["artwork_uri"] as String?)?.trim();
    final durationMs = (data["duration_ms"] as num?)?.toInt() ?? 0;
    final positionMs = (data["position_ms"] as num?)?.toInt() ?? 0;
    final playing = data["playing"] as bool? ?? false;

    await FlutterMediaSessionPlatform.instance.updateMetadata(
      MediaMetadata(
        title: title.isEmpty ? "Melody" : title,
        artist: artist == null || artist.isEmpty ? "Unknown artist" : artist,
        album: album == null || album.isEmpty ? null : album,
        artworkUri: artworkUri == null || artworkUri.isEmpty
            ? null
            : artworkUri,
        duration: Duration(milliseconds: durationMs),
      ),
    );

    final actions = <MediaAction>{
      MediaAction.play,
      MediaAction.pause,
      MediaAction.seekTo,
      if (data["has_next"] as bool? ?? false) MediaAction.skipToNext,
      if (data["has_previous"] as bool? ?? false) MediaAction.skipToPrevious,
    };
    await FlutterMediaSessionPlatform.instance.updateAvailableActions(actions);

    final repeatMode = switch (data["repeat_mode"] as String? ?? "off") {
      "track" => MediaRepeatMode.one,
      "playlist" => MediaRepeatMode.all,
      _ => MediaRepeatMode.none,
    };
    await FlutterMediaSessionPlatform.instance.updatePlaybackState(
      PlaybackState(
        status: playing ? PlaybackStatus.playing : PlaybackStatus.paused,
        position: Duration(milliseconds: positionMs),
        speed: playing ? 1.0 : 0.0,
        repeatMode: repeatMode,
        shuffleModeEnabled: data["shuffle"] as bool? ?? false,
      ),
    );
  }

  void _sendAction(String action, {Duration? seekPosition}) {
    if (_disposed) {
      return;
    }
    control.triggerEvent("action", {
      "action": action,
      "seek_position_ms": seekPosition?.inMilliseconds,
    });
  }

  Future<void> _deactivate() async {
    if (!_active) {
      return;
    }
    _active = false;
    await _session.deactivate();
  }

  @override
  void dispose() {
    _disposed = true;
    control.removeInvokeMethodListener(_invokeMethod);
    _session.clearActionHandler();
    unawaited(_session.deactivate().catchError((Object _) {}));
    super.dispose();
  }
}
