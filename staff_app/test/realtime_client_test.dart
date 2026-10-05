import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:staff_app/core/network/api_client.dart';
import 'package:staff_app/core/realtime/live_updates.dart';
import 'package:staff_app/core/realtime/realtime_client.dart';
import 'package:staff_app/core/storage/token_storage.dart';

import 'fake_server.dart';

/// A scripted server-side socket.
class FakeSocket implements LiveSocket {
  FakeSocket(this.uri);

  final Uri uri;
  final sent = <Map<String, dynamic>>[];
  final _incoming = StreamController<dynamic>();
  int? _closeCode;
  bool get isClosed => _incoming.isClosed;

  @override
  Future<void> get ready async {}

  @override
  Stream<dynamic> get stream => _incoming.stream;

  @override
  int? get closeCode => _closeCode;

  @override
  void send(String text) => sent.add(jsonDecode(text) as Map<String, dynamic>);

  @override
  Future<void> close([int? code]) async => serverClose(code ?? 1000);

  void push(Map<String, dynamic> message) => _incoming.add(jsonEncode(message));
  void pushRaw(String text) => _incoming.add(text);

  void serverClose(int code) {
    if (isClosed) return;
    _closeCode = code;
    _incoming.close();
  }
}

Map<String, dynamic> event(String id, {String type = 'order.updated'}) => {
  'event_id': id,
  'type': type,
  'restaurant_id': 'r1',
  'order_id': 'o1',
  'order_version': 2,
  'occurred_at': '2026-10-04T19:00:00Z',
  'payload': <String, dynamic>{},
};

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 30));

