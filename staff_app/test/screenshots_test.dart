// Renders every waiter screen to PNG with real fonts for visual review:
//   SCREENSHOTS=1 flutter test test/screenshots_test.dart --update-goldens
// Skipped in normal runs (font rendering differs between machines).
import 'dart:io';

import 'package:bloc_test/bloc_test.dart';
import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:staff_app/core/auth/auth_bloc.dart';
import 'package:staff_app/core/auth/staff_user.dart';
import 'package:staff_app/core/realtime/live_updates.dart';
import 'package:staff_app/core/theme/app_theme.dart';
import 'package:staff_app/features/kitchen/kitchen_cubit.dart';
import 'package:staff_app/features/kitchen/kitchen_page.dart';
import 'package:staff_app/features/manage/manage_cubits.dart';
import 'package:staff_app/features/manage/pin_settings_page.dart';
import 'package:staff_app/features/manage/staff_page.dart';
import 'package:staff_app/features/menu/sold_out_cubit.dart';
import 'package:staff_app/features/menu/sold_out_page.dart';
import 'package:staff_app/features/problems/report_problem_sheet.dart';
import 'package:staff_app/features/auth/login_page.dart';
import 'package:staff_app/features/orders/compose_order_page.dart';
import 'package:staff_app/features/orders/order_models.dart';
import 'package:staff_app/features/orders/orders_cubit.dart';
import 'package:staff_app/features/orders/orders_page.dart';
import 'package:staff_app/features/tables/table_detail_page.dart';
import 'package:staff_app/features/tables/table_models.dart';
import 'package:staff_app/features/tables/tables_cubit.dart';
import 'package:staff_app/features/tables/tables_page.dart';

import 'helpers.dart';

final _skip = !Platform.environment.containsKey('SCREENSHOTS');

/// A stand-in for the router's shell (app bar + bottom navigation).
Widget shell(String title, int index, Widget body) => Scaffold(
  appBar: AppBar(
    title: Text(title),
    actions: const [
      Padding(
        padding: EdgeInsets.all(8),
        child: CircleAvatar(child: Text('R')),
      ),
    ],
  ),
  body: body,
  bottomNavigationBar: NavigationBar(
    selectedIndex: index,
    destinations: const [
      NavigationDestination(
        icon: Icon(Icons.table_restaurant_outlined),
        selectedIcon: Icon(Icons.table_restaurant),
        label: 'Tables',
      ),
      NavigationDestination(
        icon: Icon(Icons.receipt_long_outlined),
        selectedIcon: Icon(Icons.receipt_long),
        label: 'Orders',
      ),
    ],
  ),
);

