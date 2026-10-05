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
