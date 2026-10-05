import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';

import 'core/auth/auth_bloc.dart';
import 'core/network/api_client.dart';
import 'core/realtime/live_updates.dart';
import 'core/realtime/realtime_client.dart';
import 'core/routing/app_router.dart';
import 'core/theme/app_theme.dart';
import 'features/kitchen/kitchen_device.dart';
import 'features/kitchen/kitchen_repository.dart';
import 'features/kitchen/kitchen_store.dart';
import 'features/manage/manage_repositories.dart';
import 'features/menu/availability.dart';
import 'features/menu/menu_repository.dart';
import 'features/orders/orders_repository.dart';
import 'features/problems/problem_reports.dart';
import 'features/tables/tables_repository.dart';

/// Wires repositories (data), the auth BLoC (session) and live updates above the router.
class StaffApp extends StatefulWidget {
  const StaffApp({
    super.key,
    required this.api,
    this.rememberedCode,
    this.realtime,
    this.boardStore,
    this.kitchenDevice,
  });

  final ApiClient api;
  final String? rememberedCode;

  /// Injected in tests; otherwise one connection per signed-in device.
  final RealtimeClient? realtime;

  /// Injected in tests; otherwise the kitchen board is saved in the app's private files.
  final KitchenBoardStore? boardStore;
  final KitchenDevice? kitchenDevice;

  @override
  State<StaffApp> createState() => _StaffAppState();
}

class _StaffAppState extends State<StaffApp> with WidgetsBindingObserver {
  late final AuthBloc _auth = AuthBloc(widget.api)..add(const AuthStarted());
  late final GoRouter _router = buildRouter(_auth, rememberedCode: widget.rememberedCode);
  late final RealtimeClient _realtime = widget.realtime ?? RealtimeClient(api: widget.api);
  late final KitchenBoardStore _boardStore = widget.boardStore ?? FileKitchenBoardStore();
  late final KitchenDevice _kitchenDevice = widget.kitchenDevice ?? PluginKitchenDevice();
  late final StreamSubscription<AuthState> _authChanges;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _authChanges = _auth.stream.listen(_followAuth);
    _followAuth(_auth.state);
  }

  /// Live updates run only for a signed-in user who has finished any forced password change.
  /// Signing out (or the session ending) also deletes the kitchen board saved on the device.
  void _followAuth(AuthState state) {
    final user = state.user;
    if (state.status == AuthStatus.authenticated && user != null && !user.mustChangePassword) {
      _realtime.start();
    } else {
      unawaited(_realtime.stop());
    }
    if (state.status == AuthStatus.unauthenticated) unawaited(_boardStore.clear());
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Coming back to the foreground: reconnect now instead of waiting out a backoff.
    if (state == AppLifecycleState.resumed) _realtime.reconnectNow();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _authChanges.cancel();
    unawaited(_realtime.dispose());
    _router.dispose();
    _auth.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MultiRepositoryProvider(
      providers: [
        RepositoryProvider.value(value: widget.api),
        RepositoryProvider<LiveUpdates>.value(value: _realtime),
        RepositoryProvider(create: (_) => TablesRepository(widget.api)),
        RepositoryProvider(create: (_) => MenuRepository(widget.api)),
        RepositoryProvider(create: (_) => OrdersRepository(widget.api)),
        RepositoryProvider(create: (_) => KitchenRepository(widget.api)),
        RepositoryProvider(create: (_) => AvailabilityRepository(widget.api)),
        RepositoryProvider(create: (_) => PinRepository(widget.api)),
        RepositoryProvider(create: (_) => StaffRepository(widget.api)),
        RepositoryProvider(create: (_) => ProblemReportsRepository(widget.api)),
        RepositoryProvider.value(value: _boardStore),
        RepositoryProvider.value(value: _kitchenDevice),
      ],
      child: BlocProvider.value(
        value: _auth,
        child: MaterialApp.router(
          title: 'Restaurant Staff',
          debugShowCheckedModeBanner: false,
          theme: AppTheme.light(),
          darkTheme: AppTheme.dark(),
          routerConfig: _router,
        ),
      ),
    );
  }
}
