import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../network/api_client.dart';
import 'live_updates.dart';

/// The bits of a WebSocket the client uses (a seam for tests).
abstract interface class LiveSocket {
  Future<void> get ready;
  Stream<dynamic> get stream;
  int? get closeCode;
  void send(String text);
  Future<void> close([int? code]);
}

typedef LiveSocketFactory = LiveSocket Function(Uri uri);

class _ChannelSocket implements LiveSocket {
  _ChannelSocket(Uri uri) : _channel = WebSocketChannel.connect(uri);

  final WebSocketChannel _channel;

  @override
  Future<void> get ready => _channel.ready;

  @override
  Stream<dynamic> get stream => _channel.stream;

  @override
  int? get closeCode => _channel.closeCode;

  @override
  void send(String text) => _channel.sink.add(text);

  @override
  Future<void> close([int? code]) async => _channel.sink.close(code);
}

/// Server close codes (see the backend's docs/04-api-contracts.md §8).
abstract final class LiveCloseCode {
  static const unauthorized = 4401;
  static const forbidden = 4403;
  static const resync = 4408;
  static const tooMany = 4429;
}

/// Keeps one authenticated WebSocket to `/api/v1/ws/staff` open while a staff member is
/// signed in, and turns its messages into [LiveEvent]s.
///
/// * The access token is sent in the first message, never in the URL.
/// * Reconnects with capped, jittered exponential backoff; after every (re)connection it emits
///   [LiveEvent.resync] so screens reload — events missed while offline are never assumed.
/// * A heartbeat detects half-open connections (common on restaurant Wi-Fi) within seconds.
/// * Renews the access token shortly before it expires and re-authenticates the open socket,
///   so a kitchen tablet that makes few REST calls is not disconnected every 15 minutes.
/// * Drops duplicate events (same event id).
class RealtimeClient implements LiveUpdates {
  RealtimeClient({
    required this._api,
    LiveSocketFactory? connect,
    this.pingInterval = const Duration(seconds: 25),
    this.pongTimeout = const Duration(seconds: 10),
    this.maxBackoff = const Duration(seconds: 30),
    this.renewBefore = const Duration(seconds: 90),
    Random? random,
  }) : _connect = connect ?? _ChannelSocket.new,
       _random = random ?? Random();

  final ApiClient _api;
  final LiveSocketFactory _connect;
  final Random _random;
  final Duration pingInterval;
  final Duration pongTimeout;
  final Duration maxBackoff;
  final Duration renewBefore;

  final _events = StreamController<LiveEvent>.broadcast();
  final _status = ValueNotifier(LiveStatus.offline);
  final _seen = <String>{}; // insertion-ordered, so the oldest ids are dropped first

  bool _running = false;
  LiveSocket? _socket;
  Completer<void>? _wake;
  StreamSubscription<String>? _tokens;
  Timer? _renewTimer;

  @override
  Stream<LiveEvent> get events => _events.stream;

  @override
  ValueListenable<LiveStatus> get status => _status;

  Uri get socketUri {
    final api = Uri.parse(_api.dio.options.baseUrl); // e.g. https://host/api/v1
    return api.replace(
      scheme: api.scheme == 'https' ? 'wss' : 'ws',
      path: '${api.path.replaceAll(RegExp(r'/+$'), '')}/ws/staff',
    );
  }

  /// Starts (or keeps) the connection. Safe to call repeatedly.
  void start() {
    if (_running) return;
    _running = true;
    _tokens = _api.accessTokens.listen(_onNewToken);
    _scheduleRenewal();
    unawaited(_run());
  }

  /// Closes the connection (sign-out). Screens keep what they have.
  Future<void> stop() async {
    _running = false;
    await _tokens?.cancel();
    _tokens = null;
    _renewTimer?.cancel();
    _wakeUp();
    await _socket?.close(1000);
    _socket = null;
    _status.value = LiveStatus.offline;
  }

  /// Skips the backoff wait (e.g. the app came back to the foreground).
  void reconnectNow() => _wakeUp();

  Future<void> dispose() async {
    await stop();
    await _events.close();
    _status.dispose();
  }

  // --- connection loop -------------------------------------------------------------------------

  Future<void> _run() async {
    var failures = 0;
    while (_running) {
      _status.value = LiveStatus.connecting;
      final (code, wasLive) = await _session();
      if (!_running) break;
      _status.value = LiveStatus.offline;
      if (wasLive) failures = 0;
      Duration wait;
      switch (code) {
        case LiveCloseCode.unauthorized:
          switch (await _api.refresh()) {
            case RenewResult.renewed:
              wait = Duration.zero;
            case RenewResult.rejected:
              // The session is over (signed out elsewhere, deactivated): nothing to reconnect to.
              _api.onSessionExpired?.call();
              _running = false;
              continue;
            case RenewResult.unreachable:
              wait = _backoff(failures++); // offline: the renewal itself could not get through
          }
        case LiveCloseCode.forbidden:
          _running = false;
          continue;
        case LiveCloseCode.resync:
          wait = Duration.zero;
        case LiveCloseCode.tooMany:
          wait = maxBackoff;
        default:
          wait = _backoff(failures++);
      }
      await _sleep(wait);
    }
    _status.value = LiveStatus.offline;
  }

