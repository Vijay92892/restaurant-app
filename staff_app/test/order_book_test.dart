import 'package:flutter_test/flutter_test.dart';
import 'package:staff_app/features/kitchen/kitchen_models.dart';
import 'package:staff_app/features/orders/order_book.dart';
import 'package:staff_app/features/orders/order_models.dart';

import 'helpers.dart';

final t0 = DateTime(2026, 10, 4, 19);

StaffOrder o(String id, OrderStatus status, int version, {int minute = 0, int? updatedMinute}) => sampleOrder(
  id: id,
  number: int.parse(id.substring(1)),
  status: status,
  version: version,
  createdAt: t0.add(Duration(minutes: minute)),
  updatedAt: t0.add(Duration(minutes: updatedMinute ?? minute)),
);

OrderBook kitchenBook() => OrderBook(keep: (o) => o.status.onKitchenBoard);

List<String> ids(OrderBook book) => [for (final o in book.orders) o.id];

void main() {
  group('order book', () {
    test('newer versions win; replays and older copies are ignored', () {
      var book = kitchenBook().apply(o('o1', OrderStatus.placed, 1))!;
      book = book.apply(o('o1', OrderStatus.preparing, 3))!;
      expect(book.apply(o('o1', OrderStatus.accepted, 2)), isNull); // late, older event
      expect(book.apply(o('o1', OrderStatus.preparing, 3)), isNull); // duplicate delivery
      expect(book.orders.single.status, OrderStatus.preparing);
    });

    test('orders leave when they move off the screen and never come back from stale data', () {
      var book = kitchenBook().apply(o('o1', OrderStatus.ready, 4))!;
      book = book.apply(o('o1', OrderStatus.served, 5))!;
      expect(book.isEmpty, isTrue);
      expect(book.apply(o('o1', OrderStatus.ready, 4)), isNull); // an old event replayed
      // A snapshot taken before it was served must not resurrect it either.
      book = book.replaceWith([o('o1', OrderStatus.ready, 4)], t0);
      expect(book.isEmpty, isTrue);
    });

    test('a cancellation that arrives first is remembered', () {
      var book = kitchenBook().apply(o('o1', OrderStatus.cancelled, 2))!;
      expect(book.isEmpty, isTrue);
      expect(book.apply(o('o1', OrderStatus.placed, 1)), isNull); // the creation event, late
    });

    test('snapshots replace the list, oldest first, without duplicates', () {
      var book = kitchenBook().apply(o('o1', OrderStatus.placed, 1))!;
      book = book.replaceWith([
        o('o3', OrderStatus.ready, 4, minute: 9),
        o('o1', OrderStatus.accepted, 2),
        o('o2', OrderStatus.placed, 1, minute: 5),
        o('o4', OrderStatus.served, 6, minute: 2), // not a kitchen order
      ], t0.add(const Duration(minutes: 10)));
      expect(ids(book), ['o1', 'o2', 'o3']);
      expect(book.orders.first.status, OrderStatus.accepted);
    });

    test('an event that raced ahead of a snapshot is kept; anything older is dropped', () {
      var book = kitchenBook()
          .apply(o('o1', OrderStatus.placed, 1))!
          .apply(o('o2', OrderStatus.placed, 1, minute: 11))! // created after the snapshot
          .apply(o('o3', OrderStatus.preparing, 3, minute: 4, updatedMinute: 12))!; // changed after
      final snapshotAt = t0.add(const Duration(minutes: 10));
      book = book.replaceWith([o('o3', OrderStatus.accepted, 2, minute: 4)], snapshotAt);
      // o1 is missing from the snapshot and is older than it: it was finished meanwhile.
      expect(ids(book), ['o3', 'o2']);
      expect(book.orders.first.status, OrderStatus.preparing); // our newer copy beats the snapshot's
    });
  });

  group('offline sync', () {
    test('an order cancelled while offline is forgotten and cannot come back', () {
      var book = kitchenBook().apply(o('o1', OrderStatus.preparing, 3))!;
      book = book.forget('o1', 4)!;
      expect(book.isEmpty, isTrue);
      expect(book.apply(o('o1', OrderStatus.ready, 4)), isNull); // an event older than the cancel
      expect(book.forget('o1', 4), isNull); // already known
      expect(book.knows('o1'), isTrue);
      expect(book.knows('o9'), isFalse);
    });

    test('a snapshot that drops an order remembers the version it had', () {
      var book = kitchenBook().apply(o('o1', OrderStatus.ready, 4))!;
      book = book.replaceWith(const [], t0.add(const Duration(minutes: 5)));
      expect(book.isEmpty, isTrue);
      expect(book.tombstones, {'o1': 4});
      expect(book.apply(o('o1', OrderStatus.ready, 4)), isNull); // a delayed copy of what it was
    });

    test('a saved book restores its orders and tombstones', () {
      final saved = kitchenBook()
          .apply(o('o1', OrderStatus.placed, 1))!
          .apply(o('o2', OrderStatus.preparing, 3, minute: 2))!
          .apply(o('o3', OrderStatus.served, 6))!;
      final restored = OrderBook.restore(
        keep: (o) => o.status.onKitchenBoard,
        orders: [for (final order in saved.orders) StaffOrder.fromJson(order.toJson())],
        tombstones: saved.tombstones,
      );
      expect(ids(restored), ['o1', 'o2']);
      expect(restored.tombstones, {'o3': 6});
      expect(restored.apply(o('o3', OrderStatus.ready, 5)), isNull);
      final again = restored['o2']!;
      expect(again.items.first.name, 'Paneer Tikka');
      expect(again.items.first.modifiers, ['Spice level: Medium', 'Extras: Extra cheese']);
      expect(again.createdAt, t0.add(const Duration(minutes: 2)));
      expect(again.version, 3);
    });
  });

  group('kitchen aggregation', () {
    OrderLine line(String id, String name, int qty, {List<String> mods = const [], String? notes}) =>
        OrderLine(menuItemId: id, name: name, quantity: qty, notes: notes, modifiers: mods);

    StaffOrder ticket(String id, String table, List<OrderLine> items) => sampleOrder(
      id: id,
      number: int.parse(id.substring(1)),
      table: table,
      status: OrderStatus.preparing,
      items: items,
    );

    test('sums the same dish across tables and keeps every portion traceable', () {
      final groups = aggregateDishes([
        ticket('o101', 'T4', [line('cp', 'Chilli Paneer', 1), line('naan', 'Butter Naan', 2)]),
        ticket('o102', 'T7', [line('cp', 'Chilli Paneer', 2)]),
        ticket('o104', 'T3', [line('cp', 'Chilli Paneer', 1, notes: 'no onion')]),
      ]);
      expect([for (final g in groups) '${g.name} x${g.total}'], ['Chilli Paneer x4', 'Butter Naan x2']);
      final paneer = groups.first;
      expect(
        [for (final p in paneer.portions) '${p.tableLabel}:${p.quantity}:#${p.orderNumber}'],
        ['T4:1:#101', 'T7:2:#102', 'T3:1:#104'],
      );
      expect(paneer.hasNotes, isTrue);
      expect(paneer.portions.last.notes, 'no onion');
    });

    test('different options are different dishes; same-named items are not merged', () {
      final groups = aggregateDishes([
        ticket('o1', 'T1', [
          line('cp', 'Chilli Paneer', 1, mods: ['Spice: Hot']),
          line('cp', 'Chilli Paneer', 1, mods: ['Spice: Mild']),
          line('cp2', 'Chilli Paneer', 1), // a different menu item with the same name
        ]),
        ticket('o2', 'T2', [
          line('cp', 'Chilli Paneer', 2, mods: ['Spice: Hot']),
        ]),
      ]);
      expect(
        [for (final g in groups) '${g.modifiers} x${g.total}'],
        ['[Spice: Hot] x3', '[Spice: Mild] x1', '[] x1'],
      );
    });
  });
}
