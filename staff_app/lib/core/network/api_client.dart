import 'dart:async';
import 'dart:convert';
import 'dart:io' show HttpDate, HttpException;

import 'package:dio/dio.dart';

import '../auth/staff_user.dart';
import '../storage/token_storage.dart';
import 'api_exception.dart';

/// Tokens issued by `/auth/login` and `/auth/refresh`.
class TokenPair {
  const TokenPair({
    required this.accessToken,
    required this.refreshToken,
    required this.user,
    this.expiresIn = const Duration(minutes: 15),
  });

  final String accessToken;
  final String refreshToken;
  final StaffUser user;
  final Duration expiresIn;

  factory TokenPair.fromJson(Map<String, dynamic> json) => TokenPair(
    accessToken: json['access_token'] as String,
    refreshToken: json['refresh_token'] as String,
    user: StaffUser.fromJson(json['user'] as Map<String, dynamic>),
    expiresIn: Duration(seconds: json['expires_in'] as int? ?? 900),
  );
}

/// Outcome of renewing the access token.
enum RenewResult {
  renewed,

  /// The server says the session is over (signed out elsewhere, deactivated, token reused).
  rejected,

  /// The server could not be reached or did not answer properly. The session may well still be
  /// valid, so it is kept: a kitchen tablet must not sign itself out because the Wi-Fi dropped.
  unreachable,
}

/// A session restored at app start. [offline]: the server could not be reached, so [user] is the
/// profile saved at the last sign-in, until the server confirms or ends the session.
class RestoredSession {
  const RestoredSession(this.user, {required this.offline});

  final StaffUser user;
  final bool offline;
}

