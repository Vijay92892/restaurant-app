import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:staff_app/core/auth/staff_user.dart';
import 'package:staff_app/core/realtime/live_updates.dart';
import 'package:staff_app/core/routing/app_router.dart';
import 'package:staff_app/features/manage/manage_cubits.dart';
import 'package:staff_app/features/manage/manage_repositories.dart';
import 'package:staff_app/features/manage/pin_settings_page.dart';
import 'package:staff_app/features/manage/problem_reports_page.dart';
import 'package:staff_app/features/manage/staff_page.dart';
import 'package:staff_app/features/menu/sold_out_cubit.dart';
import 'package:staff_app/features/menu/sold_out_page.dart';
import 'package:staff_app/features/orders/order_card.dart';
import 'package:staff_app/features/orders/order_models.dart';
import 'package:staff_app/features/orders/orders_cubit.dart';
import 'package:staff_app/features/orders/orders_page.dart';
import 'package:staff_app/features/problems/problem_reports.dart';
import 'package:staff_app/features/shell/home_shell.dart';

import 'helpers.dart';

void main() {
  group('cancellation requests', () {
    Future<OrdersCubit> pumpOrders(WidgetTester tester, StaffUser user, FakeOrders orders) async {
      final cubit = OrdersCubit(orders, pollInterval: const Duration(hours: 1));
      await cubit.refresh();
      await tester.pumpWidget(
        wrap(
          BlocProvider.value(
            value: cubit,
            child: const Scaffold(body: OrdersPage()),
          ),
          auth: signedIn(user),
          orders: orders,
        ),
      );
      await tester.pumpAndSettle();
      return cubit;
    }

    FakeOrders requested() => FakeOrders(
      data: [
        sampleOrder(id: 'o1', number: 12, status: OrderStatus.preparing, cancelRequested: true),
        sampleOrder(id: 'o2', number: 13, status: OrderStatus.ready),
      ],
    );

    testWidgets('managers see them first and approve on the order itself', (tester) async {
      final orders = requested();
      final cubit = await pumpOrders(tester, staff(StaffRole.admin), orders);
      expect(find.text('Waiting for your decision · 1'), findsOneWidget);
      expect(find.text('Ready to serve · 1'), findsOneWidget);
      expect(cubit.state.awaitingDecision.single.id, 'o1');

      await tester.tap(find.byKey(const Key('approve-o1')));
      await tester.pumpAndSettle();
      expect(orders.reviewed.single.$2, isTrue);
      expect(find.text('Order #12 cancelled'), findsOneWidget);
      expect(find.text('Waiting for your decision · 1'), findsNothing);
      await cubit.close();
    });

    testWidgets('a manager can tell the kitchen to carry on', (tester) async {
      final orders = requested();
      final cubit = await pumpOrders(tester, staff(StaffRole.owner), orders);
      await tester.tap(find.byKey(const Key('reject-o1')));
      await tester.pumpAndSettle();
      expect(orders.reviewed.single.$2, isFalse);
      await cubit.close();
    });

    testWidgets('waiters see the request but cannot decide', (tester) async {
      final cubit = await pumpOrders(tester, staff(StaffRole.waiter), requested());
      expect(find.text('Waiting for your decision · 1'), findsNothing);
      expect(find.byKey(const Key('approve-o1')), findsNothing);
      expect(find.text('Cancel requested: Guest left early'), findsOneWidget);
      await cubit.close();
    });
  });

  testWidgets('a direct cancellation records why (order mistakes are counted)', (tester) async {
    final orders = FakeOrders(data: [sampleOrder(status: OrderStatus.placed)]);
    final cubit = OrdersCubit(orders, pollInterval: const Duration(hours: 1));
    await cubit.refresh();
    await tester.pumpWidget(
      wrap(
        BlocProvider.value(
          value: cubit,
          child: Scaffold(body: SingleChildScrollView(child: OrderCard(cubit.state.orders.single))),
        ),
        auth: signedIn(staff(StaffRole.waiter)),
        orders: orders,
      ),
    );
    await tester.tap(find.byTooltip('Cancel or report a problem'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel order'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Entered by mistake'));
    await tester.pump();
    await tester.tap(find.widgetWithText(FilledButton, 'Cancel order'));
    await tester.pumpAndSettle();
    expect(orders.cancelReasons.single, 'mistake');
    await cubit.close();
  });

  group('problem reports', () {
    testWidgets('anyone reports a problem from the account menu', (tester) async {
      final problems = FakeProblems();
      final waiter = staff(StaffRole.waiter);
      await tester.pumpWidget(
        wrap(
          Scaffold(
            appBar: AppBar(actions: [AccountMenu(user: waiter)]),
          ),
          auth: signedIn(waiter),
          problems: problems,
        ),
      );
      await tester.tap(find.byTooltip('Account'));
      await tester.pumpAndSettle();
      expect(find.text('Manage restaurant'), findsNothing); // waiters don't manage
      expect(find.text('Sold-out dishes'), findsNothing);
      await tester.tap(find.text('Report a problem'));
      await tester.pumpAndSettle();
      final send = find.widgetWithText(FilledButton, 'Send report');
      expect(tester.widget<FilledButton>(send).onPressed, isNull); // pick what it is about first
      await tester.tap(find.text('Customer needed help'));
      await tester.enterText(find.byType(TextField), 'Guest could not find the PIN');
      await tester.pump();
      await tester.tap(send);
      await tester.pumpAndSettle();
      expect(problems.sent.single, (ProblemCategory.customerHelp, 'Guest could not find the PIN', null));
      expect(find.text('Thanks — your manager will see this.'), findsOneWidget);
    });

    testWidgets('a report about an order carries the order', (tester) async {
      final problems = FakeProblems();
      final orders = FakeOrders(data: [sampleOrder(status: OrderStatus.ready)]);
      final cubit = OrdersCubit(orders, pollInterval: const Duration(hours: 1));
      await cubit.refresh();
      await tester.pumpWidget(
        wrap(
          BlocProvider.value(
            value: cubit,
            child: Scaffold(body: SingleChildScrollView(child: OrderCard(cubit.state.orders.single))),
          ),
          auth: signedIn(staff(StaffRole.waiter)),
          problems: problems,
        ),
      );
      await tester.tap(find.byTooltip('Cancel or report a problem'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Report a problem'));
      await tester.pumpAndSettle();
      expect(find.text('Report a problem with #12 (T4)'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, 'Send report')); // "Order mistake" preselected
      await tester.pumpAndSettle();
      expect(problems.sent.single, (ProblemCategory.orderMistake, '', 'o1'));
      await cubit.close();
    });

    testWidgets('managers read them, newest first', (tester) async {
      final problems = FakeProblems([
        ProblemReport(
          id: 'p1',
          category: ProblemCategory.network,
          note: 'Kitchen tablet offline for 5 minutes',
          orderNumber: null,
          tableLabel: null,
          reportedBy: 'Meena',
          reportedByRole: 'kitchen',
          createdAt: DateTime(2026, 10, 4, 20, 15),
        ),
        ProblemReport(
          id: 'p2',
          category: ProblemCategory.orderMistake,
          note: null,
          orderNumber: 12,
          tableLabel: 'T4',
          reportedBy: 'Ravi',
          reportedByRole: 'waiter',
          createdAt: DateTime(2026, 10, 4, 19, 50),
        ),
      ]);
      await tester.pumpWidget(
        wrap(
          BlocProvider(
            create: (_) => ProblemReportsCubit(problems)..load(),
            child: const ProblemReportsPage(),
          ),
          auth: signedIn(staff(StaffRole.owner)),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Connection problem'), findsOneWidget);
      expect(find.text('Kitchen tablet offline for 5 minutes'), findsOneWidget);
      expect(find.textContaining('order #12, T4'), findsOneWidget);
    });
  });

  group('sold-out switches', () {
    Future<(FakeAvailability, FakeLive)> pumpSoldOut(WidgetTester tester) async {
      final availability = FakeAvailability();
      final live = FakeLive();
      await tester.pumpWidget(
        wrap(
          BlocProvider(
            create: (_) => SoldOutCubit(availability, live: live)..start(),
            child: const SoldOutPage(),
          ),
          auth: signedIn(staff(StaffRole.kitchen, name: 'Chef')),
        ),
      );
      await tester.pumpAndSettle();
      return (availability, live);
    }

    testWidgets('the kitchen marks a dish sold out, in words as well as colour', (tester) async {
      final (availability, _) = await pumpSoldOut(tester);
      expect(find.text('Starters'), findsOneWidget);
      expect(find.text('Extras'), findsOneWidget); // options too
      expect(find.text('SOLD OUT'), findsOneWidget); // Chicken 65
      expect(find.text('Sold out only · 1'), findsOneWidget);

      await tester.tap(
        find.descendant(of: find.byKey(const Key('switch-paneer')), matching: find.byType(Switch)),
      );
      await tester.pumpAndSettle();
      expect(availability.changes.single, ('paneer', false));
      expect(find.text('SOLD OUT'), findsNWidgets(2));
      expect(find.text('Paneer Tikka is now SOLD OUT'), findsOneWidget);

      await tester.tap(find.text('Sold out only · 2'));
      await tester.pumpAndSettle();
      expect(find.text('Butter Naan'), findsNothing);
      expect(find.text('Chicken 65'), findsOneWidget);
    });

    testWidgets('search, and changes made on another device', (tester) async {
      final (availability, live) = await pumpSoldOut(tester);
      await tester.enterText(find.byType(TextField), 'naan');
      await tester.pump();
      expect(find.text('Butter Naan'), findsOneWidget);
      expect(find.text('Paneer Tikka'), findsNothing);

      final loads = availability.loads;
      live.push(const LiveEvent(id: 'e1', type: 'menu.availability_changed'));
      await tester.pumpAndSettle();
      expect(availability.loads, loads + 1);
    });

    testWidgets('a refused switch says why and shows the server state', (tester) async {
      final (availability, _) = await pumpSoldOut(tester);
      availability.failWith = offlineError;
      await tester.tap(find.byKey(const Key('switch-naan')));
      await tester.pumpAndSettle();
      expect(find.text('No connection.'), findsOneWidget);
      expect(find.text('SOLD OUT'), findsOneWidget); // still only Chicken 65
    });
  });

  group('table PIN screen', () {
    Future<FakePin> pumpPin(WidgetTester tester, StaffUser user) async {
      final pin = FakePin();
      await tester.pumpWidget(
        wrap(
          BlocProvider(create: (_) => PinSettingsCubit(pin)..start(), child: const PinSettingsPage()),
          auth: signedIn(user),
        ),
      );
      await tester.pumpAndSettle();
      return pin;
    }

    testWidgets('the PIN stays hidden until asked for', (tester) async {
      await pumpPin(tester, staff(StaffRole.owner));
      expect(find.text('••••'), findsOneWidget);
      expect(find.text('4827'), findsNothing);
      await tester.tap(find.byTooltip('Show PIN'));
      await tester.pump();
      expect(find.text('4827'), findsOneWidget);
      await tester.tap(find.byTooltip('Show to a guest (full screen)'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('pin-full-screen')), findsOneWidget);
      await tester.tap(find.text('Tap anywhere to close'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('pin-full-screen')), findsNothing);
    });

    testWidgets('managers change how often it changes, change it now, and sign phones out', (tester) async {
      final pin = await pumpPin(tester, staff(StaffRole.admin));
      final save = find.widgetWithText(FilledButton, 'Save');
      expect(tester.widget<FilledButton>(save).onPressed, isNull); // nothing changed yet
      await tester.tap(find.text('Every 2 hours'));
      await tester.pump();
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(pin.saved.single, (120, 4));
      expect(find.text('PIN settings saved'), findsOneWidget);

      await tester.scrollUntilVisible(find.byKey(const Key('rotate-now')), 200);
      await tester.tap(find.byKey(const Key('rotate-now')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Also sign out phones already ordering'));
      await tester.pump();
      await tester.tap(find.text('Change PIN'));
      await tester.pumpAndSettle();
      expect(pin.rotations.single, isTrue);
      expect(find.text('New PIN: 1593. 3 phones must enter it.'), findsOneWidget);

      await tester.scrollUntilVisible(find.byKey(const Key('end-all-sessions')), 200);
      await tester.tap(find.byKey(const Key('end-all-sessions')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Sign them out'));
      await tester.pumpAndSettle();
      expect(pin.endAllCalls, 1);
    });

    testWidgets('waiters see the PIN but cannot change anything', (tester) async {
      await pumpPin(tester, staff(StaffRole.waiter));
      expect(find.text('Every 2 hours'), findsNothing);
      expect(find.byKey(const Key('rotate-now')), findsNothing);
      expect(find.byKey(const Key('end-all-sessions')), findsNothing);
    });
  });

  group('staff accounts', () {
    Future<FakeStaffAccounts> pumpStaff(WidgetTester tester, StaffUser user) async {
      final accounts = FakeStaffAccounts();
      await tester.pumpWidget(
        wrap(
          BlocProvider(
            create: (_) => StaffCubit(accounts, passwords: () => 'abcd-efgh-jkmn')..load(),
            child: const StaffPage(),
          ),
          auth: signedIn(user),
        ),
      );
      await tester.pumpAndSettle();
      return accounts;
    }

    testWidgets('a new person gets a temporary password, shown once', (tester) async {
      final accounts = await pumpStaff(tester, staff(StaffRole.owner, name: 'Asha'));
      expect(find.text('Asha · Owner'), findsOneWidget);
      expect(find.textContaining('Switched off'), findsOneWidget); // Meena
      await tester.tap(find.text('Add staff'));
      await tester.pumpAndSettle();
      await tester.enterText(find.widgetWithText(TextFormField, 'Name (shown on orders)'), 'Priya');
      await tester.enterText(find.widgetWithText(TextFormField, 'Username (for signing in)'), 'Priya');
      await tester.tap(find.text('Add and create a temporary password'));
      await tester.pumpAndSettle();
      expect(accounts.added.single, ('priya', 'Priya', StaffRole.waiter, 'abcd-efgh-jkmn'));
      expect(find.text('Temporary password for Priya'), findsOneWidget);
      expect(find.text('abcd-efgh-jkmn'), findsOneWidget);
      await tester.tap(find.text('Done'));
      await tester.pumpAndSettle();
      expect(find.text('Priya · Waiter'), findsOneWidget);
    });

    testWidgets('reset a password, switch someone off; never the owner or yourself', (tester) async {
      final accounts = await pumpStaff(tester, staff(StaffRole.admin, name: 'Kiran'));
      // The owner's row offers nothing.
      await tester.tap(find.byKey(const Key('staff-asha')));
      await tester.pumpAndSettle();
      expect(find.text('Reset password'), findsNothing);

      await tester.tap(find.byKey(const Key('staff-ravi')));
      await tester.pumpAndSettle();
      expect(find.text('Make manager (admin)'), findsNothing); // only the owner makes admins
      await tester.tap(find.text('Reset password'));
      await tester.pumpAndSettle();
      expect(accounts.resets.single, ('u-ravi', 'abcd-efgh-jkmn'));
      expect(find.text('Temporary password for Ravi'), findsOneWidget);
      await tester.tap(find.text('Done'));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('staff-ravi')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Switch off (cannot sign in)'));
      await tester.pumpAndSettle();
      expect(accounts.updates.single, ('u-ravi', null, false));
      expect(find.text('Ravi is switched off and signed out'), findsOneWidget);
    });

    test('temporary passwords avoid look-alike characters', () {
      final password = temporaryPassword();
      expect(password, matches(RegExp(r'^[a-hjkmnp-z2-9]{4}-[a-hjkmnp-z2-9]{4}-[a-hjkmnp-z2-9]{4}$')));
    });
  });

  test('screens open only for the people who can use them', () {
    final waiter = staff(StaffRole.waiter);
    final kitchen = staff(StaffRole.kitchen, name: 'Chef');
    final admin = staff(StaffRole.admin);
    final owner = staff(StaffRole.owner);
    expect(mayOpen(waiter, '/tables'), isTrue);
    expect(mayOpen(waiter, '/manage'), isFalse);
    expect(mayOpen(waiter, '/manage/pin'), isTrue); // waiters may see the PIN
    expect(mayOpen(waiter, '/manage/staff'), isFalse);
    expect(mayOpen(waiter, '/sold-out'), isFalse);
    expect(mayOpen(waiter, '/kitchen'), isFalse);
    expect(mayOpen(kitchen, '/kitchen'), isTrue);
    expect(mayOpen(kitchen, '/sold-out'), isTrue);
    expect(mayOpen(kitchen, '/tables'), isFalse);
    expect(mayOpen(kitchen, '/manage'), isFalse);
    for (final path in [
      '/manage',
      '/manage/pin',
      '/manage/staff',
      '/manage/reports',
      '/sold-out',
      '/kitchen',
    ]) {
      expect(mayOpen(admin, path), isTrue, reason: path);
      expect(mayOpen(owner, path), isTrue, reason: path);
    }
  });
}
