import 'package:flutter_test/flutter_test.dart';
import 'package:staff_app/core/realtime/live_updates.dart';
import 'package:staff_app/features/orders/order_models.dart';
import 'package:staff_app/features/orders/orders_cubit.dart';
import 'package:staff_app/features/tables/table_models.dart';
import 'package:staff_app/features/tables/tables_cubit.dart';

import 'helpers.dart';

Future<void> settle([int ms = 20]) => Future<void>.delayed(Duration(milliseconds: ms));

void main() {
  group('waiter orders, live', () {
    test('new orders appear and finished ones disappear without a refresh', () async {
      final live = FakeLive();
      final repo = FakeOrders(
        data: [sampleOrder(id: 'o1', status: OrderStatus.ready, version: 3)],
      );
      final cubit = OrdersCubit(repo, live: live, pollInterval: const Duration(hours: 1));
      await cubit.start();

      live.pushOrder(
        sampleOrder(id: 'o2', number: 13, status: OrderStatus.placed, version: 1),
        type: 'order.created',
      );
      live.pushOrder(sampleOrder(id: 'o1', status: OrderStatus.completed, version: 5));
      await settle();
      expect([for (final o in cubit.state.orders) o.id], ['o2']);
      await cubit.close();
    });

    test('a table screen ignores other tables', () async {
      final live = FakeLive();
      final cubit = OrdersCubit(
        FakeOrders(),
        tableId: 't4',
        live: live,
        pollInterval: const Duration(hours: 1),
      );
      await cubit.start();
      final elsewhere = StaffOrder.fromJson({
        ...orderJson(sampleOrder(id: 'o7', status: OrderStatus.placed, version: 1)),
        'table_id': 't9',
      });
      live.pushOrder(elsewhere, type: 'order.created');
      live.pushOrder(
        sampleOrder(id: 'o8', status: OrderStatus.placed, version: 1),
        type: 'order.created',
      );
      await settle();
      expect([for (final o in cubit.state.orders) o.id], ['o8']);
      await cubit.close();
    });

    test('reconnecting re-fetches; while live, polling backs off', () async {
      final live = FakeLive();
      var fetches = 0;
      final repo = _CountingOrders(() => fetches++);
      final cubit = OrdersCubit(repo, live: live, pollInterval: const Duration(milliseconds: 30));
      await cubit.start();
      await settle(100);
      expect(fetches, 1); // live: no polling
      live.push(LiveEvent.resync);
      await settle();
      expect(fetches, 2);
      live.connection = LiveStatus.offline;
      await settle(100);
      expect(fetches, greaterThan(3)); // offline: polls
      await cubit.close();
    });
  });

  group('tables, live', () {
    test('order events refresh the table overview once per burst', () async {
      final live = FakeLive();
      final tables = FakeTables(data: sampleTables);
      final cubit = TablesCubit(
        tables,
        canViewPin: false,
        live: live,
        pollInterval: const Duration(hours: 1),
        debounce: const Duration(milliseconds: 30),
      );
      await cubit.start();
      for (var i = 0; i < 5; i++) {
        live.pushOrder(sampleOrder(id: 'o$i', version: 1), type: 'order.created');
      }
      await settle(100);
      expect(tables.loads, 2);
      await cubit.close();
    });

    test('PIN guessing raises an alert naming the table', () async {
      final live = FakeLive();
      final tables = FakeTables(
        data: sampleTables,
        pinInfo: const PinInfo(pin: '4827', version: 3, nextRotationAt: null),
      );
      final cubit = TablesCubit(tables, canViewPin: true, live: live, pollInterval: const Duration(hours: 1));
      await cubit.start();
      live.push(const LiveEvent(id: 'e1', type: 'pin.bruteforce_suspected', payload: {'table_id': 't4'}));
      await settle();
      expect(cubit.state.alert, contains('at T4'));
      await cubit.close();
    });
  });
}

class _CountingOrders extends FakeOrders {
  _CountingOrders(this.onFetch);

  final void Function() onFetch;

  @override
  Future<List<StaffOrder>> orders({List<String> statuses = const [], String? tableId}) async {
    onFetch();
    return const [];
  }
}
