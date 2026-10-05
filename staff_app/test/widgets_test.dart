import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:staff_app/core/auth/auth_bloc.dart';
import 'package:staff_app/core/auth/staff_user.dart';
import 'package:staff_app/features/auth/login_page.dart';
import 'package:staff_app/features/orders/compose_order_page.dart';
import 'package:staff_app/features/orders/order_card.dart';
import 'package:staff_app/features/orders/order_models.dart';
import 'package:staff_app/features/orders/orders_cubit.dart';
import 'package:staff_app/features/tables/table_models.dart';
import 'package:staff_app/features/tables/tables_cubit.dart';
import 'package:staff_app/features/tables/tables_page.dart';

import 'helpers.dart';

void main() {
  setUpAll(() => registerFallbackValue(const AuthStarted()));

  testWidgets('login validates input and shows server messages', (tester) async {
    final auth = MockAuthBloc();
    when(() => auth.state).thenReturn(
      const AuthState(
        status: AuthStatus.unauthenticated,
        message: 'The restaurant code, username or password is not right.',
      ),
    );
    await tester.pumpWidget(wrap(const LoginPage(rememberedCode: 'spice-garden'), auth: auth));
    expect(find.text('spice-garden'), findsOneWidget); // remembered code
    expect(find.byKey(const Key('login-error')), findsOneWidget);
    await tester.tap(find.text('Sign in'));
    await tester.pump();
    expect(find.text('Enter your username'), findsOneWidget);
    verifyNever(() => auth.add(any()));

    await tester.enterText(find.widgetWithText(TextFormField, 'Username'), 'ravi');
    await tester.enterText(find.widgetWithText(TextFormField, 'Password'), 'secret-pass');
    await tester.tap(find.text('Sign in'));
    verify(() => auth.add(any(that: isA<AuthLoginRequested>()))).called(1);
  });

  testWidgets('forgot password sends a reset link request without revealing accounts', (tester) async {
    final auth = MockAuthBloc();
    when(() => auth.state).thenReturn(const AuthState(status: AuthStatus.unauthenticated));
    final requests = <(String, String)>[];
    await tester.pumpWidget(
      wrap(
        LoginPage(
          rememberedCode: 'spice-garden',
          requestReset: (code, email) async => requests.add((code, email)),
        ),
        auth: auth,
      ),
    );
    await tester.tap(find.text('Forgot password?'));
    await tester.pumpAndSettle();
    expect(find.textContaining('ask your manager'), findsOneWidget);
    await tester.tap(find.text('Send link'));
    await tester.pump();
    expect(find.text('Enter your email address'), findsOneWidget);
    await tester.enterText(find.widgetWithText(TextFormField, 'Email address'), ' Asha@Example.com ');
    await tester.tap(find.text('Send link'));
    await tester.pumpAndSettle();
    expect(requests.single, ('spice-garden', 'asha@example.com'));
    expect(find.text('Check your email'), findsOneWidget);
  });

  testWidgets('tables show the PIN, who is seated and what is ready', (tester) async {
    final tables = FakeTables(
      data: sampleTables,
      pinInfo: PinInfo(pin: '4827', version: 3, nextRotationAt: DateTime(2026, 10, 4, 18)),
    );
    await tester.pumpWidget(
      wrap(
        BlocProvider(
          create: (_) =>
              TablesCubit(tables, canViewPin: true, pollInterval: const Duration(hours: 1))..start(),
          child: const Scaffold(body: TablesPage()),
        ),
        auth: signedIn(staff(StaffRole.waiter)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('table-pin')), findsOneWidget);
    expect(find.text('4827'), findsOneWidget);
    expect(find.text('1 ready to serve'), findsOneWidget);
    expect(find.text('2 phones ordering'), findsOneWidget);
    expect(find.text('Switched off'), findsOneWidget);
    await tester.pumpWidget(const SizedBox()); // dispose cubit + timer
  });

  group('order card actions follow the role', () {
    Future<OrdersCubit> pumpCard(
      WidgetTester tester,
      StaffUser user,
      StaffOrder order,
      FakeOrders orders,
    ) async {
      orders.data = [order];
      final cubit = OrdersCubit(orders, pollInterval: const Duration(hours: 1));
      await cubit.refresh();
      await tester.pumpWidget(
        wrap(
          BlocProvider.value(
            value: cubit,
            child: Scaffold(body: SingleChildScrollView(child: OrderCard(order))),
          ),
          auth: signedIn(user),
        ),
      );
      return cubit;
    }

    testWidgets('a waiter can mark a ready order served', (tester) async {
      final orders = FakeOrders();
      final order = sampleOrder();
      final cubit = await pumpCard(tester, staff(StaffRole.waiter), order, orders);
      expect(find.text('Mark served'), findsOneWidget);
      await tester.tap(find.text('Mark served'));
      await tester.pump();
      expect(orders.transitions.single, (order, OrderStatus.served));
      await cubit.close();
    });

    testWidgets('the kitchen cannot serve', (tester) async {
      final cubit = await pumpCard(
        tester,
        staff(StaffRole.kitchen, name: 'Chef'),
        sampleOrder(),
        FakeOrders(),
      );
      expect(find.text('Mark served'), findsNothing);
      await cubit.close();
    });

    testWidgets('a waiter asks a manager to cancel an order being cooked', (tester) async {
      final orders = FakeOrders();
      final order = sampleOrder(status: OrderStatus.preparing);
      final cubit = await pumpCard(tester, staff(StaffRole.waiter), order, orders);
      await tester.tap(find.byTooltip('Cancel or report a problem'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Ask a manager to cancel'));
      await tester.pumpAndSettle();
      expect(find.text('Ask a manager to cancel #12'), findsOneWidget);
      await tester.enterText(find.byType(TextField), 'Guest left');
      await tester.tap(find.text('Send request'));
      await tester.pumpAndSettle();
      expect(orders.requested.single.$2, 'Guest left');
      expect(orders.cancelled, isEmpty);
      await cubit.close();
    });

    testWidgets('customer orders and cancellation requests are labelled', (tester) async {
      final cubit = await pumpCard(
        tester,
        staff(StaffRole.admin),
        sampleOrder(status: OrderStatus.preparing, fromCustomer: true, cancelRequested: true),
        FakeOrders(),
      );
      expect(find.text('From customer phone'), findsOneWidget);
      expect(find.text('Cancel requested'), findsOneWidget);
      expect(find.text('Cancel requested: Guest left early'), findsOneWidget);
      await cubit.close();
    });
  });

  testWidgets('a waiter builds an order and sends it to the kitchen', (tester) async {
    final orders = FakeOrders();
    tester.view.physicalSize = const Size(1080, 2200);
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      wrap(
        const ComposeOrderPage(tableId: 't4', label: 'T4'),
        auth: signedIn(staff(StaffRole.waiter)),
        orders: orders,
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Sold out'), findsOneWidget); // Egg Bhurji

    await tester.tap(find.widgetWithText(FilledButton, 'Add').first); // Chicken 65
    await tester.pump();
    await tester.tap(find.text('Choose')); // Paneer Tikka has options
    await tester.pumpAndSettle();
    await tester.tap(find.text('Add to order'));
    await tester.pump();
    expect(find.text('Choose a spice level'), findsOneWidget); // required choice enforced
    await tester.tap(find.text('Medium'));
    await tester.tap(find.text('Add to order'));
    await tester.pumpAndSettle();

    expect(find.text('Review order · 2 items'), findsOneWidget);
    await tester.tap(find.byKey(const Key('review-order')));
    await tester.pumpAndSettle();
    expect(find.text('Spice level: Medium'), findsOneWidget);
    await tester.tap(find.byKey(const Key('send-order')));
    await tester.pumpAndSettle();
    final (tableId, draft, key) = orders.placed.single;
    expect(tableId, 't4');
    expect(draft.lines.map((l) => l.itemId), ['chicken', 'paneer']);
    expect(draft.lines.last.modifierIds, ['m2']);
    expect(key, matches(RegExp(r'^[a-f0-9]{32}$')));
  });
}
