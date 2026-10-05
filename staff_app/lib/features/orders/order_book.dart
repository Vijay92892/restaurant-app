import 'order_models.dart';

/// The orders a screen is showing, merged from two sources that can arrive in any order:
/// full snapshots (REST) and single-order live events (WebSocket).
///
/// Rules, so a screen never shows a duplicate, a stale status, or a finished order coming back:
///  * an order is identified by its id; a change is applied only if its `version` is newer;
///  * an order that leaves the screen (served, cancelled…) leaves a tombstone with its version,
///    so an older copy of it in a late snapshot or event cannot bring it back;
///  * a snapshot taken at server time T drops orders it doesn't list — unless they changed
///    after T (an event raced ahead of the snapshot) — and remembers the version it had.
class OrderBook {
  OrderBook({required this.keep}) : _orders = {}, _gone = {};

  /// A book saved on the device ([orders] and [tombstones] as they were).
  OrderBook.restore({
    required this.keep,
    required Iterable<StaffOrder> orders,
    required Map<String, int> tombstones,
  }) : _orders = {
         for (final order in orders)
           if (keep(order)) order.id: order,
       },
       _gone = Map.of(tombstones);

  OrderBook._(this.keep, this._orders, this._gone);

  /// Which orders belong on this screen.
  final bool Function(StaffOrder order) keep;
  final Map<String, StaffOrder> _orders;
  final Map<String, int> _gone;

  static const _maxTombstones = 1000;

  /// Oldest first — the order a kitchen works in.
  List<StaffOrder> get orders {
    final list = _orders.values.toList()
      ..sort((a, b) {
        final byTime = a.createdAt.compareTo(b.createdAt);
        return byTime != 0 ? byTime : a.orderNumber.compareTo(b.orderNumber);
      });
    return list;
  }

  bool get isEmpty => _orders.isEmpty;

  /// The order with [id] if it is on this screen.
  StaffOrder? operator [](String id) => _orders[id];

  /// Whether this book has ever seen [id], on screen or gone.
  bool knows(String id) => _orders.containsKey(id) || _gone.containsKey(id);

  /// Versions of orders that left the screen (to save on the device).
  Map<String, int> get tombstones => Map.unmodifiable(_gone);

  int? _knownVersion(String id) => _orders[id]?.version ?? _gone[id];

  /// Applies one changed order (from a live event or an action's response).
  /// Returns null when nothing changed, so callers can skip a rebuild.
  OrderBook? apply(StaffOrder order) {
    final known = _knownVersion(order.id);
    if (known != null && known >= order.version) return null;
    final orders = Map.of(_orders);
    final gone = Map.of(_gone);
    if (keep(order)) {
      orders[order.id] = order;
      gone.remove(order.id);
    } else {
      final removed = orders.remove(order.id);
      gone[order.id] = order.version;
      _trim(gone);
      if (removed == null && known == null) {
        return OrderBook._(keep, orders, gone); // remember it, though nothing visible changed
      }
    }
    return OrderBook._(keep, orders, gone);
  }

  /// Records that order [id] left the screen at [version] (e.g. it was cancelled while this
  /// device was offline), so no older copy can bring it back. Null when nothing changed.
  OrderBook? forget(String id, int version) {
    final known = _knownVersion(id);
    if (known != null && known >= version) return null;
    final orders = Map.of(_orders)..remove(id);
    final gone = Map.of(_gone)..[id] = version;
    _trim(gone);
    return OrderBook._(keep, orders, gone);
  }

  /// Replaces the book with a snapshot taken at [serverTime].
  OrderBook replaceWith(List<StaffOrder> snapshot, DateTime serverTime) {
    final orders = <String, StaffOrder>{};
    final gone = Map.of(_gone);
    final listed = {for (final order in snapshot) order.id};
    for (final order in snapshot) {
      final known = _knownVersion(order.id);
      final current = _orders[order.id];
      if (known != null && known > order.version) {
        if (current != null) orders[order.id] = current; // we already have something newer
        continue;
      }
      if (keep(order)) {
        orders[order.id] = order;
        gone.remove(order.id);
      }
    }
    for (final order in _orders.values) {
      if (listed.contains(order.id)) continue;
      if (order.updatedAt.isAfter(serverTime)) {
        orders[order.id] = order; // changed after the snapshot was taken
      } else {
        gone[order.id] = order.version; // it left the screen; this copy (or older) is stale
      }
    }
    _trim(gone);
    return OrderBook._(keep, orders, gone);
  }

  static void _trim(Map<String, int> gone) {
    while (gone.length > _maxTombstones) {
      gone.remove(gone.keys.first);
    }
  }
}