/// HTTP client for the staff API.
///
/// * Adds the short-lived access token (kept in memory only) to every request.
/// * When it expires, refreshes **once** — concurrent 401s share a single refresh — and
///   retries the request. Refresh tokens rotate on every use, so two parallel refreshes would
///   look like token theft to the server; the single-flight guard prevents that.
/// * Only when the server *rejects* the refresh token is [onSessionExpired] called (the app
///   returns to the login screen). Not reaching the server is reported as a network error and
///   the session is kept.
class ApiClient {
  ApiClient({required String baseUrl, required this._storage, Dio? dio})
    : dio =
          dio ??
          Dio(
            BaseOptions(
              baseUrl: '$baseUrl/api/v1',
              connectTimeout: const Duration(seconds: 8),
              receiveTimeout: const Duration(seconds: 15),
              sendTimeout: const Duration(seconds: 15),
              contentType: Headers.jsonContentType,
              responseType: ResponseType.json,
              listFormat: ListFormat.multi, // status=ready&status=served
            ),
          ) {
    this.dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          final token = _accessToken;
          if (token != null && options.extra['skipAuth'] != true) {
            options.headers['Authorization'] = 'Bearer $token';
          }
          handler.next(options);
        },
        onResponse: (response, handler) {
          _trackServerClock(response.headers.value('date'));
          handler.next(response);
        },
        onError: _onError,
      ),
    );
  }

  final Dio dio;
  final TokenStorage _storage;
  String? _accessToken;
  DateTime? _accessTokenExpiresAt;
  StaffUser? _user;
  Future<RenewResult>? _refreshing;
  final _accessTokens = StreamController<String>.broadcast();

  /// Called when the session can no longer be renewed (logged out elsewhere, deactivated…).
  void Function()? onSessionExpired;

  /// Called whenever a refresh returns fresh user details (role or name may have changed).
  void Function(StaffUser user)? onUserUpdated;

  bool get hasAccessToken => _accessToken != null;

  /// The current access token, for the live-updates socket (which authenticates in its first
  /// message, never in the URL). Kept in memory only.
  String? get accessToken => _accessToken;

  /// When the current access token expires (device clock).
  DateTime? get accessTokenExpiresAt => _accessTokenExpiresAt;

  /// Every newly issued access token (login or refresh), so a live socket can re-authenticate.
  Stream<String> get accessTokens => _accessTokens.stream;

  /// Server clock minus device clock, from the `Date` header of the latest response (±1 s).
  /// Lets screens compare server timestamps with "now" even on a tablet with a wrong clock.
  Duration get serverClockOffset => _serverClockOffset;
  Duration _serverClockOffset = Duration.zero;

  void _trackServerClock(String? date) {
    if (date == null) return;
    try {
      _serverClockOffset = HttpDate.parse(date).difference(DateTime.now());
    } on FormatException {
      // Ignore a malformed header; keep the previous estimate.
    } on HttpException {
      // Same: some proxies rewrite Date in non-standard formats.
    }
  }

  Future<void> _onError(DioException error, ErrorInterceptorHandler handler) async {
    final request = error.requestOptions;
    final unauthorized = error.response?.statusCode == 401;
    if (!unauthorized || request.extra['skipAuth'] == true || request.extra['retried'] == true) {
      handler.next(error);
      return;
    }
    switch (await refresh()) {
      case RenewResult.renewed:
        try {
          request.extra['retried'] = true;
          request.headers['Authorization'] = 'Bearer $_accessToken';
          handler.resolve(await dio.fetch<dynamic>(request));
        } on DioException catch (retryError) {
          handler.next(retryError);
        }
      case RenewResult.rejected:
        onSessionExpired?.call();
        handler.next(error);
      case RenewResult.unreachable:
        // The token could not be renewed right now: report the network problem it is.
        handler.next(DioException.connectionError(requestOptions: request, reason: 'Token renewal failed'));
    }
  }

  /// Renews the access token using the stored refresh token. Concurrent callers share one call.
  Future<RenewResult> refresh() => _refreshing ??= _doRefresh().whenComplete(() => _refreshing = null);

  Future<RenewResult> _doRefresh() async {
    final refreshToken = await _storage.readRefreshToken();
    if (refreshToken == null) return RenewResult.rejected;
    try {
      final response = await dio.post<Map<String, dynamic>>(
        '/auth/refresh',
        data: {'refresh_token': refreshToken},
        options: Options(extra: {'skipAuth': true}),
      );
      final pair = TokenPair.fromJson(response.data!);
      await _accept(pair);
      onUserUpdated?.call(pair.user);
      return RenewResult.renewed;
    } on DioException catch (error) {
      if (error.response?.statusCode == 401) {
        await clear(); // the session is over (logout elsewhere, deactivated, token reused)
        return RenewResult.rejected;
      }
      return RenewResult.unreachable; // offline, timed out, rate limited or a server error
    }
  }

  Future<void> _accept(TokenPair pair) async {
    _accessToken = pair.accessToken;
    _accessTokenExpiresAt = DateTime.now().add(pair.expiresIn);
    _user = pair.user;
    await _storage.writeRefreshToken(pair.refreshToken);
    await _storage.writeUserProfile(jsonEncode(pair.user.toJson()));
    _accessTokens.add(pair.accessToken);
  }

  Future<StaffUser> login({
    required String restaurantCode,
    required String username,
    required String password,
  }) async {
    final response = await _send(
      () => dio.post<Map<String, dynamic>>(
        '/auth/login',
        data: {'restaurant_code': restaurantCode, 'username': username, 'password': password},
        options: Options(extra: {'skipAuth': true}),
      ),
    );
    final pair = TokenPair.fromJson(response.data!);
    await _accept(pair);
    await _storage.writeRestaurantCode(restaurantCode.trim().toLowerCase());
    return pair.user;
  }

  /// Restores a session at app start from the stored refresh token.
  ///
  /// When the server can't be reached, the profile saved at sign-in is used (offline), so a
  /// kitchen tablet that restarts during an outage can keep showing its saved board. This
  /// unlocks nothing on the server — every request still needs a valid token — and the session
  /// is confirmed or ended as soon as the server answers.
  Future<RestoredSession?> restore() async {
    switch (await refresh()) {
      case RenewResult.renewed:
        return RestoredSession(_user!, offline: false);
      case RenewResult.rejected:
        return null;
      case RenewResult.unreachable:
        final saved = await _savedUser();
        if (saved == null) return null;
        _user = saved;
        return RestoredSession(saved, offline: true);
    }
  }

  Future<StaffUser?> _savedUser() async {
    final json = await _storage.readUserProfile();
    if (json == null) return null;
    try {
      return StaffUser.fromJson(jsonDecode(json) as Map<String, dynamic>);
    } catch (_) {
      return null; // unreadable (e.g. an older app version): sign in again when online
    }
  }

  Future<void> logout() async {
    try {
      if (_accessToken != null) await dio.post<void>('/auth/logout');
    } on DioException {
      // Logging out locally matters most; the server session expires on its own anyway.
    } finally {
      await clear();
    }
  }

  Future<void> clear() async {
    _accessToken = null;
    _accessTokenExpiresAt = null;
    _user = null;
    await _storage.clearSession();
  }

  // --- typed helpers that translate errors into ApiException ---------------------------------

  Future<Response<T>> _send<T>(Future<Response<T>> Function() call) async {
    try {
      return await call();
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<T> getJson<T>(String path, {Map<String, dynamic>? query}) async =>
      (await _send(() => dio.get<T>(path, queryParameters: query))).data as T;

  Future<T> postJson<T>(String path, {Object? data, Map<String, String>? headers}) async =>
      (await _send(
            () => dio.post<T>(
              path,
              data: data ?? const {},
              options: Options(headers: headers),
            ),
          )).data
          as T;

  Future<Response<T>> postResponse<T>(String path, {Object? data, Map<String, String>? headers}) => _send(
    () => dio.post<T>(
      path,
      data: data ?? const {},
      options: Options(headers: headers),
    ),
  );

  Future<T> patchJson<T>(String path, {required Object data}) async =>
      (await _send(() => dio.patch<T>(path, data: data))).data as T;

  Future<T> putJson<T>(String path, {required Object data}) async =>
      (await _send(() => dio.put<T>(path, data: data))).data as T;
}