void main() {
  setUpAll(() async {
    await loadRealFonts();
    FocusManager.instance.highlightStrategy = FocusHighlightStrategy.alwaysTouch; // touch device
  });

  Future<void> phone(WidgetTester tester) async {
    tester.view.physicalSize = const Size(412 * 2.5, 915 * 2.5);
    tester.view.devicePixelRatio = 2.5;
    debugDisableShadows = false; // from the first frame (layers are cached); restored in snap()
    addTearDown(tester.view.reset);
  }

  Future<void> snap(WidgetTester tester, String name) async {
    debugDisableShadows = false; // real shadows, not the test-mode black outlines
    try {
      await tester.pumpAndSettle();
      await expectLater(find.byType(MaterialApp), matchesGoldenFile('screens/$name.png'));
    } finally {
      debugDisableShadows = true; // the test framework requires it restored
      await tester.pump();
    }
  }

  final waiter = staff(StaffRole.waiter);
  final pin = PinInfo(pin: '4827', version: 3, nextRotationAt: DateTime(2026, 10, 4, 18));
  final orders = [
    sampleOrder(),
    sampleOrder(id: 'o2', number: 14, status: OrderStatus.preparing, table: 'T2', fromCustomer: true),
    sampleOrder(id: 'o3', number: 15, status: OrderStatus.placed, table: 'Patio 1'),
    sampleOrder(id: 'o4', number: 9, status: OrderStatus.served, table: 'T2'),
  ];

  testWidgets('1 login', skip: _skip, (tester) async {
    await phone(tester);
    final auth = MockAuthBloc();
    whenListen(
      auth,
      const Stream<AuthState>.empty(),
      initialState: const AuthState(status: AuthStatus.unauthenticated),
    );
    await tester.pumpWidget(wrap(const LoginPage(rememberedCode: 'spice-garden'), auth: auth));
    await snap(tester, '1-login');
  });

  for (final dark in [false, true]) {
    testWidgets('2 tables${dark ? ' dark' : ''}', skip: _skip, (tester) async {
      await phone(tester);
      await tester.pumpWidget(
        wrap(
          BlocProvider(
            create: (_) => TablesCubit(
              FakeTables(data: sampleTables, pinInfo: pin),
              canViewPin: true,
              pollInterval: const Duration(hours: 1),
            )..start(),
            child: shell('Tables', 0, const TablesPage()),
          ),
          auth: signedIn(waiter),
          theme: dark ? AppTheme.dark() : null,
        ),
      );
      await snap(tester, dark ? '2-tables-dark' : '2-tables');
      await tester.pumpWidget(const SizedBox());
    });
  }

  testWidgets('3 table detail', skip: _skip, (tester) async {
    await phone(tester);
    await tester.pumpWidget(
      wrap(
        const TableDetailPage(tableId: 't4', label: 'T4'),
        auth: signedIn(waiter),
        orders: FakeOrders(
          data: [
            orders[0],
            sampleOrder(id: 'o5', number: 13, status: OrderStatus.preparing),
          ],
        ),
      ),
    );
    await snap(tester, '3-table-detail');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('4 new order + options + review', skip: _skip, (tester) async {
    await phone(tester);
    await tester.pumpWidget(
      wrap(
        const ComposeOrderPage(tableId: 't4', label: 'T4'),
        auth: signedIn(waiter),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Add').first);
    await snap(tester, '4-compose-menu');
    await tester.tap(find.text('Choose'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Medium'));
    await tester.tap(find.text('Extra cheese'));
    await snap(tester, '5-options-sheet');
    await tester.tap(find.text('Add to order'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Review order · 2 items'));
    await snap(tester, '6-review');
  });

  testWidgets('7 orders', skip: _skip, (tester) async {
    await phone(tester);
    await tester.pumpWidget(
      wrap(
        BlocProvider(
          create: (_) =>
              OrdersCubit(FakeOrders(data: orders), pollInterval: const Duration(hours: 1))..start(),
          child: shell('Orders', 1, const OrdersPage()),
        ),
        auth: signedIn(waiter),
      ),
    );
    await snap(tester, '7-orders');
    await tester.pumpWidget(const SizedBox());
  });

  Future<void> tablet(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1280 * 1.5, 800 * 1.5);
    tester.view.devicePixelRatio = 1.5;
    debugDisableShadows = false;
    addTearDown(tester.view.reset);
  }

  Widget kitchenScreen({
    LiveStatus status = LiveStatus.live,
    bool dark = false,
    FakeLive? live,
    bool restoredOffline = false,
  }) {
    final now = DateTime.now();
    final kitchen = FakeKitchen(orders: sampleRush(now), serverTime: now);
    if (restoredOffline) kitchen.failWith = offlineError;
    return wrap(
      BlocProvider(
        create: (_) => KitchenCubit(
          kitchen,
          FakeOrders(),
          live ?? FakeLive(status: status),
          store: FakeBoardStore(restoredOffline ? savedBoard(sampleRush(now)) : null),
          device: FakeKitchenDevice(),
          restaurantId: 'r1',
          tick: const Duration(hours: 1),
          fallbackPoll: const Duration(hours: 1),
        )..start(),
        child: const KitchenPage(),
      ),
      auth: signedIn(staff(StaffRole.kitchen, name: 'Chef')),
      theme: dark ? AppTheme.dark() : null,
    );
  }

  for (final dark in [false, true]) {
    testWidgets('8 kitchen tablet${dark ? ' dark' : ''}', skip: _skip, (tester) async {
      await tablet(tester);
      await tester.pumpWidget(kitchenScreen(dark: dark));
      await snap(tester, dark ? '8-kitchen-tablet-dark' : '8-kitchen-tablet');
      await tester.pumpWidget(const SizedBox());
    });
  }

  testWidgets('9 kitchen offline', skip: _skip, (tester) async {
    await tablet(tester);
    await tester.pumpWidget(kitchenScreen(status: LiveStatus.offline));
    await snap(tester, '9-kitchen-offline');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('11 kitchen restored while offline', skip: _skip, (tester) async {
    await tablet(tester);
    await tester.pumpWidget(kitchenScreen(status: LiveStatus.offline, restoredOffline: true));
    await snap(tester, '11-kitchen-restored-offline');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('12 kitchen cancellation notice', skip: _skip, (tester) async {
    await tablet(tester);
    final live = FakeLive();
    await tester.pumpWidget(kitchenScreen(live: live));
    await tester.pumpAndSettle();
    live.pushOrder(
      sampleOrder(id: 'o1', number: 101, table: 'T4', status: OrderStatus.cancelled, version: 4),
    );
    await snap(tester, '12-kitchen-cancel-notice');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('10 kitchen phone', skip: _skip, (tester) async {
    await phone(tester);
    await tester.pumpWidget(kitchenScreen());
    await snap(tester, '10-kitchen-phone-new');
    await tester.tap(find.text('Cooking (2)'));
    await snap(tester, '10-kitchen-phone-cooking');
    await tester.pumpWidget(const SizedBox());
  });

  // --- Phase 11: manager tools ----------------------------------------------------------------

  testWidgets('13 orders: a manager decides', skip: _skip, (tester) async {
    await phone(tester);
    final requested = [
      sampleOrder(id: 'o1', number: 12, status: OrderStatus.preparing, cancelRequested: true),
      sampleOrder(id: 'o2', number: 13, status: OrderStatus.ready, table: 'T2'),
    ];
    await tester.pumpWidget(
      wrap(
        BlocProvider(
          create: (_) =>
              OrdersCubit(FakeOrders(data: requested), pollInterval: const Duration(hours: 1))..start(),
          child: shell('Orders', 1, const OrdersPage()),
        ),
        auth: signedIn(staff(StaffRole.admin, name: 'Kiran')),
      ),
    );
    await snap(tester, '13-orders-manager-decides');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('14 sold-out dishes', skip: _skip, (tester) async {
    await phone(tester);
    await tester.pumpWidget(
      wrap(
        BlocProvider(create: (_) => SoldOutCubit(FakeAvailability())..start(), child: const SoldOutPage()),
        auth: signedIn(staff(StaffRole.kitchen, name: 'Chef')),
      ),
    );
    await snap(tester, '14-sold-out');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('15 table PIN', skip: _skip, (tester) async {
    await phone(tester);
    await tester.pumpWidget(
      wrap(
        BlocProvider(create: (_) => PinSettingsCubit(FakePin())..start(), child: const PinSettingsPage()),
        auth: signedIn(staff(StaffRole.owner, name: 'Asha')),
      ),
    );
    await snap(tester, '15-table-pin');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('16 staff', skip: _skip, (tester) async {
    await phone(tester);
    await tester.pumpWidget(
      wrap(
        BlocProvider(create: (_) => StaffCubit(FakeStaffAccounts())..load(), child: const StaffPage()),
        auth: signedIn(staff(StaffRole.owner, name: 'Asha')),
      ),
    );
    await snap(tester, '16-staff');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('17 report a problem', skip: _skip, (tester) async {
    await phone(tester);
    await tester.pumpWidget(
      wrap(
        Builder(
          builder: (context) => shell(
            'Tables',
            0,
            Center(
              child: FilledButton(
                onPressed: () => showReportProblemSheet(context),
                child: const Text('open'),
              ),
            ),
          ),
        ),
        auth: signedIn(waiter),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Customer needed help'));
    await tester.enterText(find.byType(TextField), 'Guest at T4 could not find the PIN');
    await snap(tester, '17-report-problem');
    await tester.pumpWidget(const SizedBox());
  });
}
