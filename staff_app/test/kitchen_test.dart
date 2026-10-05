import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:staff_app/core/auth/staff_user.dart';
import 'package:staff_app/core/network/api_exception.dart';
import 'package:staff_app/core/realtime/live_updates.dart';
import 'package:staff_app/features/kitchen/kitchen_cubit.dart';
import 'package:staff_app/features/kitchen/kitchen_device.dart';
import 'package:staff_app/features/kitchen/kitchen_page.dart';
import 'package:staff_app/features/kitchen/kitchen_store.dart';
import 'package:staff_app/features/orders/order_models.dart';

import 'helpers.dart';

const _conflict = ApiException(statusCode: 409, code: 'version_conflict', message: 'Changed elsewhere.');

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 20));

void main() {
  group('kitchen cubit', () {
    late FakeKitchen kitchen;
    late FakeOrders orders;
    late FakeLive live;
    late KitchenCubit cubit;

    setUp(() {
      // The tablet's clock is 2 hours slow; timings must follow the server's clock.
      final serverNow = DateTime.now().add(const Duration(hours: 2));
      kitchen = FakeKitchen(orders: sampleRush(serverNow), serverTime: serverNow);
      orders = FakeOrders();
      live = FakeLive();
      cubit = KitchenCubit(kitchen, orders, live, fallbackPoll: const Duration(milliseconds: 40));
    });

    tearDown(() => cubit.close());

    test('loads the board into lanes and sums what is being cooked', () async {
      await cubit.start();
      final state = cubit.state;
      expect([for (final o in state.newOrders) o.orderNumber], [103]);
      expect([for (final o in state.cooking) o.orderNumber], [101, 102]);
      expect([for (final o in state.ready) o.orderNumber], [99]);
      expect([for (final g in state.toCook) '${g.name} x${g.total}'], ['Paneer Tikka x3', 'Butter Naan x4']);
      expect(
        [for (final p in state.toCook.first.portions) '${p.tableLabel} x${p.quantity}'],
        ['T4 x2', 'T7 x1'],
      );
    });

    test('"delayed" uses the server clock, not the tablet clock', () async {
      await cubit.start();
      final state = cubit.state;
      bool delayed(int number) => state.isDelayed(state.orders.firstWhere((o) => o.orderNumber == number));
      expect(delayed(101), isTrue); // 22 min > 15
      expect(delayed(102), isFalse); // 9 min
      expect(delayed(99), isFalse); // ready: no longer the kitchen's delay
      expect(
        state.waiting(state.orders.firstWhere((o) => o.orderNumber == 101)).inMinutes,
        inInclusiveRange(21, 22),
      );
    });

    test('live events add, move and remove orders; stale ones are ignored', () async {
      await cubit.start();
      final fresh = sampleOrder(id: 'o9', number: 110, table: 'T1', status: OrderStatus.placed, version: 1);
      live.pushOrder(fresh, type: 'order.created');
      await settle();
      expect(cubit.state.newOrders.map((o) => o.orderNumber), contains(110));

      final ready = cubit.state.orders.firstWhere((o) => o.id == 'o1');
      live.pushOrder(
        sampleOrder(
          id: 'o1',
          number: 101,
          table: 'T4',
          status: OrderStatus.ready,
          version: 4,
          createdAt: ready.createdAt,
        ),
      );
      live.pushOrder(
        sampleOrder(id: 'o1', number: 101, table: 'T4', status: OrderStatus.accepted, version: 2),
      ); // late
      live.pushOrder(sampleOrder(id: 'o4', number: 99, table: 'T2', status: OrderStatus.served, version: 5));
      await settle();
      expect([for (final o in cubit.state.ready) o.orderNumber], [101]);
      expect(cubit.state.orders.where((o) => o.id == 'o4'), isEmpty);
    });

    test('reconnecting reloads the whole board', () async {
      await cubit.start();
      expect(kitchen.loads, 1);
      live.push(LiveEvent.resync);
      await settle();
      expect(kitchen.loads, 2);
    });

    test('while offline it keeps the orders and checks the server regularly', () async {
      await cubit.start();
      live.connection = LiveStatus.offline;
      await settle();
      expect(cubit.state.live, LiveStatus.offline);
      kitchen.failWith = const ApiException(statusCode: 0, code: 'network', message: 'No connection.');
      await Future<void>.delayed(const Duration(milliseconds: 100));
      expect(kitchen.loads, greaterThan(1));
      expect(cubit.state.orders, hasLength(4)); // still showing what it had
      expect(cubit.state.loadError, isNotNull);
    });

    test('actions move orders and a double tap sends only once', () async {
      await cubit.start();
      final cooking = cubit.state.orders.firstWhere((o) => o.id == 'o1');
      await Future.wait([cubit.markReady(cooking), cubit.markReady(cooking)]);
      expect(orders.transitions, hasLength(1));
      expect(orders.transitions.single.$2, OrderStatus.ready);
      expect(cubit.state.ready.map((o) => o.id), contains('o1'));
      expect(cubit.state.busy, isEmpty);
    });

    test('a conflict shows the latest board instead of retrying', () async {
      await cubit.start();
      orders.failWith = _conflict;
      await cubit.accept(cubit.state.newOrders.single);
      expect(kitchen.loads, 2);
      expect(cubit.state.message, isNull); // cleared after the snackbar state
    });
  });

  group('kitchen reliability', () {
    late DateTime serverNow;
    late FakeKitchen kitchen;
    late FakeOrders orders;
    late FakeLive live;
    late FakeBoardStore store;
    late FakeKitchenDevice device;
    late KitchenCubit cubit;

    KitchenCubit build({Duration reconcileEvery = const Duration(minutes: 2)}) => cubit = KitchenCubit(
      kitchen,
      orders,
      live,
      store: store,
      device: device,
      restaurantId: 'r1',
      fallbackPoll: const Duration(milliseconds: 20),
      reconcileEvery: reconcileEvery,
      saveDelay: const Duration(milliseconds: 5),
      chimeGap: const Duration(milliseconds: 60),
    );

    List<String> messagesOf(KitchenCubit cubit, List<StreamSubscription<KitchenState>> subs) {
      final messages = <String>[];
      subs.add(
        cubit.stream.listen((s) {
          if (s.message != null) messages.add(s.message!);
        }),
      );
      return messages;
    }

    final subscriptions = <StreamSubscription<KitchenState>>[];

    setUp(() {
      serverNow = DateTime.now();
      kitchen = FakeKitchen(orders: sampleRush(serverNow), serverTime: serverNow);
      orders = FakeOrders();
      live = FakeLive();
      store = FakeBoardStore();
      device = FakeKitchenDevice();
      build();
    });

    tearDown(() async {
      for (final sub in subscriptions) {
        await sub.cancel();
      }
      subscriptions.clear();
      await cubit.close();
    });

    test('a tablet restarted during an outage shows its saved board, with buttons paused', () async {
      store.saved = savedBoard(sampleRush(serverNow));
      kitchen.failWith = offlineError;
      live.connection = LiveStatus.offline;
      await cubit.start();
      expect(cubit.state.orders, hasLength(4));
      expect(cubit.state.fromSaved, isTrue);
      expect(cubit.state.canAct, isFalse);

      await cubit.markReady(cubit.state.orders.firstWhere((o) => o.id == 'o1'));
      expect(orders.transitions, isEmpty); // neither sent nor queued for later
      expect(cubit.state.message, KitchenCubit.offlineMessage);

      kitchen.failWith = null; // the server answers again
      await cubit.load();
      expect(cubit.state.fromSaved, isFalse);
      expect(cubit.state.canAct, isTrue);
      expect(device.played, isEmpty); // the same orders: nothing new to announce
    });

    test('every change is saved on the tablet, including orders that left', () async {
      await cubit.start();
      await settle();
      expect(store.saved!.restaurantId, 'r1');
      expect(store.saved!.orders, hasLength(4));

      live.pushOrder(sampleOrder(id: 'o4', number: 99, table: 'T2', status: OrderStatus.served, version: 5));
      await settle();
      expect(store.saved!.orders.map((o) => o.id), isNot(contains('o4')));
      expect(store.saved!.tombstones['o4'], 5);
    });

    test('new orders chime once per rush; loading the board does not', () async {
      await cubit.start();
      expect(device.played, isEmpty);
      for (var i = 0; i < 3; i++) {
        live.pushOrder(
          sampleOrder(
            id: 'n$i',
            number: 200 + i,
            status: OrderStatus.placed,
            version: 1,
            createdAt: serverNow,
          ),
          type: 'order.created',
        );
      }
      await settle();
      expect(device.played, [KitchenSound.newOrder]);

      // A change to an order the kitchen already has is not news.
      live.pushOrder(
        sampleOrder(id: 'o3', number: 103, table: 'Patio 1', status: OrderStatus.accepted, version: 2),
      );
      await Future<void>.delayed(const Duration(milliseconds: 80));
      expect(device.played, hasLength(1));

      // Found by a reload (e.g. while the live connection was down): it chimes too.
      kitchen.orders = [
        ...kitchen.orders,
        sampleOrder(id: 'n9', number: 209, status: OrderStatus.placed, version: 1, createdAt: serverNow),
      ];
      await cubit.load();
      expect(device.played, [KitchenSound.newOrder, KitchenSound.newOrder]);
    });

    test('a muted tablet stays quiet; turning the sound on plays a test chime', () async {
      await cubit.start();
      await cubit.toggleSound();
      expect(cubit.state.soundOn, isFalse);
      expect(device.soundPreference, isFalse); // remembered on this tablet
      live.pushOrder(
        sampleOrder(id: 'n1', number: 201, status: OrderStatus.placed, version: 1),
        type: 'order.created',
      );
      await settle();
      expect(device.played, isEmpty);

      final next = KitchenCubit(kitchen, orders, live, device: device);
      await next.start();
      expect(next.state.soundOn, isFalse);
      await next.close();

      await cubit.toggleSound();
      expect(device.played, [KitchenSound.newOrder]);
    });

    test('cancelling an order that is being cooked tells the kitchen to stop', () async {
      await cubit.start();
      live.pushOrder(
        sampleOrder(id: 'o1', number: 101, table: 'T4', status: OrderStatus.cancelled, version: 4),
      );
      await settle();
      expect(cubit.state.notices.single.title, 'T4 #101 was cancelled');
      expect(cubit.state.notices.single.detail, 'Stop preparing: 2× Paneer Tikka, 4× Butter Naan');
      expect(device.played, [KitchenSound.cancelled]);

      // Not started yet: it simply leaves the board.
      live.pushOrder(
        sampleOrder(id: 'o3', number: 103, table: 'Patio 1', status: OrderStatus.cancelled, version: 2),
      );
      await settle();
      expect(cubit.state.notices, hasLength(1));
      expect(cubit.state.newOrders, isEmpty);
      expect(device.played, hasLength(1));

      cubit.dismissNotice('o1');
      expect(cubit.state.notices, isEmpty);
    });

    test('a cancellation missed while offline is reported after reconnecting, and stays gone', () async {
      await cubit.start();
      live.connection = LiveStatus.offline;
      // Meanwhile on the server: #102 (being cooked) was cancelled.
      kitchen.orders = [
        for (final o in kitchen.orders)
          if (o.id != 'o2') o,
      ];
      kitchen.recentlyCancelled = [('o2', 3)];
      live
        ..connection = LiveStatus.live
        ..push(LiveEvent.resync);
      await settle();
      expect(cubit.state.notices.single.title, 'T7 #102 was cancelled');
      expect(device.played, [KitchenSound.cancelled]);

      // A late copy from before the cancellation cannot bring it back.
      live.pushOrder(
        sampleOrder(id: 'o2', number: 102, table: 'T7', status: OrderStatus.accepted, version: 2),
      );
      await settle();
      expect(cubit.state.orders.map((o) => o.id), isNot(contains('o2')));
    });

    test('the screen stays awake while the kitchen board is open', () async {
      await cubit.start();
      expect(device.awake, isTrue);
      await cubit.close();
      expect(device.awake, isFalse);
    });

    test('even while live, the board is double-checked with the server regularly', () async {
      build(reconcileEvery: const Duration(milliseconds: 50));
      await cubit.start();
      expect(kitchen.loads, 1);
      await Future<void>.delayed(const Duration(milliseconds: 150));
      expect(kitchen.loads, greaterThanOrEqualTo(2));
    });

    test('a connection lost mid-action: no blind retry, buttons pause, the board refreshes', () async {
      live.connection = LiveStatus.offline;
      await cubit.start();
      expect(cubit.state.canAct, isTrue); // reloads still get through
      final messages = messagesOf(cubit, subscriptions);
      orders.failWith = offlineError;
      kitchen.failWith = offlineError;
      await cubit.accept(cubit.state.newOrders.single);
      await settle(); // state changes reach listeners asynchronously
      expect(orders.transitions, hasLength(1));
      expect(kitchen.loads, greaterThanOrEqualTo(2)); // it checked where things stand
      expect(messages.last, startsWith('No connection — this change may not have gone through'));
      expect(cubit.state.canAct, isFalse);

      await cubit.accept(cubit.state.newOrders.single);
      await settle();
      expect(orders.transitions, hasLength(1)); // paused, not sent
      expect(messages.last, KitchenCubit.offlineMessage);
    });

    test('a tap whose answer was lost is reported as done when retried, not as a conflict', () async {
      await cubit.start();
      final placed = cubit.state.newOrders.single;
      // The first tap reached the server, but the answer never arrived.
      kitchen.orders = [
        for (final o in kitchen.orders)
          o.id == 'o3'
              ? sampleOrder(
                  id: 'o3',
                  number: 103,
                  table: 'Patio 1',
                  status: OrderStatus.accepted,
                  version: 2,
                  createdAt: o.createdAt,
                  fromCustomer: true,
                )
              : o,
      ];
      orders.failWith = _conflict;
      final messages = messagesOf(cubit, subscriptions);
      await cubit.accept(placed);
      await settle();
      expect(messages.last, 'Patio 1 #103 was already accepted');
      expect(cubit.state.cooking.map((o) => o.id), contains('o3'));
    });
  });

  group('kitchen screen', () {
    Future<(FakeOrders, FakeLive)> pumpKitchen(
      WidgetTester tester, {
      Size size = const Size(1280, 800),
      StaffRole role = StaffRole.kitchen,
      LiveStatus status = LiveStatus.live,
    }) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final serverNow = DateTime.now();
      final orders = FakeOrders();
      final live = FakeLive(status: status);
      final kitchen = FakeKitchen(orders: sampleRush(serverNow), serverTime: serverNow);
      await tester.pumpWidget(
        wrap(
          BlocProvider(
            create: (_) => KitchenCubit(
              kitchen,
              orders,
              live,
              tick: const Duration(hours: 1),
              fallbackPoll: const Duration(hours: 1),
            )..start(),
            child: const KitchenPage(),
          ),
          auth: signedIn(staff(role, name: 'Chef')),
        ),
      );
      await tester.pumpAndSettle();
      return (orders, live);
    }

    testWidgets('tablet: new, cooking (with dishes summed per table) and ready side by side', (tester) async {
      await pumpKitchen(tester);
      expect(find.text('NEW ORDERS'), findsOneWidget);
      expect(find.text('COOKING'), findsOneWidget);
      expect(find.text('READY FOR PICKUP'), findsOneWidget);
      expect(find.text('4 active'), findsOneWidget);
      expect(find.text('TO COOK'), findsOneWidget);
      expect(find.text('3×'), findsOneWidget); // Paneer Tikka across T4 and T7
      expect(find.text('T4 ×2'), findsOneWidget);
      expect(find.text('T7 ×1'), findsOneWidget);
      expect(find.text('DELAYED'), findsOneWidget); // #101, waiting 22 min
      expect(find.text('Live'), findsOneWidget);
    });

    testWidgets('the next step is one big button per ticket', (tester) async {
      final (orders, _) = await pumpKitchen(tester);
      await tester.tap(find.byKey(const Key('ready-o1')));
      await tester.pumpAndSettle();
      expect(orders.transitions.single.$2, OrderStatus.ready);
      expect(find.text('T4 #101 is ready for pickup'), findsOneWidget);

      await tester.tap(find.byKey(const Key('cook-o3')));
      await tester.pumpAndSettle();
      expect(orders.transitions.last.$2, OrderStatus.preparing);
      expect(find.byKey(const Key('accept-o3')), findsNothing); // it moved on
    });

    testWidgets('staff without kitchen rights see tickets but no kitchen buttons', (tester) async {
      await pumpKitchen(tester, role: StaffRole.waiter);
      expect(find.byKey(const Key('ticket-o1')), findsOneWidget);
      expect(find.byKey(const Key('ready-o1')), findsNothing);
      expect(find.byKey(const Key('cook-o3')), findsNothing);
    });

    testWidgets('an offline tablet says so in words and keeps the orders', (tester) async {
      await pumpKitchen(tester, status: LiveStatus.offline);
      expect(find.text('Offline'), findsOneWidget);
      expect(find.textContaining('Live updates paused'), findsOneWidget);
      expect(find.byKey(const Key('ticket-o1')), findsOneWidget);
    });

    Future<(FakeLive, FakeKitchenDevice)> pumpWithDevice(
      WidgetTester tester, {
      SavedBoard? saved,
      bool serverDown = false,
      LiveStatus status = LiveStatus.live,
    }) async {
      tester.view.physicalSize = const Size(1280, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final serverNow = DateTime.now();
      final live = FakeLive(status: status);
      final device = FakeKitchenDevice();
      final kitchen = FakeKitchen(orders: sampleRush(serverNow), serverTime: serverNow);
      if (serverDown) kitchen.failWith = offlineError;
      await tester.pumpWidget(
        wrap(
          BlocProvider(
            create: (_) => KitchenCubit(
              kitchen,
              FakeOrders(),
              live,
              store: FakeBoardStore(saved),
              device: device,
              restaurantId: 'r1',
              tick: const Duration(hours: 1),
              fallbackPoll: const Duration(hours: 1),
            )..start(),
            child: const KitchenPage(),
          ),
          auth: signedIn(staff(StaffRole.kitchen, name: 'Chef')),
        ),
      );
      await tester.pumpAndSettle();
      return (live, device);
    }

    testWidgets('a restored board says so and pauses the buttons until the server answers', (tester) async {
      await pumpWithDevice(
        tester,
        saved: savedBoard(sampleRush(DateTime.now())),
        serverDown: true,
        status: LiveStatus.offline,
      );
      expect(find.textContaining('Showing the orders saved on this tablet (as of 7:41'), findsOneWidget);
      expect(find.textContaining('Buttons are paused'), findsOneWidget);
      expect(tester.widget<OutlinedButton>(find.byKey(const Key('accept-o3'))).onPressed, isNull);
      expect(tester.widget<FilledButton>(find.byKey(const Key('ready-o1'))).onPressed, isNull);
    });

    testWidgets('a cancelled order shows a stop notice until someone taps OK', (tester) async {
      final (live, _) = await pumpWithDevice(tester);
      live.pushOrder(
        sampleOrder(id: 'o1', number: 101, table: 'T4', status: OrderStatus.cancelled, version: 4),
      );
      await tester.pumpAndSettle();
      expect(find.text('T4 #101 WAS CANCELLED'), findsOneWidget);
      expect(find.text('Stop preparing: 2× Paneer Tikka, 4× Butter Naan'), findsOneWidget);
      expect(find.byKey(const Key('ticket-o1')), findsNothing);
      await tester.tap(find.byKey(const Key('dismiss-o1')));
      await tester.pumpAndSettle();
      expect(find.text('T4 #101 WAS CANCELLED'), findsNothing);
    });

    testWidgets('the sound switch says what it does and is remembered', (tester) async {
      final (_, device) = await pumpWithDevice(tester);
      expect(find.text('Sound on'), findsOneWidget);
      await tester.tap(find.byKey(const Key('sound-toggle')));
      await tester.pumpAndSettle();
      expect(find.text('Sound off'), findsOneWidget);
      expect(device.soundPreference, isFalse);
    });

    testWidgets('phone: the lanes become tabs', (tester) async {
      await pumpKitchen(tester, size: const Size(400, 860));
      expect(find.text('New (1)'), findsOneWidget);
      expect(find.text('Cooking (2)'), findsOneWidget);
      expect(find.text('Ready (1)'), findsOneWidget);
      await tester.tap(find.text('Ready (1)'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('ticket-o4')), findsOneWidget);
    });
  });
}
