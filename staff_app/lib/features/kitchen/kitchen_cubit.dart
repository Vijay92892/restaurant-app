import 'dart:async';

import 'package:equatable/equatable.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../../core/network/api_exception.dart';
import '../../core/realtime/live_updates.dart';
import '../orders/order_book.dart';
import '../orders/order_models.dart';
import '../orders/orders_repository.dart';
import 'kitchen_device.dart';
import 'kitchen_models.dart';
import 'kitchen_repository.dart';
import 'kitchen_store.dart';

/// "Stop cooking": an order the kitchen had started (or finished) was cancelled.
class KitchenNotice extends Equatable {
  const KitchenNotice({required this.orderId, required this.title, required this.detail});

  final String orderId;
  final String title;
  final String detail;

  @override
  List<Object?> get props => [orderId, title, detail];
}

class KitchenState extends Equatable {
  const KitchenState({
    this.orders = const [],
    this.loading = true,
    this.loadError,
    this.live = LiveStatus.offline,
    this.delayMinutes = 15,
    this.clockOffset = Duration.zero,
    this.now,
    this.syncedAt,
    this.truncated = false,
    this.fromSaved = false,
    this.notices = const [],
    this.soundOn = true,
    this.busy = const {},
    this.message,
  });

  /// Oldest first.
  final List<StaffOrder> orders;
  final bool loading;

  /// Set when the board could not be loaded (the screen keeps showing [orders] meanwhile).
  final String? loadError;
  final LiveStatus live;
  final int delayMinutes;

  /// Server clock minus device clock, so a tablet with a wrong clock still times orders right.
  final Duration clockOffset;

  /// Ticks regularly so waiting times and "delayed" badges stay current.
  final DateTime? now;

  /// When the board was last loaded from the server (device time).
  final DateTime? syncedAt;
  final bool truncated;

  /// Showing the board saved on this device; the server has not confirmed it yet.
  final bool fromSaved;

  /// Cancellations of orders the kitchen had started, until someone taps OK.
  final List<KitchenNotice> notices;
  final bool soundOn;

  /// Orders with an action in flight (their buttons are disabled).
  final Set<String> busy;

  /// One-shot feedback for a snackbar.
  final String? message;

  /// Whether changes can be sent now. Offline, the buttons are paused rather than queued: a
  /// queued "Ready" replayed minutes later could contradict what happened meanwhile (docs/01 §7).
  /// The board must have been confirmed by the server, and the server must be reachable — the
  /// live connection is up, or the last load got through.
  bool get canAct => !fromSaved && (live == LiveStatus.live || loadError == null);

  List<StaffOrder> get newOrders => [
    for (final o in orders)
      if (o.status == OrderStatus.placed) o,
  ];
  List<StaffOrder> get cooking => [
    for (final o in orders)
      if (o.status == OrderStatus.accepted || o.status == OrderStatus.preparing) o,
  ];
  List<StaffOrder> get ready => [
    for (final o in orders)
      if (o.status == OrderStatus.ready) o,
  ];

  /// What to cook right now, summed across tables.
  List<DishGroup> get toCook => aggregateDishes(cooking);

  int get activeCount => orders.length;

  DateTime get serverNow => (now ?? DateTime.now()).add(clockOffset);

  /// How long the guest has been waiting (or, for ready orders, how long it has been waiting
  /// to be picked up).
  Duration waiting(StaffOrder order) {
    final since = order.status == OrderStatus.ready ? (order.readyAt ?? order.updatedAt) : order.createdAt;
    final waited = serverNow.difference(since);
    return waited.isNegative ? Duration.zero : waited;
  }

  /// Derived, never stored: not ready yet and older than the restaurant's threshold.
  bool isDelayed(StaffOrder order) =>
      order.status.inKitchen && serverNow.difference(order.createdAt) > Duration(minutes: delayMinutes);

  KitchenState copyWith({
    List<StaffOrder>? orders,
    bool? loading,
    String? loadError,
    bool clearLoadError = false,
    LiveStatus? live,
    int? delayMinutes,
    Duration? clockOffset,
    DateTime? now,
    DateTime? syncedAt,
    bool? truncated,
    bool? fromSaved,
    List<KitchenNotice>? notices,
    bool? soundOn,
    Set<String>? busy,
    String? message,
  }) => KitchenState(
    orders: orders ?? this.orders,
    loading: loading ?? this.loading,
    loadError: clearLoadError ? null : (loadError ?? this.loadError),
    live: live ?? this.live,
    delayMinutes: delayMinutes ?? this.delayMinutes,
    clockOffset: clockOffset ?? this.clockOffset,
    now: now ?? this.now,
    syncedAt: syncedAt ?? this.syncedAt,
    truncated: truncated ?? this.truncated,
    fromSaved: fromSaved ?? this.fromSaved,
    notices: notices ?? this.notices,
    soundOn: soundOn ?? this.soundOn,
    busy: busy ?? this.busy,
    message: message,
  );

