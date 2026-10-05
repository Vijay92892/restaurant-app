import 'package:equatable/equatable.dart';

enum StaffRole { owner, admin, waiter, kitchen, unknown }

/// The signed-in staff member, as returned by the API. `permissions` decide what the UI
/// *shows*; the server re-checks every action regardless.
class StaffUser extends Equatable {
  const StaffUser({
    required this.id,
    required this.restaurantId,
    required this.restaurantName,
    required this.restaurantCode,
    required this.username,
    required this.displayName,
    required this.role,
    required this.mustChangePassword,
    required this.permissions,
  });

  final String id;
  final String restaurantId;
  final String restaurantName;
  final String restaurantCode;
  final String username;
  final String displayName;
  final StaffRole role;
  final bool mustChangePassword;
  final Set<String> permissions;

  bool can(String permission) => permissions.contains(permission);

  factory StaffUser.fromJson(Map<String, dynamic> json) => StaffUser(
    id: json['id'] as String,
    restaurantId: json['restaurant_id'] as String,
    restaurantName: json['restaurant_name'] as String,
    restaurantCode: json['restaurant_code'] as String,
    username: json['username'] as String,
    displayName: json['display_name'] as String,
    role: StaffRole.values.firstWhere((r) => r.name == json['role'], orElse: () => StaffRole.unknown),
    mustChangePassword: json['must_change_password'] as bool? ?? false,
    permissions: {for (final p in json['permissions'] as List<dynamic>) p as String},
  );

  /// The inverse of [StaffUser.fromJson] (saved on the device for offline restarts).
  Map<String, dynamic> toJson() => {
    'id': id,
    'restaurant_id': restaurantId,
    'restaurant_name': restaurantName,
    'restaurant_code': restaurantCode,
    'username': username,
    'display_name': displayName,
    'role': role.name,
    'must_change_password': mustChangePassword,
    'permissions': permissions.toList()..sort(),
  };

  StaffUser copyWith({bool? mustChangePassword}) => StaffUser(
    id: id,
    restaurantId: restaurantId,
    restaurantName: restaurantName,
    restaurantCode: restaurantCode,
    username: username,
    displayName: displayName,
    role: role,
    mustChangePassword: mustChangePassword ?? this.mustChangePassword,
    permissions: permissions,
  );

  @override
  List<Object?> get props => [id, role, mustChangePassword, permissions];
}

/// Permission names (mirror of the backend's `Permission` enum).
abstract final class Perm {
  static const tablesClose = 'tables.close';
  static const pinView = 'pin.view';
  static const ordersCreate = 'orders.create';
  static const ordersServe = 'orders.serve';
  static const ordersKitchen = 'orders.kitchen_transition';
  static const ordersCancel = 'orders.cancel';
  static const ordersCancelPrivileged = 'orders.cancel_privileged';
  static const ordersCancelRequest = 'orders.cancel_request';
  static const ordersCancelReview = 'orders.cancel_review';
  static const menuAvailability = 'menu.availability';
  static const pinManage = 'pin.manage';
  static const tableSessionsInvalidateAll = 'table_sessions.invalidate_all';
  static const staffRead = 'staff.read';
  static const staffManage = 'staff.manage';
  static const staffManageAdmins = 'staff.manage_admins';
  static const problemReportsCreate = 'problem_reports.create';
  static const problemReportsRead = 'problem_reports.read';
}
