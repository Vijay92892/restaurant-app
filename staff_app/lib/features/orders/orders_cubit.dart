import 'dart:async';

import 'package:equatable/equatable.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../../core/network/api_exception.dart';
import '../../core/realtime/live_updates.dart';
import 'order_book.dart';
import 'order_models.dart';
import 'orders_repository.dart';

class OrdersState extends Equatable {
  const OrdersState({this.orders = const [], this.loading = true, this.busyOrderId, this.message});

  final List<StaffOrder> orders;
  final bool loading;

  /// The order whose action is in flight (its buttons are disabled meanwhile).
  final String? busyOrderId;

  /// One-shot feedback for a snackbar.
  final String? message;

  List<StaffOrder> withStatus(Set<OrderStatus> statuses) =>
      orders.where((o) => statuses.contains(o.status)).toList();

  /// Cancellation requests waiting for a manager.
  List<StaffOrder> get awaitingDecision =>
      orders.where((o) => o.cancelRequested && o.status.isActive).toList();

  OrdersState copyWith({List<StaffOrder>? orders, bool? loading, String? busyOrderId, String? message}) =>
      OrdersState(
        orders: orders ?? this.orders,
        loading: loading ?? this.loading,
        busyOrderId: busyOrderId,
        message: message,
      );

  @override
  List<Object?> get props => [orders, loading, busyOrderId, message];
}

/// Active orders — for the whole restaurant or, with [tableId], for one table.
///
/// Live events keep the list current; it is also re-fetched every [pollInterval] while the live
/// connection is down (and every [livePollInterval] as a safety net while it is up).
class OrdersCubit extends Cubit<OrdersState> {
  OrdersCubit(
    this._repository, {
    this.tableId,
    this._live,
    this.pollInterval = const Duration(seconds: 15),
    this.livePollInterval = const Duration(minutes: 2),
  }) : super(const OrdersState());

  final OrdersRepository _repository;
  final String? tableId;
  final LiveUpdates? _live;
  final Duration pollInterval;
  final Duration livePollInterval;
  late OrderBook _book = OrderBook(
    keep: (o) => o.status.isActive && (tableId == null || o.tableId == tableId),
  );
  Timer? _timer;
  StreamSubscription<LiveEvent>? _events;
  DateTime _lastFetch = DateTime.fromMillisecondsSinceEpoch(0);
  Future<void>? _refreshing;

  Future<void> start() async {
    _events = _live?.events.listen(_onEvent);
    await refresh();
    _timer ??= Timer.periodic(pollInterval, (_) {
      final live = _live?.status.value == LiveStatus.live;
      if (!live || DateTime.now().difference(_lastFetch) >= livePollInterval) refresh();
    });
  }

  /// Re-fetches the list. Concurrent calls share one request.
  Future<void> refresh() => _refreshing ??= _refresh().whenComplete(() => _refreshing = null);

  Future<void> _refresh() async {
    try {
      final requestedAt = _lastFetch = DateTime.now();
      final orders = await _repository.orders(tableId: tableId);
      if (isClosed) return;
      // The list was read on the server after the request left; an order that a live event
      // changed after that moment (in server time, with margin) and that the list lacks is
      // newer than the list, so it is kept.
      final asOf = requestedAt.add(_repository.serverClockOffset).subtract(const Duration(seconds: 2));
      _book = _book.replaceWith(orders, asOf);
      emit(
        state.copyWith(orders: _newestFirst(_book.orders), loading: false, busyOrderId: state.busyOrderId),
      );
    } on ApiException catch (error) {
      if (!isClosed) {
        emit(state.copyWith(loading: false, message: error.message, busyOrderId: state.busyOrderId));
      }
    }
  }

  void _onEvent(LiveEvent event) {
    if (event.isResync || event.type == 'table.closed') {
      refresh();
      return;
    }
    if (event.type != 'order.created' && event.type != 'order.updated') return;
    final json = event.payload['order'];
    if (json is! Map<String, dynamic>) return;
    try {
      _apply(StaffOrder.fromJson(json));
    } catch (_) {
      refresh();
    }
  }

  void _apply(StaffOrder order) {
    final next = _book.apply(order);
    if (next == null || isClosed) return;
    _book = next;
    emit(state.copyWith(orders: _newestFirst(_book.orders), busyOrderId: state.busyOrderId));
  }

  static List<StaffOrder> _newestFirst(List<StaffOrder> oldestFirst) => oldestFirst.reversed.toList();

  Future<void> markServed(StaffOrder order) => _act(
    order,
    () => _repository.transition(order, OrderStatus.served),
    'Order #${order.orderNumber} served',
  );

  Future<void> markCompleted(StaffOrder order) => _act(
    order,
    () => _repository.transition(order, OrderStatus.completed),
    'Order #${order.orderNumber} completed',
  );

  Future<void> cancel(StaffOrder order, {required String reasonCode, String? reason}) => _act(
    order,
    () => _repository.cancel(order, reasonCode: reasonCode, reason: reason),
    'Order #${order.orderNumber} cancelled',
  );

  Future<void> requestCancellation(StaffOrder order, String reason) => _act(
    order,
    () => _repository.requestCancellation(order, reason),
    'Cancellation requested — a manager will review it',
  );

  Future<void> approveCancellation(StaffOrder order) => _act(
    order,
    () => _repository.reviewCancellation(order, approve: true),
    'Order #${order.orderNumber} cancelled',
  );

  Future<void> rejectCancellation(StaffOrder order) => _act(
    order,
    () => _repository.reviewCancellation(order, approve: false),
    'Order #${order.orderNumber} stays — the kitchen carries on',
  );

  Future<void> _act(StaffOrder order, Future<StaffOrder> Function() action, String success) async {
    emit(state.copyWith(busyOrderId: order.id));
    try {
      final updated = await action();
      if (isClosed) return;
      _book = _book.apply(updated) ?? _book;
      emit(state.copyWith(orders: _newestFirst(_book.orders), message: success)); // and not busy
    } on ApiException catch (error) {
      if (isClosed) return;
      // A stale order (someone else changed it) is refreshed rather than retried blindly.
      emit(state.copyWith(message: error.message));
      if (error.isConflict || error.statusCode == 404) await refresh();
    }
  }

  @override
  Future<void> close() async {
    _timer?.cancel();
    await _events?.cancel();
    return super.close();
  }
}
