import 'dart:math';

import 'package:equatable/equatable.dart';

import '../../core/auth/staff_user.dart';
import '../../core/network/api_client.dart';

/// The table PIN and how it changes (spec §35). Only staff with `pin.view` ever receive it.
class PinSettings extends Equatable {
  const PinSettings({
    required this.pin,
    required this.version,
    required this.pinLength,
    required this.rotationMinutes,
    required this.nextRotationAt,
  });

  final String pin;
  final int version;
  final int pinLength;

  /// 120, 240 or 360; null = changes only when someone changes it.
  final int? rotationMinutes;
  final DateTime? nextRotationAt;

  factory PinSettings.fromJson(Map<String, dynamic> json) => PinSettings(
    pin: json['pin'] as String,
    version: json['version'] as int,
    pinLength: json['pin_length'] as int,
    rotationMinutes: json['rotation_minutes'] as int?,
    nextRotationAt: switch (json['next_rotation_at']) {
      final String at => DateTime.parse(at).toLocal(),
      _ => null,
    },
  );

  @override
  List<Object?> get props => [pin, version, pinLength, rotationMinutes, nextRotationAt];
}

class PinRepository {
  const PinRepository(this._api);

  final ApiClient _api;

  Future<PinSettings> current() async =>
      PinSettings.fromJson(await _api.getJson<Map<String, dynamic>>('/pin'));

  /// A new PIN now. With [endSessions], phones already at tables must enter the new PIN too.
  /// Returns the new settings and how many customer sessions ended.
  Future<(PinSettings, int)> rotate({required bool endSessions}) async {
    final data = await _api.postJson<Map<String, dynamic>>(
      '/pin/rotate',
      data: {'invalidate_active_sessions': endSessions},
    );
    return (PinSettings.fromJson(data), data['sessions_invalidated'] as int);
  }

  Future<PinSettings> save({required int? rotationMinutes, required int pinLength}) async =>
      PinSettings.fromJson(
        await _api.putJson<Map<String, dynamic>>(
          '/pin/settings',
          data: {'rotation_minutes': rotationMinutes, 'pin_length': pinLength},
        ),
      );

  /// Ends every customer session (all tables). Returns how many ended.
  Future<int> endAllSessions() async {
    final data = await _api.postJson<Map<String, dynamic>>('/table-sessions/invalidate-all');
    return data['invalidated'] as int;
  }
}

class StaffMember extends Equatable {
  const StaffMember({
    required this.id,
    required this.username,
    required this.displayName,
    required this.role,
    required this.isActive,
    required this.mustChangePassword,
    required this.lastLoginAt,
  });

  final String id;
  final String username;
  final String displayName;
  final StaffRole role;
  final bool isActive;
  final bool mustChangePassword;
  final DateTime? lastLoginAt;

  factory StaffMember.fromJson(Map<String, dynamic> json) => StaffMember(
    id: json['id'] as String,
    username: json['username'] as String,
    displayName: json['display_name'] as String,
    role: StaffRole.values.firstWhere((r) => r.name == json['role'], orElse: () => StaffRole.unknown),
    isActive: json['is_active'] as bool,
    mustChangePassword: json['must_change_password'] as bool,
    lastLoginAt: switch (json['last_login_at']) {
      final String at => DateTime.parse(at).toLocal(),
      _ => null,
    },
  );

  @override
  List<Object?> get props => [id, username, displayName, role, isActive, mustChangePassword, lastLoginAt];
}

class StaffRepository {
  const StaffRepository(this._api);

  final ApiClient _api;

  Future<List<StaffMember>> list() async {
    final data = await _api.getJson<List<dynamic>>('/staff');
    return [for (final row in data) StaffMember.fromJson(row as Map<String, dynamic>)];
  }

  Future<StaffMember> add({
    required String username,
    required String displayName,
    required StaffRole role,
    required String temporaryPassword,
  }) async => StaffMember.fromJson(
    await _api.postJson<Map<String, dynamic>>(
      '/staff',
      data: {
        'username': username,
        'display_name': displayName,
        'role': role.name,
        'temporary_password': temporaryPassword,
      },
    ),
  );

  Future<StaffMember> update(String id, {StaffRole? role, bool? isActive}) async => StaffMember.fromJson(
    await _api.patchJson<Map<String, dynamic>>(
      '/staff/$id',
      data: {'role': ?role?.name, 'is_active': ?isActive},
    ),
  );