  @override
  List<Object?> get props => [
    orders,
    loading,
    loadError,
    live,
    delayMinutes,
    clockOffset,
    now,
    syncedAt,
    truncated,
    fromSaved,
    notices,
    soundOn,
    busy,
    message,
  ];
}

/// The kitchen display: a board snapshot kept current by live events.
///
/// * On start it shows the board saved on this device (if any) straight away, then loads the
///   server's; the server's snapshot always wins (versions decide, see [OrderBook]).
/// * While the live connection is down the board stays on screen (marked as not live) and is
///   re-fetched every [fallbackPoll]; on every reconnection, and every [reconcileEvery] even
///   while live, it is reloaded in full.
/// * Every change is saved on the device shortly after it happens.
/// * New orders chime (once per [chimeGap], so a rush is one chime, not ten); cancellations of
///   orders already being cooked raise a notice and an alert.
class KitchenCubit extends Cubit<KitchenState> {
  KitchenCubit(
    this._kitchen,
    this._orders,
    this._live, {
    this._store,
    this._device,
    this._restaurantId,
    DateTime Function()? clock,
    this.tick = const Duration(seconds: 20),
    this.fallbackPoll = const Duration(seconds: 20),
    this.reconcileEvery = const Duration(minutes: 2),
    this.saveDelay = const Duration(seconds: 1),
    this.chimeGap = const Duration(seconds: 3),
  }) : _clock = clock ?? DateTime.now,
       super(const KitchenState());

  final KitchenRepository _kitchen;
  final OrdersRepository _orders;
  final LiveUpdates? _live;
  final KitchenBoardStore? _store;
  final KitchenDevice? _device;
  final String? _restaurantId;
  final DateTime Function() _clock;
  final Duration tick;
  final Duration fallbackPoll;
  final Duration reconcileEvery;
  final Duration saveDelay;
  final Duration chimeGap;

  /// Saving on the device needs to know whose board it is.
  KitchenBoardStore? get _saved => _restaurantId == null ? null : _store;

  static bool _keep(StaffOrder order) => order.status.onKitchenBoard;

  /// Statuses in which the kitchen may already have food on the go.
  static const _started = {OrderStatus.accepted, OrderStatus.preparing, OrderStatus.ready};

  OrderBook _book = OrderBook(keep: _keep);

  /// Set once the board has been seen (saved or loaded): only orders appearing after that
  /// are "new" and chime.
  bool _hasBaseline = false;
  DateTime? _lastSync;
  DateTime? _lastChime;
  StreamSubscription<LiveEvent>? _events;
  Timer? _ticker;
  Timer? _poller;
  Timer? _saveTimer;
  Future<void>? _loading;

  Future<void> start() async {
    final device = _device;
    if (device != null) {
      unawaited(device.keepScreenOn(true));
      final soundOn = await device.soundOn();
      if (isClosed) return;
      emit(state.copyWith(soundOn: soundOn, busy: state.busy));
    }
    final live = _live;
    if (live != null) {
      _events = live.events.listen(_onEvent);
      live.status.addListener(_onLiveStatus);
      _onLiveStatus();
    }
    _ticker = Timer.periodic(tick, (_) {
      if (!isClosed) emit(state.copyWith(now: _clock(), busy: state.busy));
    });
    _poller = Timer.periodic(fallbackPoll, (_) {
      final last = _lastSync;
      final due = last == null || _clock().difference(last) >= reconcileEvery;
      if (state.live != LiveStatus.live || due) load();
    });
    await _restore();
    await load();
  }

  /// Shows the board saved on this device until the server answers.
  Future<void> _restore() async {
    final store = _saved;
    if (store == null) return;
    final saved = await store.load(_restaurantId!);
    if (saved == null || isClosed || _hasBaseline) return;
    _book = OrderBook.restore(keep: _keep, orders: saved.orders, tombstones: saved.tombstones);
    _hasBaseline = true;
    emit(
      state.copyWith(
        orders: _book.orders,
        loading: false,
        fromSaved: true,
        delayMinutes: saved.delayMinutes,
        clockOffset: saved.clockOffset,
        now: _clock(),
        syncedAt: saved.syncedAt,
        truncated: saved.truncated,
        busy: state.busy,
      ),
    );
  }

