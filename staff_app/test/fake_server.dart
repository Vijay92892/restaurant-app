import 'dart:async';
import 'dart:convert';
import 'dart:io' show HttpDate, SocketException;
import 'dart:typed_data';

import 'package:dio/dio.dart';

/// A scripted fake server: returns 401 for stale access tokens, rotates refresh tokens.
class FakeServer implements HttpClientAdapter {
  String validAccess = 'access-1';
  String validRefresh = 'refresh-1';
  int refreshCalls = 0;
  bool refreshRejects = false;
  final List<String> seen = [];

  Map<String, dynamic> _user() => {
    'id': 'u1',
    'restaurant_id': 'r1',
    'restaurant_name': 'Spice Garden',
    'restaurant_code': 'spice-garden',
    'username': 'ravi',
    'display_name': 'Ravi',
    'role': 'waiter',
    'must_change_password': false,
    'permissions': ['orders.read', 'orders.create'],
  };

  /// Sent as the HTTP Date header when set.
  DateTime? serverDate;

  /// The network is down: every request fails before reaching the server.
  bool offline = false;

  /// Only token renewals fail to get through (e.g. Wi-Fi dropped right after a 401).
  bool refreshUnreachable = false;

  ResponseBody _json(int status, Object body) => ResponseBody.fromString(
    jsonEncode(body),
    status,
    headers: {
      Headers.contentTypeHeader: [Headers.jsonContentType],
      if (serverDate != null) 'date': [HttpDate.format(serverDate!)],
    },
  );

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    seen.add('${options.method} ${options.path}');
    await Future<void>.delayed(const Duration(milliseconds: 5));
    if (offline || (refreshUnreachable && options.path == '/auth/refresh')) {
      throw const SocketException('Network is unreachable');
    }
    if (options.path == '/auth/refresh') {
      refreshCalls++;
      final body = options.data as Map<String, dynamic>;
      if (refreshRejects || body['refresh_token'] != validRefresh) {
        return _json(401, {
          'error': {'code': 'session_expired', 'message': 'Your session has expired.'},
        });
      }
      validAccess = 'access-${refreshCalls + 1}';
      validRefresh = 'refresh-${refreshCalls + 1}';
      return _json(200, {
        'access_token': validAccess,
        'refresh_token': validRefresh,
        'expires_in': 900,
        'user': _user(),
      });
    }
    if (options.headers['Authorization'] != 'Bearer $validAccess') {
      return _json(401, {
        'error': {'code': 'token_expired', 'message': 'Session expired.'},
      });
    }
    return _json(200, [
      {'ok': options.path},
    ]);
  }

  @override
  void close({bool force = false}) {}
}