void main() {
  late FakeServer server;
  late ApiClient api;
  late List<FakeSocket> sockets;
  late RealtimeClient client;
  late List<LiveEvent> events;

  RealtimeClient build({
    Duration ping = const Duration(seconds: 25),
    Duration pong = const Duration(seconds: 10),
  }) {
    final c = RealtimeClient(
      api: api,
      connect: (uri) {
        final socket = FakeSocket(uri);
        sockets.add(socket);
        return socket;
      },
      pingInterval: ping,
      pongTimeout: pong,
      maxBackoff: const Duration(milliseconds: 20),
      random: Random(1),
    );
    c.events.listen(events.add);
    return c;
  }

  setUp(() async {
    server = FakeServer();
    final dio = Dio(BaseOptions(baseUrl: 'https://api.example.test/api/v1'))..httpClientAdapter = server;
    api = ApiClient(
      baseUrl: 'https://api.example.test',
      storage: MemoryTokenStorage()..refreshToken = 'refresh-1',
      dio: dio,
    );
    await api.restore(); // access-2
    sockets = [];
    events = [];
    client = build();
  });

  tearDown(() => client.dispose());

  test('connects to the tenant socket over TLS and authenticates in the first message', () async {
    expect(client.socketUri.toString(), 'wss://api.example.test/api/v1/ws/staff');
    client.start();
    await settle();
    final socket = sockets.single;
    expect(socket.uri.query, isEmpty); // the token is never in the URL
    expect(socket.sent.first, {'type': 'auth', 'access_token': 'access-2'});
    expect(client.status.value, LiveStatus.connecting);

    socket.push({'type': 'ready', 'restaurant_id': 'r1'});
    await settle();
    expect(client.status.value, LiveStatus.live);
    expect(events.single.isResync, isTrue); // reload everything after connecting
  });

  test('delivers events once, ignoring duplicates and garbage', () async {
    client.start();
    await settle();
    final socket = sockets.single..push({'type': 'ready'});
    socket
      ..push(event('e1'))
      ..push(event('e1')) // redelivered
      ..pushRaw('not json')
      ..push({'type': 'unknown-without-id'})
      ..push(event('e2', type: 'pin.rotated'));
    await settle();
    expect([for (final e in events) e.type], ['resync', 'order.updated', 'pin.rotated']);
    expect(events[1].orderId, 'o1');
  });

  test('reconnects after a drop and resyncs', () async {
    client.start();
    await settle();
    sockets.single
      ..push({'type': 'ready'})
      ..serverClose(1006);
    await settle();
    expect(client.status.value, isNot(LiveStatus.live));
    await Future<void>.delayed(const Duration(milliseconds: 60));
    expect(sockets, hasLength(2));
    sockets.last.push({'type': 'ready'});
    await settle();
    expect(client.status.value, LiveStatus.live);
    expect(events.where((e) => e.isResync), hasLength(2));
  });

  test('an expired token is renewed and the socket re-opened with the new one', () async {
    client.start();
    await settle();
    sockets.single.serverClose(LiveCloseCode.unauthorized);
    await settle();
    expect(server.refreshCalls, 2);
    expect(sockets, hasLength(2));
    expect(sockets.last.sent.first['access_token'], 'access-3');
  });

  test('a session that ended stops the client and signs the user out', () async {
    var expired = false;
    api.onSessionExpired = () => expired = true;
    client.start();
    await settle();
    server.refreshRejects = true;
    sockets.single.serverClose(LiveCloseCode.unauthorized);
    await settle();
    await Future<void>.delayed(const Duration(milliseconds: 60));
    expect(expired, isTrue);
    expect(sockets, hasLength(1)); // no reconnect storm
    expect(client.status.value, LiveStatus.offline);
  });

  test('an app that starts offline keeps trying, never signs out, and connects once back', () async {
    await client.dispose();
    // A restart during an outage: a stored refresh token, no access token yet.
    api = ApiClient(
      baseUrl: 'https://api.example.test',
      storage: MemoryTokenStorage()..refreshToken = server.validRefresh,
      dio: Dio(BaseOptions(baseUrl: 'https://api.example.test/api/v1'))..httpClientAdapter = server,
    );
    var expired = false;
    api.onSessionExpired = () => expired = true;
    server.offline = true;
    client = build()..start();
    await Future<void>.delayed(const Duration(milliseconds: 100));
    expect(sockets, isEmpty);
    expect(expired, isFalse);
    expect(client.status.value, isNot(LiveStatus.live));

    server.offline = false;
    await Future<void>.delayed(const Duration(milliseconds: 100));
    expect(sockets, hasLength(1));
    expect(sockets.single.sent.first['type'], 'auth');
    expect(expired, isFalse);
  });

  test('"fell behind" reconnects straight away', () async {
    client.start();
    await settle();
    sockets.single
      ..push({'type': 'ready'})
      ..serverClose(LiveCloseCode.resync);
    await settle();
    expect(sockets, hasLength(2));
  });

  test('a silent (half-open) connection is detected by the heartbeat', () async {
    await client.dispose();
    client = build(ping: const Duration(milliseconds: 40), pong: const Duration(milliseconds: 40));
    client.start();
    await settle();
    sockets.single.push({'type': 'ready'});
    await Future<void>.delayed(const Duration(milliseconds: 150));
    expect(sockets.first.sent.any((m) => m['type'] == 'ping'), isTrue);
    expect(sockets.first.isClosed, isTrue);
    expect(sockets.length, greaterThanOrEqualTo(2));
  });

  test('a renewed access token re-authenticates the open socket', () async {
    client.start();
    await settle();
    final socket = sockets.single..push({'type': 'ready'});
    await settle();
    await api.refresh(); // e.g. a REST call renewed it
    await settle();
    expect(socket.sent.last, {'type': 'auth', 'access_token': 'access-3'});
    expect(sockets, hasLength(1));
  });

  test('stop closes the socket and stays offline', () async {
    client.start();
    await settle();
    sockets.single.push({'type': 'ready'});
    await settle();
    await client.stop();
    await Future<void>.delayed(const Duration(milliseconds: 60));
    expect(sockets.single.isClosed, isTrue);
    expect(sockets, hasLength(1));
    expect(client.status.value, LiveStatus.offline);
  });
}