  /// Reloads the board. Concurrent calls share one request.
  Future<void> load() => _loading ??= _load().whenComplete(() => _loading = null);

  Future<void> _load() async {
    final requestedAt = _clock();
    try {
      final board = await _kitchen.board();
      if (isClosed) return;
      final receivedAt = _clock();
      // Estimate the server clock from the middle of the round trip.
      final midpoint = requestedAt.add(receivedAt.difference(requestedAt) ~/ 2);
      final before = _book;
      var book = before.replaceWith(board.orders, board.serverTime);
      for (final (id, version) in board.recentlyCancelled) {
        book = book.forget(id, version) ?? book;
      }
      _book = book;
      final cancelled = {for (final (id, _) in board.recentlyCancelled) id};
      final notices = [
        for (final order in before.orders)
          if (_started.contains(order.status) && book[order.id] == null && cancelled.contains(order.id))
            _noticeFor(order),
      ];
      final arrived =
          _hasBaseline && book.orders.any((o) => o.status == OrderStatus.placed && !before.knows(o.id));
      _hasBaseline = true;
      _lastSync = receivedAt;
      emit(
        state.copyWith(
          orders: _book.orders,
          loading: false,
          clearLoadError: true,
          delayMinutes: board.delayMinutes,
          clockOffset: board.serverTime.difference(midpoint),
          now: receivedAt,
          syncedAt: receivedAt,
          truncated: board.truncated,
          fromSaved: false,
          notices: _withNotices(notices),
          busy: state.busy,
        ),
      );
      _alert(cancelled: notices.isNotEmpty, arrived: arrived);
      _scheduleSave();
    } on ApiException catch (error) {
      if (!isClosed) emit(state.copyWith(loading: false, loadError: error.message, busy: state.busy));
    }
  }

  void _onLiveStatus() {
    final status = _live!.status.value;
    if (!isClosed && status != state.live) emit(state.copyWith(live: status, busy: state.busy));
  }

  void _onEvent(LiveEvent event) {
    if (event.isResync) {
      load();
      return;
    }
    if (event.type == 'order.created' || event.type == 'order.updated') {
      final json = event.payload['order'];
      if (json is! Map<String, dynamic>) return;
      final StaffOrder order;
      try {
        order = StaffOrder.fromJson(json);
      } catch (_) {
        load(); // unexpected shape: fall back to the source of truth
        return;
      }
      _applyOrder(order, created: event.type == 'order.created');
    }
  }

  void _applyOrder(StaffOrder order, {bool created = false, String? message}) {
    final before = _book;
    final next = before.apply(order);
    if (next != null) _book = next;
    if (isClosed) return;
    final previous = before[order.id];
    final notice =
        next != null &&
            order.status == OrderStatus.cancelled &&
            previous != null &&
            _started.contains(previous.status)
        ? _noticeFor(previous)
        : null;
    if (next != null || message != null) {
      emit(
        state.copyWith(
          orders: _book.orders,
          notices: notice == null ? null : _withNotices([notice]),
          busy: state.busy,
          message: message,
        ),
      );
    }
    if (next == null) return;
    _alert(cancelled: notice != null, arrived: created && order.status == OrderStatus.placed);
    _scheduleSave();
  }

  KitchenNotice _noticeFor(StaffOrder order) => KitchenNotice(
    orderId: order.id,
    title: '${order.tableLabel} #${order.orderNumber} was cancelled',
    detail:
        'Stop preparing: ${[for (final line in order.items) '${line.quantity}× ${line.name}'].join(', ')}',
  );

  List<KitchenNotice> _withNotices(List<KitchenNotice> added) {
    if (added.isEmpty) return state.notices;
    final ids = {for (final n in added) n.orderId};
    final all = [...state.notices.where((n) => !ids.contains(n.orderId)), ...added];
    return all.length > 20 ? all.sublist(all.length - 20) : all;
  }

  void dismissNotice(String orderId) {
    emit(state.copyWith(notices: [...state.notices.where((n) => n.orderId != orderId)], busy: state.busy));
  }

  void dismissAllNotices() => emit(state.copyWith(notices: const [], busy: state.busy));

  // --- sound -------------------------------------------------------------------------------------

  void _alert({required bool cancelled, required bool arrived}) {
    final device = _device;
    if (device == null || !state.soundOn || (!cancelled && !arrived)) return;
    final now = _clock();
    final last = _lastChime;
    // A cancellation always sounds; new orders in a rush share one chime.
    if (!cancelled && last != null && now.difference(last) < chimeGap) return;
    _lastChime = now;
    unawaited(device.play(cancelled ? KitchenSound.cancelled : KitchenSound.newOrder));
  }

