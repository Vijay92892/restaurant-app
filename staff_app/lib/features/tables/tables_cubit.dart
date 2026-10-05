import 'dart:async';

import 'package:equatable/equatable.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../../core/network/api_exception.dart';
import '../../core/realtime/live_updates.dart';
import 'table_models.dart';
import 'tables_repository.dart';

class TablesState extends Equatable {
  const TablesState({this.tables = const [], this.pin, this.loading = true, this.error, this.alert});

  final List<TableSummary> tables;
  final PinInfo? pin;
  final bool loading;
  final String? error;

  /// One-shot security notice (e.g. someone is guessing the table PIN).
  final String? alert;

  TablesState copyWith({
    List<TableSummary>? tables,
    PinInfo? pin,
    bool? loading,
    String? error,
    String? alert,
  }) => TablesState(
    tables: tables ?? this.tables,
    pin: pin ?? this.pin,
    loading: loading ?? this.loading,
    error: error,
    alert: alert,
  );

  @override
  List<Object?> get props => [tables, pin, loading, error, alert];
}

/// Table overview. Live events trigger a (debounced) refresh; it also polls every
/// [pollInterval] while the live connection is down.
class TablesCubit extends Cubit<TablesState> {
  TablesCubit(
    this._repository, {
    required this.canViewPin,
    this._live,
    this.pollInterval = const Duration(seconds: 15),
    this.livePollInterval = const Duration(minutes: 2),
    this.debounce = const Duration(milliseconds: 400),
  }) : super(const TablesState());

  final TablesRepository _repository;
  final bool canViewPin;
  final LiveUpdates? _live;
  final Duration pollInterval;
  final Duration livePollInterval;
  final Duration debounce;
  Timer? _timer;
  Timer? _pending;
  StreamSubscription<LiveEvent>? _events;
  DateTime _lastFetch = DateTime.fromMillisecondsSinceEpoch(0);
  Future<void>? _refreshing;

  static const _refreshOn = {'resync', 'order.created', 'order.updated', 'table.closed', 'pin.rotated'};

  Future<void> start() async {
    _events = _live?.events.listen(_onEvent);
    await refresh();
    _timer ??= Timer.periodic(pollInterval, (_) {
      final live = _live?.status.value == LiveStatus.live;
      if (!live || DateTime.now().difference(_lastFetch) >= livePollInterval) refresh();
    });
  }

  void _onEvent(LiveEvent event) {
    if (_refreshOn.contains(event.type)) {
      // A burst of events (a big order moving through the kitchen) becomes one request.
      _pending?.cancel();
      _pending = Timer(debounce, refresh);
    } else if (event.type == 'pin.bruteforce_suspected' && canViewPin && !isClosed) {
      final tableId = event.payload['table_id'];
      final label = [
        for (final t in state.tables)
          if (t.id == tableId) t.label,
      ].firstOrNull;
      emit(
        state.copyWith(
          alert:
              'Many wrong PIN attempts${label == null ? '' : ' at $label'}. '
              'If guests aren’t struggling to type it, ask a manager to change the PIN.',
        ),
      );
    }
  }

  /// Re-fetches tables (and the PIN). Concurrent calls share one request.
  Future<void> refresh() => _refreshing ??= _refresh().whenComplete(() => _refreshing = null);

  Future<void> _refresh() async {
    try {
      _lastFetch = DateTime.now();
      final tables = await _repository.tables();
      final pin = canViewPin ? await _repository.pin() : null;
      if (!isClosed) emit(state.copyWith(tables: tables, pin: pin, loading: false));
    } on ApiException catch (error) {
      if (!isClosed) emit(state.copyWith(loading: false, error: error.message));
    }
  }

  Future<CloseTableResult?> closeTable(String tableId) async {
    try {
      final result = await _repository.closeTable(tableId);
      await refresh();
      return result;
    } on ApiException catch (error) {
      emit(state.copyWith(error: error.message));
      return null;
    }
  }

  @override
  Future<void> close() async {
    _timer?.cancel();
    _pending?.cancel();
    await _events?.cancel();
    return super.close();
  }
}
