import "dart:async";

import "package:audioplayers/audioplayers.dart";
import "package:flet/flet.dart";

/// Melody's native source boundary. Audio bytes never cross the control tree.
/// A service owns one source for its lifetime; replacement disposes the player.
class ManagedAudioService extends FletService {
  ManagedAudioService({required super.control});

  final AudioPlayer _player = AudioPlayer();
  final List<StreamSubscription<dynamic>> _subscriptions = [];
  late final Future<void> _ready;
  bool _disposed = false;
  double _volume = 1;
  bool _volumeUpdating = false;
  int _positionSecond = -1;

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(_invokeMethod);
    _subscriptions.add(_player.onDurationChanged.listen((value) {
      _event("duration_change", {"duration": value});
    }));
    _subscriptions.add(_player.onPlayerStateChanged.listen((value) {
      _event("state_change", {"state": value.name});
    }));
    _subscriptions.add(_player.onPositionChanged.listen((value) {
      final second = value.inMilliseconds ~/ 1000;
      if (second != _positionSecond) {
        _positionSecond = second;
        _event("position_change", {"position": value.inMilliseconds});
      }
    }));
    _subscriptions.add(_player.onPlayerComplete.listen((_) {
      _event("state_change", {"state": "completed"});
    }));
    _volume = control.getDouble("volume", 1)!;
    _ready = _prepare();
    // Errors are delivered as source-scoped events, including preparation errors
    // that occur before Python can issue an RPC.
    unawaited(_ready.catchError((Object error) {
      _event("error", {"message": "The native audio source could not be prepared."});
    }));
  }

  Future<void> _prepare() async {
    final source = control.getString("src", "")!;
    await _player.setReleaseMode(ReleaseMode.stop);
    if (_disposed) return;
    // No existsSync, base64 decoding or whole-file copies on the Flutter isolate.
    final uri = Uri.tryParse(source);
    final remote = uri?.scheme == "https" || uri?.scheme == "http";
    await _player.setSource(remote ? UrlSource(source) : DeviceFileSource(source))
        .timeout(const Duration(seconds: 20));
    if (_disposed) return;
    await _player.setVolume(_volume);
    _event("loaded");
  }

  void _event(String name, [dynamic value]) {
    if (!_disposed) control.triggerEvent(name, value);
  }

  @override
  void update() {
    _volume = control.getDouble("volume", 1)!;
    if (!_volumeUpdating && !_disposed) {
      _volumeUpdating = true;
      unawaited(_updateVolume());
    }
  }

  Future<void> _updateVolume() async {
    try {
      await _ready;
      while (!_disposed) {
        final value = _volume;
        await _player.setVolume(value);
        if (_volume == value) break;
      }
    } catch (_) {
      _event("error", {"message": "The native audio player did not respond."});
    } finally {
      _volumeUpdating = false;
    }
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    await _ready;
    if (_disposed) return null;
    switch (name) {
      case "play":
        final position = parseDuration(args["position"]);
        if (position != null) await _player.seek(position);
        if (!_disposed) await _player.resume();
        return null;
      case "resume":
        await _player.resume();
        return null;
      case "pause":
        await _player.pause();
        return null;
      case "seek":
        final position = parseDuration(args["position"]);
        if (position != null) await _player.seek(position);
        return null;
      case "get_duration":
        return await _player.getDuration();
      case "get_current_position":
        return await _player.getCurrentPosition();
      default:
        throw ArgumentError("Unknown ManagedAudio method: $name");
    }
  }

  @override
  void dispose() {
    _disposed = true;
    control.removeInvokeMethodListener(_invokeMethod);
    for (final subscription in _subscriptions) {
      unawaited(subscription.cancel());
    }
    unawaited(_player.dispose().catchError((Object _) {}));
    super.dispose();
  }
}
