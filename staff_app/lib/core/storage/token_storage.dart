import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Where credentials live on the device.
///
/// * The refresh token is kept in the platform keystore (Android Keystore / Windows DPAPI)
///   via flutter_secure_storage.
/// * The access token is never written to disk — it lives in memory only.
/// * The signed-in user's profile (name, role, permissions — no secrets) is kept next to the
///   refresh token, so a device that restarts while offline knows who was signed in. It is
///   removed together with the refresh token.
/// * The restaurant code (not a secret) is remembered for convenience.
abstract class TokenStorage {
  Future<String?> readRefreshToken();
  Future<void> writeRefreshToken(String token);
  Future<String?> readUserProfile();
  Future<void> writeUserProfile(String json);

  /// Forgets the refresh token and the user profile (sign-out, or the session ended).
  Future<void> clearSession();
  Future<String?> readRestaurantCode();
  Future<void> writeRestaurantCode(String code);
}

class SecureTokenStorage implements TokenStorage {
  SecureTokenStorage({FlutterSecureStorage? secure}) : _secure = secure ?? const FlutterSecureStorage();

  static const _refreshKey = 'refresh_token';
  static const _profileKey = 'user_profile';
  static const _codeKey = 'restaurant_code';
  final FlutterSecureStorage _secure;

  @override
  Future<String?> readRefreshToken() => _secure.read(key: _refreshKey);

  @override
  Future<void> writeRefreshToken(String token) => _secure.write(key: _refreshKey, value: token);

  @override
  Future<String?> readUserProfile() => _secure.read(key: _profileKey);

  @override
  Future<void> writeUserProfile(String json) => _secure.write(key: _profileKey, value: json);

  @override
  Future<void> clearSession() async {
    await _secure.delete(key: _refreshKey);
    await _secure.delete(key: _profileKey);
  }

  @override
  Future<String?> readRestaurantCode() async => (await SharedPreferences.getInstance()).getString(_codeKey);

  @override
  Future<void> writeRestaurantCode(String code) async =>
      (await SharedPreferences.getInstance()).setString(_codeKey, code);
}

/// In-memory storage for tests.
class MemoryTokenStorage implements TokenStorage {
  String? refreshToken;
  String? userProfile;
  String? restaurantCode;

  @override
  Future<String?> readRefreshToken() async => refreshToken;

  @override
  Future<void> writeRefreshToken(String token) async => refreshToken = token;

  @override
  Future<String?> readUserProfile() async => userProfile;

  @override
  Future<void> writeUserProfile(String json) async => userProfile = json;

  @override
  Future<void> clearSession() async {
    refreshToken = null;
    userProfile = null;
  }

  @override
  Future<String?> readRestaurantCode() async => restaurantCode;

  @override
  Future<void> writeRestaurantCode(String code) async => restaurantCode = code;
}