  /// Turns this tablet's sound on or off (remembered on the device). Turning it on plays the
  /// chime, so staff can check the volume.
  Future<void> toggleSound() async {
    final on = !state.soundOn;
    emit(state.copyWith(soundOn: on, busy: state.busy));
    final device = _device;
    if (device == null) return;
    if (on) unawaited(device.play(KitchenSound.newOrder));
    await device.setSoundOn(on);
  }

  // --- saving on the device ----------------------------------------------------------------------

  void _scheduleSave() {
    if (_saved == null) return;
    _saveTimer?.cancel();
    _saveTimer = Timer(saveDelay, _save);
  }

  Future<void> _save() async {
    final store = _saved;
    if (store == null || !_hasBaseline || isClosed) return;
    await store.save(
      SavedBoard(
        restaurantId: _restaurantId!,
        savedAt: _clock(),
        syncedAt: state.syncedAt,
        clockOffset: state.clockOffset,
        delayMinutes: state.delayMinutes,
        truncated: state.truncated,
        orders: _book.orders,
        tombstones: _book.tombstones,
      ),
    );
  }

  // --- actions -----------------------------------------------------------------------------------

  Future<void> accept(StaffOrder order) =>
      _transition(order, OrderStatus.accepted, 'Order #${order.orderNumber} accepted');

  Future<void> startCooking(StaffOrder order) =>
      _transition(order, OrderStatus.preparing, '${order.tableLabel}: cooking #${order.orderNumber}');

  Future<void> markReady(StaffOrder order) =>
      _transition(order, OrderStatus.ready, '${order.tableLabel} #${order.orderNumber} is ready for pickup');

  Future<void> requestCancellation(StaffOrder order, String reason) => _run(
    order,
    () => _orders.requestCancellation(order, reason),
    'Cancellation requested — a manager will review it',
  );

  Future<void> _transition(StaffOrder order, OrderStatus to, String success) =>
      _run(order, () => _orders.transition(order, to), success, target: to);

  Future<void> _run(
    StaffOrder order,
    Future<StaffOrder> Function() action,
    String success, {
    OrderStatus? target,
  }) async {
    if (state.busy.contains(order.id)) return; // a double tap must not send twice
    if (!state.canAct) {
      emit(state.copyWith(busy: state.busy, message: offlineMessage));
      return;
    }
    emit(state.copyWith(busy: {...state.busy, order.id}));
    try {
      final updated = await action();
      if (isClosed) return;
      _applyOrder(updated, message: success);
    } on ApiException catch (error) {
      if (isClosed) return;
      final conflict = error.code == 'version_conflict' || error.code == 'invalid_transition';
      // Someone else changed it first, or the answer was lost on the way back: show the current
      // truth instead of retrying blindly. Offline, this load fails and pauses the buttons.
      if (conflict || error.isNetwork) await load();
      if (isClosed) return;
      final current = _book[order.id];
      final message = conflict && target != null && current?.status == target
          ? _alreadyDone(order, target) // e.g. our own earlier tap got through after all
          : _explain(error);
      emit(state.copyWith(busy: state.busy, message: message));
    } finally {
      if (!isClosed) emit(state.copyWith(busy: {...state.busy}..remove(order.id)));
    }
  }

  static const offlineMessage = 'No connection — changes are paused until the board is back online.';

  static String _alreadyDone(StaffOrder order, OrderStatus status) => switch (status) {
    OrderStatus.accepted => '${order.tableLabel} #${order.orderNumber} was already accepted',
    OrderStatus.preparing => '${order.tableLabel} #${order.orderNumber} is already cooking',
    _ => '${order.tableLabel} #${order.orderNumber} was already marked ${status.label.toLowerCase()}',
  };

  static String _explain(ApiException error) => switch (error.code) {
    'version_conflict' ||
    'invalid_transition' => 'This order was just changed on another device — showing the latest.',
    'network' => 'No connection — this change may not have gone through. The board will show the latest once it reconnects.',
    _ => error.message,
  };

  @override
  Future<void> close() async {
    _ticker?.cancel();
    _poller?.cancel();
    // A pending save is dropped: on sign-out the saved board must stay deleted.
    _saveTimer?.cancel();
    await _events?.cancel();
    _live?.status.removeListener(_onLiveStatus);
    final device = _device;
    if (device != null) unawaited(device.keepScreenOn(false));
    return super.close();
  }
}
