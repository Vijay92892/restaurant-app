import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';

import '../../features/auth/change_password_page.dart';
import '../../features/auth/login_page.dart';
import '../../features/kitchen/kitchen_cubit.dart';
import '../../features/kitchen/kitchen_device.dart';
import '../../features/kitchen/kitchen_page.dart';
import '../../features/kitchen/kitchen_repository.dart';
import '../../features/kitchen/kitchen_store.dart';
import '../../features/manage/manage_cubits.dart';
import '../../features/manage/manage_page.dart';
import '../../features/manage/manage_repositories.dart';
import '../../features/manage/pin_settings_page.dart';
import '../../features/manage/problem_reports_page.dart';
import '../../features/manage/staff_page.dart';
import '../../features/menu/availability.dart';
import '../../features/menu/sold_out_cubit.dart';
import '../../features/menu/sold_out_page.dart';
import '../../features/orders/compose_order_page.dart';
import '../../features/orders/orders_cubit.dart';
import '../../features/orders/orders_page.dart';
import '../../features/orders/orders_repository.dart';
import '../../features/problems/problem_reports.dart';
import '../../features/shell/home_shell.dart';
import '../../features/tables/table_detail_page.dart';
import '../../features/tables/tables_cubit.dart';
import '../../features/tables/tables_page.dart';
import '../../features/tables/tables_repository.dart';
import '../auth/auth_bloc.dart';
import '../auth/staff_user.dart';
import '../realtime/live_updates.dart';

/// Re-runs the router's redirect whenever the auth state changes.
class _AuthListenable extends ChangeNotifier {
  _AuthListenable(Stream<AuthState> stream) {
    _subscription = stream.listen((_) => notifyListeners());
  }

  late final StreamSubscription<AuthState> _subscription;

  @override
  void dispose() {
    _subscription.cancel();
    super.dispose();
  }
}

String homeFor(StaffUser user) => user.role == StaffRole.kitchen ? '/kitchen' : '/tables';

/// Screens a signed-in user may open, by permission (the server checks every request anyway;
/// this keeps people out of screens that would only show "not allowed").
bool mayOpen(StaffUser user, String path) {
  bool under(String prefix) => path == prefix || path.startsWith('$prefix/');
  if (under('/kitchen')) return user.can(Perm.ordersKitchen);
  if (under('/sold-out')) return user.can(Perm.menuAvailability);
  if (under('/manage/pin')) return user.can(Perm.pinView);
  if (under('/manage/staff')) return user.can(Perm.staffRead);
  if (under('/manage/reports')) return user.can(Perm.problemReportsRead);
  if (under('/manage')) return canManage(user);
  // Kitchen staff work from the kitchen screen only.
  return user.role != StaffRole.kitchen;
}

GoRouter buildRouter(AuthBloc auth, {String? rememberedCode}) {
  return GoRouter(
    initialLocation: '/splash',
    refreshListenable: _AuthListenable(auth.stream),
    redirect: (context, state) {
      final auth$ = auth.state;
      final at = state.matchedLocation;
      switch (auth$.status) {
        case AuthStatus.unknown:
          return at == '/splash' ? null : '/splash';
        case AuthStatus.unauthenticated:
          return at == '/login' ? null : '/login';
        case AuthStatus.authenticated:
          final user = auth$.user!;
          if (user.mustChangePassword) return at == '/change-password' ? null : '/change-password';
          if (at == '/splash' || at == '/login' || at == '/change-password') return homeFor(user);
          // Role-based areas. Owners and admins may also open the kitchen screen (small
          // restaurants share devices).
          return mayOpen(user, at) ? null : homeFor(user);
      }
    },
    routes: [
      GoRoute(
        path: '/splash',
        builder: (_, _) => const Scaffold(body: Center(child: CircularProgressIndicator())),
      ),
      GoRoute(
        path: '/login',
        builder: (_, _) => LoginPage(rememberedCode: rememberedCode),
      ),
      GoRoute(path: '/change-password', builder: (_, _) => const ChangePasswordPage()),
      GoRoute(
        path: '/kitchen',
        builder: (context, _) => BlocProvider(
          create: (context) => KitchenCubit(
            context.read<KitchenRepository>(),
            context.read<OrdersRepository>(),
            context.read<LiveUpdates>(),
            store: context.read<KitchenBoardStore>(),
            device: context.read<KitchenDevice>(),
            restaurantId: auth.state.user?.restaurantId,
          )..start(),
          child: const KitchenPage(),
        ),
      ),
      GoRoute(
        path: '/sold-out',
        builder: (context, _) => BlocProvider(
          create: (context) =>
              SoldOutCubit(context.read<AvailabilityRepository>(), live: context.read<LiveUpdates>())
                ..start(),
          child: const SoldOutPage(),
        ),
      ),
      GoRoute(
        path: '/manage',
        builder: (_, _) => const ManagePage(),
        routes: [
          GoRoute(
            path: 'pin',
            builder: (context, _) => BlocProvider(
              create: (context) =>
                  PinSettingsCubit(context.read<PinRepository>(), live: context.read<LiveUpdates>())..start(),
              child: const PinSettingsPage(),
            ),
          ),
          GoRoute(
            path: 'staff',
            builder: (context, _) => BlocProvider(
              create: (context) => StaffCubit(context.read<StaffRepository>())..load(),
              child: const StaffPage(),
            ),
          ),
          GoRoute(
            path: 'reports',
            builder: (context, _) => BlocProvider(
              create: (context) => ProblemReportsCubit(context.read<ProblemReportsRepository>())..load(),
              child: const ProblemReportsPage(),
            ),
          ),
        ],
      ),
      StatefulShellRoute.indexedStack(
        // One list of the restaurant's orders for the shell: the Orders tab shows it and the
        // tab's badge counts the cancellation requests waiting for a manager.
        builder: (context, state, shell) => BlocProvider(
          create: (context) =>
              OrdersCubit(context.read<OrdersRepository>(), live: context.read<LiveUpdates>())..start(),
          child: HomeShell(shell: shell),
        ),
        branches: [
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/tables',
                builder: (context, _) => BlocProvider(
                  create: (context) => TablesCubit(
                    context.read<TablesRepository>(),
                    canViewPin: auth.state.user?.can(Perm.pinView) ?? false,
                    live: context.read<LiveUpdates>(),
                  )..start(),
                  child: const TablesPage(),
                ),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [GoRoute(path: '/orders', builder: (_, _) => const OrdersPage())],
          ),
        ],
      ),
      GoRoute(
        path: '/tables/:id',
        builder: (_, state) => TableDetailPage(
          tableId: state.pathParameters['id']!,
          label: state.uri.queryParameters['label'] ?? '',
        ),
      ),
      GoRoute(
        path: '/tables/:id/order',
        builder: (_, state) => ComposeOrderPage(
          tableId: state.pathParameters['id']!,
          label: state.uri.queryParameters['label'] ?? '',
        ),
      ),
    ],
  );
}
