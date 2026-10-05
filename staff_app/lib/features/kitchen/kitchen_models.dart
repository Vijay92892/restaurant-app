import 'package:equatable/equatable.dart';

import '../orders/order_models.dart';

/// `GET /kitchen/board`: every order the kitchen still has to act on, oldest first, plus the
/// orders cancelled in the last two hours (so a tablet that was offline can tell the cook).
class KitchenBoard {
  const KitchenBoard({
    required this.serverTime,
    required this.delayMinutes,
    required this.truncated,
    required this.orders,
    this.recentlyCancelled = const [],
  });

  final DateTime serverTime;
  final int delayMinutes;
  final bool truncated;
  final List<StaffOrder> orders;

  /// `(order id, final version)`.
  final List<(String, int)> recentlyCancelled;

  factory KitchenBoard.fromJson(Map<String, dynamic> json) => KitchenBoard(
    serverTime: DateTime.parse(json['server_time'] as String).toLocal(),
    delayMinutes: json['kitchen_delay_minutes'] as int,
    truncated: json['truncated'] as bool? ?? false,
    orders: [for (final o in json['orders'] as List<dynamic>) StaffOrder.fromJson(o as Map<String, dynamic>)],
    recentlyCancelled: [
      for (final c in json['recently_cancelled'] as List<dynamic>? ?? const [])
        ((c as Map<String, dynamic>)['id'] as String, c['version'] as int),
    ],
  );
}

/// One table's share of an aggregated dish — the link back to the original order.
class Portion extends Equatable {
  const Portion({
    required this.orderId,
    required this.orderNumber,
    required this.tableLabel,
    required this.quantity,
    required this.notes,
  });

  final String orderId;
  final int orderNumber;
  final String tableLabel;
  final int quantity;
  final String? notes;

  @override
  List<Object?> get props => [orderId, orderNumber, tableLabel, quantity, notes];
}

/// "Chilli Paneer (Spicy) ×4" — the same dish with the same options across all orders that
/// are being cooked, with the per-table breakdown kept.
class DishGroup extends Equatable {
  const DishGroup({required this.name, required this.modifiers, required this.portions});

  final String name;
  final List<String> modifiers;
  final List<Portion> portions;

  int get total => portions.fold(0, (sum, p) => sum + p.quantity);
  bool get hasNotes => portions.any((p) => p.notes != null);

  @override
  List<Object?> get props => [name, modifiers, portions];
}

/// Groups the lines of [orders] by dish and chosen options (in the order the dishes were first
/// ordered). Lines are never merged across different options, and every portion keeps its
/// order and table — the relationship between an item and its order is never lost.
List<DishGroup> aggregateDishes(Iterable<StaffOrder> orders) {
  final groups = <String, (String, List<String>, List<Portion>)>{};
  for (final order in orders) {
    for (final line in order.items) {
      // Options are kept in menu order, so equal choices produce equal keys.
      final key = '${line.menuItemId}\u0000${line.modifiers.join('\u0001')}';
      final group = groups.putIfAbsent(key, () => (line.name, line.modifiers, <Portion>[]));
      group.$3.add(
        Portion(
          orderId: order.id,
          orderNumber: order.orderNumber,
          tableLabel: order.tableLabel,
          quantity: line.quantity,
          notes: line.notes,
        ),
      );
    }
  }
  return [
    for (final (name, modifiers, portions) in groups.values)
      DishGroup(name: name, modifiers: modifiers, portions: portions),
  ];
}
