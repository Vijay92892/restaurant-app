import 'package:equatable/equatable.dart';

class TableSummary extends Equatable {
  const TableSummary({
    required this.id,
    required this.label,
    required this.isActive,
    required this.activeSessions,
    required this.openOrders,
    required this.readyOrders,
  });

  final String id;
  final String label;
  final bool isActive;

  /// Diners' phones currently seated at the table (table sessions).
  final int activeSessions;
  final int openOrders;
  final int readyOrders;

  bool get isBusy => activeSessions > 0 || openOrders > 0;

  factory TableSummary.fromJson(Map<String, dynamic> json) => TableSummary(
    id: json['id'] as String,
    label: json['label'] as String,
    isActive: json['is_active'] as bool,
    activeSessions: json['active_sessions'] as int,
    openOrders: json['open_orders'] as int,
    readyOrders: json['ready_orders'] as int? ?? 0,
  );

  @override
  List<Object?> get props => [id, label, isActive, activeSessions, openOrders, readyOrders];
}

class PinInfo extends Equatable {
  const PinInfo({required this.pin, required this.version, required this.nextRotationAt});

  final String pin;
  final int version;
  final DateTime? nextRotationAt;

  factory PinInfo.fromJson(Map<String, dynamic> json) => PinInfo(
    pin: json['pin'] as String,
    version: json['version'] as int,
    nextRotationAt: json['next_rotation_at'] == null
        ? null
        : DateTime.parse(json['next_rotation_at'] as String).toLocal(),
  );

  @override
  List<Object?> get props => [pin, version, nextRotationAt];
}

class CloseTableResult {
  const CloseTableResult({required this.sessionsEnded, required this.ordersCompleted});

  final int sessionsEnded;
  final int ordersCompleted;
}
