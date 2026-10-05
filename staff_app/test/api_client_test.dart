import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:staff_app/core/auth/staff_user.dart';
import 'package:staff_app/core/network/api_client.dart';
import 'package:staff_app/core/network/api_exception.dart';
import 'package:staff_app/core/storage/token_storage.dart';

import 'fake_server.dart';

void main() {
  late FakeServer server;
  late MemoryTokenStorage storage;
  late ApiClient api;

  setUp(() {
    server = FakeServer();
    storage = MemoryTokenStorage()..refreshToken = 'refresh-1';
    final dio = Dio(BaseOptions(baseUrl: 'http://test/api/v1'))..httpClientAdapter = server;
    api = ApiClient(baseUrl: 'http://test', storage: storage, dio: dio);
  });

  test('restores a session from the stored refresh token and rotates it', () async {
    final restored = await api.restore();
    expect(restored?.user.displayName, 'Ravi');
    expect(restored?.offline, isFalse);
    expect(storage.refreshToken, 'refresh-2');
    expect(storage.userProfile, contains('"display_name":"Ravi"')); // for offline restarts
  });

  test('starting offline uses the profile saved at sign-in and keeps the session', () async {
    await api.restore(); // signed in once, online
    server.offline = true;
    final restarted = ApiClient(
      baseUrl: 'http://test',
      storage: storage,
      dio: Dio(BaseOptions(baseUrl: 'http://test/api/v1'))..httpClientAdapter = server,
    );
    final restored = await restarted.restore();
    expect(restored?.offline, isTrue);
    expect(restored?.user.role, StaffRole.waiter);
    expect(restarted.hasAccessToken, isFalse); // nothing works on the server until it answers
    expect(storage.refreshToken, 'refresh-2');

    server.offline = false; // back online: the first request renews and confirms the session
    expect(await restarted.getJson<List<dynamic>>('/tables'), isNotEmpty);
    expect(restarted.hasAccessToken, isTrue);
  });

  test('starting offline with nothing saved asks the user to sign in, keeping the token', () async {
    server.offline = true;
    expect(await api.restore(), isNull);
    expect(storage.refreshToken, 'refresh-1'); // not thrown away: the server never rejected it
  });

  test('a network failure while renewing reports a network error, never a sign-out', () async {
    await api.restore();
    var expired = false;
    api.onSessionExpired = () => expired = true;
    server
      ..validAccess = 'expired-meanwhile'
      ..refreshUnreachable = true;
    await expectLater(
      api.getJson<List<dynamic>>('/tables'),
      throwsA(isA<ApiException>().having((e) => e.isNetwork, 'isNetwork', isTrue)),
    );
    expect(expired, isFalse);
    expect(storage.refreshToken, 'refresh-2');
    expect(await api.refresh(), RenewResult.unreachable);

    server.refreshUnreachable = false;
    expect(await api.refresh(), RenewResult.renewed);
  });

  test('concurrent 401s trigger exactly one refresh, then every request is retried', () async {
    await api.restore(); // access-2
    server.validAccess = 'access-rotated-elsewhere'; // the current access token has now expired
    server.validRefresh = storage.refreshToken!;
    final results = await Future.wait([
      api.getJson<List<dynamic>>('/tables'),
      api.getJson<List<dynamic>>('/orders'),
      api.getJson<List<dynamic>>('/pin'),
    ]);
    expect(results.length, 3);
    expect(server.refreshCalls, 2); // one at restore, exactly one for the three expired calls
  });

  test('when the refresh token is rejected the session ends cleanly', () async {
    await api.restore();
    var expired = false;
    api.onSessionExpired = () => expired = true;
    server
      ..validAccess = 'gone'
      ..refreshRejects = true;
    await expectLater(
      api.getJson<List<dynamic>>('/tables'),
      throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', 401)),
    );
    expect(expired, isTrue);
    expect(storage.refreshToken, isNull); // nothing left to replay
    expect(storage.userProfile, isNull);
    expect(api.hasAccessToken, isFalse);
    expect(await api.refresh(), RenewResult.rejected);
  });

  test('tracks the server clock from the Date header', () async {
    server.serverDate = DateTime.now().toUtc().add(const Duration(minutes: 7));
    await api.restore();
    expect(api.serverClockOffset.inMinutes, inInclusiveRange(6, 7));
    expect(api.accessTokenExpiresAt!.isAfter(DateTime.now().add(const Duration(minutes: 14))), isTrue);
  });

  test('every new access token is announced (for the live connection)', () async {
    final tokens = <String>[];
    api.accessTokens.listen(tokens.add);
    await api.restore();
    await api.refresh();
    await Future<void>.delayed(Duration.zero);
    expect(tokens, ['access-2', 'access-3']);
  });

  test('network failures become a friendly ApiException', () async {
    final dio = Dio(
      BaseOptions(baseUrl: 'http://127.0.0.1:9/api/v1', connectTimeout: const Duration(milliseconds: 300)),
    );
    final offline = ApiClient(baseUrl: 'http://127.0.0.1:9', storage: MemoryTokenStorage(), dio: dio);
    await expectLater(
      offline.getJson<List<dynamic>>('/tables'),
      throwsA(isA<ApiException>().having((e) => e.isNetwork, 'isNetwork', isTrue)),
    );
  });
}
