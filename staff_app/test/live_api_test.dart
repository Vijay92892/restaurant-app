// End-to-end check of the app's data layer against a REAL running backend.
// Skipped unless LIVE_API_URL, LIVE_CODE and LIVE_PASSWORD are set, e.g. after
//   python scripts/seed_demo.py --code live-test --password Live-Test-2026 --json
@TestOn('vm')
library;

import 'dart:io';

import 'package:async/async.dart' show StreamQueue;
import 'package:flutter_test/flutter_test.dart';
import 'package:staff_app/core/network/api_client.dart';
import 'package:staff_app/core/network/api_exception.dart';
import 'package:staff_app/core/realtime/live_updates.dart';
import 'package:staff_app/core/realtime/realtime_client.dart';
import 'package:staff_app/core/storage/token_storage.dart';
import 'package:staff_app/features/kitchen/kitchen_repository.dart';
import 'package:staff_app/features/menu/menu_repository.dart';
import 'package:staff_app/features/orders/order_draft.dart';
import 'package:staff_app/features/orders/order_models.dart';
import 'package:staff_app/features/orders/orders_repository.dart';
import 'package:staff_app/features/tables/tables_repository.dart';

final env = Platform.environment;
final skip =
    !(env.containsKey('LIVE_API_URL') && env.containsKey('LIVE_CODE') && env.containsKey('LIVE_PASSWORD'));

