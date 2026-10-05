import 'package:equatable/equatable.dart';

enum OrderStatus {
  placed('New', 'Waiting for the kitchen'),
  accepted('Accepted', 'The kitchen has it'),
  preparing('Preparing', 'Being cooked'),
  ready('Ready', 'Take it to the table'),
  served('Served', 'On the table'),
  completed('Completed', 'Done'),
  cancelled('Cancelled', 'Not going ahead');

  const OrderStatus(this.label, this.hint);

  final String label;
  final String hint;

  bool get isActive => this != completed && this != cancelled;
  bool get inKitchen => this == placed || this == accepted || this == preparing;

  /// Statuses shown on the kitchen board (the kitchen still has to act, or hand over).
  bool get onKitchenBoard => inKitchen || this == ready;

  static OrderStatus parse(String value) => values.firstWhere((s) => s.name == value);
}

class OrderLine extends Equatable {
  const OrderLine({
    required this.menuItemId,
    required this.name,
    required this.quantity,
    required this.notes,
    required this.modifiers,
  });

  final String menuItemId;
  final String name;
  final int quantity;
  final String? notes;
  final List<String> modifiers;

  factory OrderLine.fromJson(Map<String, dynamic> json) => OrderLine(
    menuItemId: json['menu_item_id'] as String,
    name: json['name'] as String,
    quantity: json['quantity'] as int,
    notes: json['notes'] as String?,
    modifiers: [for (final m in json['modifiers'] as List<dynamic>) m as String],
  );

  Map<String, dynamic> toJson() => {
    'menu_item_id': menuItemId,
    'name': name,
    'quantity': quantity,
    'notes': notes,
    'modifiers': modifiers,
  };

  @override
  List<Object?> get props => [menuItemId, name, quantity, notes, modifiers];
}

/// An order as staff see it. No prices or totals exist on orders, by design.
class StaffOrder extends Equatable {
  const StaffOrder({
    required this.id,
    required this.orderNumber,
    required this.status,
    required this.version,
    required this.createdAt,
    required this.updatedAt,
    this.readyAt,
    required this.notes,
    required this.items,
    required this.tableId,
    required this.tableLabel,
    required this.source,
    required this.placedBy,
    required this.cancelRequested,
    required this.cancelRequestReason,
  });

  final String id;
  final int orderNumber;
  final OrderStatus status;

  /// Optimistic-concurrency version: sent back with every change.
  final int version;
  final DateTime createdAt;

  /// Server time of the last change; used to merge live events with board snapshots.
  final DateTime updatedAt;
  final DateTime? readyAt;
  final String? notes;
  final List<OrderLine> items;
  final String tableId;
  final String tableLabel;
  final String source;
  final String? placedBy;
  final bool cancelRequested;
  final String? cancelRequestReason;

  bool get fromCustomer => source == 'customer';

  factory StaffOrder.fromJson(Map<String, dynamic> json) => StaffOrder(
    id: json['id'] as String,
    orderNumber: json['order_number'] as int,
    status: OrderStatus.parse(json['status'] as String),
    version: json['version'] as int,
    createdAt: DateTime.parse(json['created_at'] as String).toLocal(),
    updatedAt: DateTime.parse(json['updated_at'] as String).toLocal(),
    readyAt: switch (json['ready_at']) {
      final String at => DateTime.parse(at).toLocal(),
      _ => null,
    },
    notes: json['notes'] as String?,
    items: [for (final i in json['items'] as List<dynamic>) OrderLine.fromJson(i as Map<String, dynamic>)],
    tableId: json['table_id'] as String,
    tableLabel: json['table_label'] as String,
    source: json['source'] as String,
    placedBy: json['placed_by'] as String?,
    cancelRequested: json['cancel_requested'] as bool,
    cancelRequestReason: json['cancel_request_reason'] as String?,
  );

  /// The server's shape (the inverse of [StaffOrder.fromJson]), for saving on the device.
  Map<String, dynamic> toJson() => {
    'id': id,
    'order_number': orderNumber,
    'status': status.name,
    'version': version,
    'created_at': createdAt.toUtc().toIso8601String(),
    'updated_at': updatedAt.toUtc().toIso8601String(),
    'ready_at': readyAt?.toUtc().toIso8601String(),
    'notes': notes,
    'items': [for (final line in items) line.toJson()],
    'table_id': tableId,
    'table_label': tableLabel,
    'source': source,
    'placed_by': placedBy,
    'cancel_requested': cancelRequested,
    'cancel_request_reason': cancelRequestReason,
  };

  @override
  List<Object?> get props => [id, status, version, cancelRequested];
}