  /// Runs one connection until it ends. Returns the close code (null if the network failed)
  /// and whether it got as far as being live.
  Future<(int?, bool)> _session() async {
    var token = _api.accessToken;
    if (token == null) {
      // E.g. the app started offline: renew first. Unreachable is a network failure, never a
      // reason to sign anyone out.
      switch (await _api.refresh()) {
        case RenewResult.renewed:
          token = _api.accessToken!;
        case RenewResult.rejected:
          return (LiveCloseCode.unauthorized, false);
        case RenewResult.unreachable:
          return (null, false);
      }
    }
    final LiveSocket socket;
    try {
      socket = _connect(socketUri);
      await socket.ready.timeout(const Duration(seconds: 10));
    } catch (_) {
      return (null, false);
    }
    if (!_running) {
      await socket.close(1000);
      return (null, false);
    }
    _socket = socket;
    var live = false;
    final ended = Completer<int?>();
    Timer? heartbeat;
    Timer? deadline;

    void finish(int? code) {
      if (!ended.isCompleted) ended.complete(code);
    }

    final subscription = socket.stream.listen(
      (data) {
        deadline?.cancel(); // any message proves the connection is alive
        deadline = null;
        final message = _decode(data);
        if (message == null) return;
        switch (message['type']) {
          case 'ready':
            live = true;
            _status.value = LiveStatus.live;
            _events.add(LiveEvent.resync);
            heartbeat = Timer.periodic(pingInterval, (_) {
              _send(socket, {'type': 'ping'});
              // Keep an outstanding deadline: only a message from the server clears it.
              deadline ??= Timer(pongTimeout, () {
                unawaited(socket.close(4000));
                finish(null);
              });
            });
          case 'pong' || 'reauthenticated':
            break;
          default:
            if (message['event_id'] is String) _emit(message);
        }
      },
      onDone: () => finish(socket.closeCode),
      onError: (_) => finish(null),
      cancelOnError: true,
    );
    _send(socket, {'type': 'auth', 'access_token': token});

    final code = await ended.future;
    heartbeat?.cancel();
    deadline?.cancel();
    await subscription.cancel();
    if (identical(_socket, socket)) _socket = null;
    return (code, live);
  }

  void _emit(Map<String, dynamic> message) {
    final LiveEvent event;
    try {
      event = LiveEvent.fromJson(message);
    } catch (_) {
      return; // malformed: ignore rather than crash the stream
    }
    if (!_seen.add(event.id)) return;
    if (_seen.length > 512) _seen.remove(_seen.first);
    _events.add(event);
  }

  static Map<String, dynamic>? _decode(dynamic data) {
    if (data is! String) return null;
    try {
      final value = jsonDecode(data);
      return value is Map<String, dynamic> ? value : null;
    } on FormatException {
      return null;
    }
  }

  static void _send(LiveSocket socket, Map<String, dynamic> message) {
    try {
      socket.send(jsonEncode(message));
    } catch (_) {
      // The socket is closing; the stream's onDone handles it.
    }
  }

  // --- tokens ----------------------------------------------------------------------------------

  void _onNewToken(String token) {
    final socket = _socket;
    if (socket != null && _status.value == LiveStatus.live) {
      _send(socket, {'type': 'auth', 'access_token': token});
    }
    _scheduleRenewal();
  }

  void _scheduleRenewal() {
    _renewTimer?.cancel();
    final expiresAt = _api.accessTokenExpiresAt;
    if (!_running || expiresAt == null) return;
    var wait = expiresAt.subtract(renewBefore).difference(DateTime.now());
    if (wait < const Duration(seconds: 5)) wait = const Duration(seconds: 5);
    _renewTimer = Timer(wait, () async {
      if (!_running) return;
      switch (await _api.refresh()) {
        case RenewResult.renewed:
          break; // the new token schedules the next renewal
        case RenewResult.rejected:
          _api.onSessionExpired?.call();
        case RenewResult.unreachable:
          // Offline right now: try again soon; a 4401 close will also trigger a refresh.
          _renewTimer = Timer(const Duration(seconds: 20), _scheduleRenewal);
      }
    });
  }

  // --- waiting ---------------------------------------------------------------------------------

  Duration _backoff(int failures) {
    final base = Duration(seconds: 1 << min(failures, 5)); // 1, 2, 4, 8, 16, 32 s
    final capped = base > maxBackoff ? maxBackoff : base;
    // Full jitter: tablets that lost Wi-Fi together don't all reconnect in the same instant.
    return Duration(milliseconds: (capped.inMilliseconds * (0.5 + _random.nextDouble() / 2)).round());
  }

  Future<void> _sleep(Duration wait) async {
    if (wait <= Duration.zero || !_running) return;
    final wake = _wake = Completer<void>();
    final timer = Timer(wait, () {
      if (!wake.isCompleted) wake.complete();
    });
    await wake.future;
    timer.cancel();
    _wake = null;
  }

  void _wakeUp() {
    final wake = _wake;
    if (wake != null && !wake.isCompleted) wake.complete();
  }
}