void main() {
  late String baseUrl;
  late String code;
  late String password;

  setUpAll(() {
    baseUrl = env['LIVE_API_URL'] ?? '';
    code = env['LIVE_CODE'] ?? '';
    password = env['LIVE_PASSWORD'] ?? '';
  });

  Future<(ApiClient, MemoryTokenStorage)> signIn(String username) async {
    final storage = MemoryTokenStorage();
    final api = ApiClient(baseUrl: baseUrl, storage: storage);
    await api.login(restaurantCode: code, username: username, password: password);
    return (api, storage);
  }

  test('waiter and kitchen run an order from table to completion', skip: skip, () async {
    final (waiterApi, waiterStorage) = await signIn('waiter');
    final (kitchenApi, _) = await signIn('chef');
    final tables = TablesRepository(waiterApi);
    final waiterOrders = OrdersRepository(waiterApi);
    final kitchenOrders = OrdersRepository(kitchenApi);

    // Waiter sees the tables and the PIN to give customers.
    final tableList = await tables.tables();
    expect(tableList, isNotEmpty);
    final table = tableList.first;
    expect((await tables.pin()).pin, matches(RegExp(r'^\d{4,6}$')));

    // Build an order from the live menu, including a required option.
    final menu = await MenuRepository(waiterApi).orderingMenu();
    final paneer = menu.categories.expand((c) => c.items).firstWhere((i) => i.name == 'Paneer Tikka');
    final spice = menu.groupsFor(paneer).firstWhere((g) => g.name == 'Spice level');
    final naan = menu.categories.expand((c) => c.items).firstWhere((i) => i.name == 'Butter Naan');
    var draft = const OrderDraft();
    draft = draft
        .add(DraftLine(itemId: paneer.id, name: paneer.name, quantity: 2, modifierIds: [spice.options[1].id]))
        .$1;
    draft = draft.add(DraftLine(itemId: naan.id, name: naan.name, quantity: 4)).$1;

    // A double tap / retry with the same key is one order.
    final key = 'live${DateTime.now().microsecondsSinceEpoch}';
    final order = await waiterOrders.place(tableId: table.id, draft: draft, idempotencyKey: key);
    final again = await waiterOrders.place(tableId: table.id, draft: draft, idempotencyKey: key);
    expect(again.id, order.id);
    expect(order.status, OrderStatus.placed);
    expect(order.items.first.modifiers.single, startsWith('Spice level:'));

    // A waiter cannot do the kitchen's job — the server refuses, whatever the UI shows.
    await expectLater(
      waiterOrders.transition(order, OrderStatus.accepted),
      throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', 403)),
    );

    // Kitchen cooks it.
    var current = await kitchenOrders.transition(order, OrderStatus.accepted);
    current = await kitchenOrders.transition(current, OrderStatus.preparing);
    current = await kitchenOrders.transition(current, OrderStatus.ready);

    // A stale tablet cannot clobber newer state.
    await expectLater(
      kitchenOrders.transition(order, OrderStatus.preparing),
      throwsA(isA<ApiException>().having((e) => e.code, 'code', 'version_conflict')),
    );

    // Waiter sees it ready, serves it, closes the table.
    final ready = (await waiterOrders.orders(statuses: ['ready'])).firstWhere((o) => o.id == order.id);
    await waiterOrders.transition(ready, OrderStatus.served);
    final closed = await tables.closeTable(table.id);
    expect(closed.ordersCompleted, greaterThanOrEqualTo(1));

    // The session survives an app restart: a new client restores from the stored refresh token.
    final restarted = ApiClient(baseUrl: baseUrl, storage: waiterStorage);
    final restored = await restarted.restore();
    expect(restored?.user.username, 'waiter');
    expect(restored?.offline, isFalse);
    await restarted.logout();
    expect(waiterStorage.userProfile, isNull); // nothing about the user stays on the device
    expect(await ApiClient(baseUrl: baseUrl, storage: waiterStorage).restore(), isNull);
  });

  test('the kitchen tablet sees a waiter order live, over the real WebSocket', skip: skip, () async {
    final (waiterApi, _) = await signIn('waiter');
    final (kitchenApi, _) = await signIn('chef');
    final live = RealtimeClient(api: kitchenApi);
    final events = StreamQueue(live.events);
    addTearDown(live.dispose);
    live.start();

    Future<LiveEvent> next(bool Function(LiveEvent) wanted) async {
      while (true) {
        final event = await events.next.timeout(const Duration(seconds: 10));
        if (wanted(event)) return event;
      }
    }

    expect((await next((e) => e.isResync)).isResync, isTrue); // connected and authenticated
    expect(live.status.value, LiveStatus.live);

    final table = (await TablesRepository(waiterApi).tables()).first;
    final menu = await MenuRepository(waiterApi).orderingMenu();
    final naan = menu.categories.expand((c) => c.items).firstWhere((i) => i.name == 'Butter Naan');
    final draft = const OrderDraft().add(DraftLine(itemId: naan.id, name: naan.name, quantity: 3)).$1;
    final order = await OrdersRepository(waiterApi).place(
      tableId: table.id,
      draft: draft,
      idempotencyKey: 'live-ws-${DateTime.now().microsecondsSinceEpoch}',
    );

    final created = await next((e) => e.orderId == order.id);
    expect(created.type, 'order.created');
    final pushed = StaffOrder.fromJson(created.payload['order'] as Map<String, dynamic>);
    expect(pushed.items.single.menuItemId, naan.id);
    expect(pushed.version, order.version);

    final board = await KitchenRepository(kitchenApi).board();
    expect(board.orders.map((o) => o.id), contains(order.id));

    final accepted = await OrdersRepository(kitchenApi).transition(pushed, OrderStatus.accepted);
    final updated = await next((e) => e.orderId == order.id);
    expect(updated.type, 'order.updated');
    expect(updated.orderVersion, accepted.version);

    // Cancelling it (which also tidies up for the next run) takes it off the board; a tablet
    // that missed the event learns about it from the board's recent cancellations.
    final cancelled = await OrdersRepository(waiterApi)
        .cancel(accepted, reasonCode: 'other', reason: 'live test');
    final after = await KitchenRepository(kitchenApi).board();
    expect(after.orders.map((o) => o.id), isNot(contains(order.id)));
    expect(after.recentlyCancelled, contains((order.id, cancelled.version)));
    await events.cancel();
  });
}
