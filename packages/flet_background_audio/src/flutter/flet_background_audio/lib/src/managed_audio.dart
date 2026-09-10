import "dart:async";

import "package:audioplayers/audioplayers.dart";
import "package:flet/flet.dart";

/// Melody's long-lived native source boundary.
///
/// Candidate and preloaded players prepare away from the active decoder. A
/// source switch swaps players only after preparation succeeds, so Flutter UI
/// patches, filesystem checks, and decoder setup never interrupt the current
/// track or block the UI isolate.
class ManagedAudioService extends FletService {
  ManagedAudioService({required super.control});

  AudioPlayer? _activePlayer;
  AudioPlayer? _retiringPlayer;
  Future<AudioPlayer>? _preloadFuture;
  String? _preloadedSource;
  final List<StreamSubscription<dynamic>> _subscriptions = [];
  bool _disposed = false;
  double _volume = 1;
  bool _volumeUpdating = false;
  int _positionSecond = -1;
  int _loadToken = 0;
  int _preloadToken = 0;

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(_invokeMethod);
    _volume = control.getDouble("volume", 1)!;
    final source = control.getString("src", "")!.trim();
    if (source.isNotEmpty) {
      unawaited(_loadSource(source, 0).catchError((Object error) {
        _event("error", {
          "message": "The native audio source could not be prepared.",
        });
      }));
    }
  }

  Source _source(String source) {
    final uri = Uri.tryParse(source);
    final remote = uri?.scheme == "https" || uri?.scheme == "http";
    return remote ? UrlSource(source) : DeviceFileSource(source);
  }

  Future<AudioPlayer> _preparePlayer(String source) async {
    final player = AudioPlayer();
    try {
      await player.setReleaseMode(ReleaseMode.stop);
      await player
          .setSource(_source(source))
          .timeout(const Duration(seconds: 20));
      await player.setVolume(_volume);
      return player;
    } catch (_) {
      unawaited(player.dispose().catchError((Object _) {}));
      rethrow;
    }
  }

  Future<void> _loadSource(String source, int generation) async {
    final token = ++_loadToken;
    final Future<AudioPlayer> candidateFuture;
    if (_preloadedSource == source && _preloadFuture != null) {
      candidateFuture = _preloadFuture!;
      _preloadFuture = null;
      _preloadedSource = null;
      _preloadToken++;
    } else {
      await _cancelPreload();
      candidateFuture = _preparePlayer(source);
    }

    final candidate = await candidateFuture;
    if (_disposed || token != _loadToken) {
      unawaited(candidate.dispose().catchError((Object _) {}));
      return;
    }
    await _activatePlayer(candidate, generation, token);
  }

  Future<void> _activatePlayer(
    AudioPlayer candidate,
    int generation,
    int token,
  ) async {
    if (_disposed || token != _loadToken) {
      unawaited(candidate.dispose().catchError((Object _) {}));
      return;
    }
    // Everything through the loaded event is synchronous on the Dart isolate.
    // Detaching the retiring stream must not yield: a newer load could
    // otherwise invalidate this candidate after the old subscriptions were
    // cancelled but before the new player was installed.
    _cancelSubscriptionsSoon();
    final previous = _activePlayer;
    if (_retiringPlayer == null) {
      _retiringPlayer = previous;
    } else if (previous != null && previous != _retiringPlayer) {
      unawaited(previous.dispose().catchError((Object _) {}));
    }
    _activePlayer = candidate;
    _positionSecond = -1;

    _subscriptions.add(candidate.onDurationChanged.listen((value) {
      _event("duration_change", {
        "duration": value.inMilliseconds,
        "generation": generation,
      });
    }));
    _subscriptions.add(candidate.onPlayerStateChanged.listen((value) {
      _event("state_change", {
        "state": value.name,
        "generation": generation,
      });
    }));
    _subscriptions.add(candidate.onPositionChanged.listen((value) {
      final second = value.inMilliseconds ~/ 1000;
      if (second != _positionSecond) {
        _positionSecond = second;
        _event("position_change", {
          "position": value.inMilliseconds,
          "generation": generation,
        });
      }
    }));
    _subscriptions.add(candidate.onPlayerComplete.listen((_) {
      _event("state_change", {
        "state": "completed",
        "generation": generation,
      });
    }));

    if (!_disposed && token == _loadToken && candidate == _activePlayer) {
      _event("loaded", {"generation": generation});
    }
    final duration = await candidate.getDuration();
    if (duration != null &&
        !_disposed &&
        token == _loadToken &&
        candidate == _activePlayer) {
      _event("duration_change", {
        "duration": duration.inMilliseconds,
        "generation": generation,
      });
    }
  }

  Future<void> _preloadSource(String source) async {
    await _cancelPreload();
    if (_disposed) return;
    final token = ++_preloadToken;
    final future = _preparePlayer(source);
    _preloadedSource = source;
    _preloadFuture = future;
    try {
      final player = await future;
      if (_disposed || token != _preloadToken) {
        // A consumed future is owned by _loadSource; cancellation owns all
        // other obsolete candidates.
        if (_preloadFuture == future) {
          _preloadFuture = null;
          _preloadedSource = null;
          unawaited(player.dispose().catchError((Object _) {}));
        }
      }
    } catch (_) {
      if (_preloadFuture == future) {
        _preloadFuture = null;
        _preloadedSource = null;
      }
      rethrow;
    }
  }

  Future<void> _cancelPreload() async {
    _preloadToken++;
    final future = _preloadFuture;
    _preloadFuture = null;
    _preloadedSource = null;
    if (future == null) return;
    unawaited(future.then((player) async {
      if (player != _activePlayer) await player.dispose();
    }).catchError((Object _) {}));
  }

  Future<void> _retirePrevious() async {
    final previous = _retiringPlayer;
    _retiringPlayer = null;
    if (previous == null) return;
    try {
      await previous.pause();
    } finally {
      await previous.dispose();
    }
  }

  Future<void> _clearSource() async {
    _loadToken++;
    await _cancelPreload();
    await _cancelSubscriptions();
    final active = _activePlayer;
    final retiring = _retiringPlayer;
    _activePlayer = null;
    _retiringPlayer = null;
    await Future.wait([
      if (active != null) active.dispose(),
      if (retiring != null && retiring != active) retiring.dispose(),
    ]);
  }

  Future<void> _cancelSubscriptions() async {
    final subscriptions =
        List<StreamSubscription<dynamic>>.from(_subscriptions);
    _subscriptions.clear();
    await Future.wait(subscriptions.map((subscription) => subscription.cancel()));
  }

  void _cancelSubscriptionsSoon() {
    final subscriptions =
        List<StreamSubscription<dynamic>>.from(_subscriptions);
    _subscriptions.clear();
    for (final subscription in subscriptions) {
      unawaited(subscription.cancel().catchError((Object _) {}));
    }
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
      while (!_disposed) {
        final value = _volume;
        final players = <AudioPlayer>{
          if (_activePlayer != null) _activePlayer!,
          if (_retiringPlayer != null) _retiringPlayer!,
        };
        await Future.wait(players.map((player) => player.setVolume(value)));
        if (_volume == value) break;
      }
    } catch (_) {
      _event("error", {"message": "The native audio player did not respond."});
    } finally {
      _volumeUpdating = false;
    }
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    if (_disposed) return null;
    switch (name) {
      case "load_source":
        final data = Map<String, dynamic>.from(args as Map);
        await _loadSource(
          data["source"] as String,
          (data["generation"] as num?)?.toInt() ?? 0,
        );
        return null;
      case "preload_source":
        final data = Map<String, dynamic>.from(args as Map);
        await _preloadSource(data["source"] as String);
        return null;
      case "cancel_preload":
        await _cancelPreload();
        return null;
      case "cancel_load":
        _loadToken++;
        return null;
      case "clear_source":
        await _clearSource();
        return null;
      case "play":
        final player = _activePlayer;
        if (player == null) return null;
        final position = parseDuration(args["position"]);
        if (position != null) await player.seek(position);
        await _retirePrevious();
        if (!_disposed && player == _activePlayer) await player.resume();
        return null;
      case "resume":
        final player = _activePlayer;
        if (player == null) return null;
        await _retirePrevious();
        if (!_disposed && player == _activePlayer) await player.resume();
        return null;
      case "pause":
        await Future.wait([
          if (_activePlayer != null) _activePlayer!.pause(),
          if (_retiringPlayer != null) _retiringPlayer!.pause(),
        ]);
        await _retirePrevious();
        return null;
      case "seek":
        final position = parseDuration(args["position"]);
        final player = _activePlayer;
        if (position != null && player != null) await player.seek(position);
        return null;
      case "get_duration":
        return await _activePlayer?.getDuration();
      case "get_current_position":
        return await _activePlayer?.getCurrentPosition();
      default:
        throw ArgumentError("Unknown ManagedAudio method: $name");
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _loadToken++;
    _preloadToken++;
    control.removeInvokeMethodListener(_invokeMethod);
    unawaited(_clearSource().catchError((Object _) {}));
    super.dispose();
  }
}