  /// Signs the person out everywhere; they choose a new password at their next sign-in.
  Future<void> resetPassword(String id, String temporaryPassword) async {
    await _api.postJson<void>('/staff/$id/reset-password', data: {'temporary_password': temporaryPassword});
  }
}

/// Three groups of four characters from an alphabet without look-alikes (0/o, 1/l/i): easy to
/// read out to a new waiter, about 59 bits of randomness. Used once, then replaced.
String temporaryPassword([Random? random]) {
  const alphabet = 'abcdefghjkmnpqrstuvwxyz23456789';
  final rng = random ?? Random.secure();
  String group() => List.generate(4, (_) => alphabet[rng.nextInt(alphabet.length)]).join();
  return '${group()}-${group()}-${group()}';
}

/// How guests open their table: the table PIN, and/or a location check at the restaurant
/// (owner and managers only). Mirrors `GET/PUT /restaurant/customer-access`.
class CustomerAccess extends Equatable {
  const CustomerAccess({
    required this.locationEnabled,
    required this.latitude,
    required this.longitude,
    required this.radiusM,
    required this.pinFallback,
    this.maxAccuracyM = 100,
  });

  final bool locationEnabled;

  /// The branch's point, confirmed by the owner or a manager (never guessed).
  final double? latitude;
  final double? longitude;
  final int radiusM;

  /// With the location check on: can guests whose check fails still use the PIN?
  final bool pinFallback;

  /// How precise a phone's position must be (a server setting; shown in the explanation).
  final int maxAccuracyM;

  /// The radii a manager can choose from (the server accepts 25–500 m).
  static const radii = [50, 75, 100, 150, 200, 300, 500];

  bool get hasPoint => latitude != null && longitude != null;

  /// What guests experience, in one sentence.
  String get summary {
    if (!locationEnabled) return 'Guests enter the table PIN to order.';
    if (pinFallback) {
      return 'Guests at the restaurant (within $radiusM m) order without a PIN. '
          'If their phone can\'t confirm it, they use the table PIN.';
    }
    return 'Only guests at the restaurant (within $radiusM m) can order from their phones; '
        'the table PIN is not accepted. Others ask a waiter.';
  }

  factory CustomerAccess.fromJson(Map<String, dynamic> json) => CustomerAccess(
    locationEnabled: json['location_ordering_enabled'] as bool,
    latitude: (json['latitude'] as num?)?.toDouble(),
    longitude: (json['longitude'] as num?)?.toDouble(),
    radiusM: json['radius_m'] as int,
    pinFallback: json['pin_fallback_enabled'] as bool,
    maxAccuracyM: json['max_accuracy_m'] as int? ?? 100,
  );

  Map<String, dynamic> toJson() => {
    'location_ordering_enabled': locationEnabled,
    'latitude': latitude,
    'longitude': longitude,
    'radius_m': radiusM,
    'pin_fallback_enabled': pinFallback,
  };

  @override
  List<Object?> get props => [locationEnabled, latitude, longitude, radiusM, pinFallback, maxAccuracyM];
}

/// "28.2449519, 76.8135882", as a map app copies a pin's coordinates (a space, a comma or
/// both between them). Null when it isn't a valid point, or is 0, 0.
(double, double)? parseCoordinates(String text) {
  final parts = text.trim().split(RegExp(r'[\s,;]+')).where((p) => p.isNotEmpty).toList();
  if (parts.length != 2) return null;
  final latitude = double.tryParse(parts[0]);
  final longitude = double.tryParse(parts[1]);
  if (latitude == null || longitude == null || !latitude.isFinite || !longitude.isFinite) return null;
  if (latitude.abs() > 90 || longitude.abs() > 180) return null;
  if (latitude == 0 && longitude == 0) return null;
  return (latitude, longitude);
}

String formatCoordinates(double latitude, double longitude) =>
    '${latitude.toStringAsFixed(6)}, ${longitude.toStringAsFixed(6)}';

class CustomerAccessRepository {
  const CustomerAccessRepository(this._api);

  final ApiClient _api;

  Future<CustomerAccess> current() async =>
      CustomerAccess.fromJson(await _api.getJson<Map<String, dynamic>>('/restaurant/customer-access'));

  Future<CustomerAccess> save(CustomerAccess access) async => CustomerAccess.fromJson(
    await _api.putJson<Map<String, dynamic>>('/restaurant/customer-access', data: access.toJson()),
  );
}
