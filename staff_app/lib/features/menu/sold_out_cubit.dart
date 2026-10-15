import 'dart:async';

import 'package:equatable/equatable.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../../core/network/api_exception.dart';
import '../../core/realtime/live_updates.dart';
import 'availability.dart';

class SoldOutState extends Equatable {
  const SoldOutState({
    this.menu,
    this.loading = true,
    this.busy = const {},
    this.query = '',
    this.onlySoldOut = false,
    this.message,
    this.error,
  });

  final AvailabilityMenu? menu;
  final bool loading;

  /// Dishes/options whose switch is waiting for the server.
  final Set<String> busy;
  final String query;
  final bool onlySoldOut;

  /// One-shot feedback for a snackbar.
  final String? message;

  /// The menu could not be loaded.
  final String? error;

  List<Switchable> visible(List<Switchable> all) {
    final q = query.trim().toLowerCase();
    return [
      for (final s in all)
        if ((!onlySoldOut || s.isOff) &&
            (q.isEmpty || s.name.toLowerCase().contains(q) || s.section.toLowerCase().contains(q)))
          s,
    ];
  }

  SoldOutState copyWith({
    AvailabilityMenu? menu,
    bool? loading,
    Set<String>? busy,
    String? query,
    bool? onlySoldOut,
    String? message,
    String? error,
  }) => SoldOutState(
    menu: menu ?? this.menu,
    loading: loading ?? this.loading,
    busy: busy ?? this.busy,
    query: query ?? this.query,
    onlySoldOut: onlySoldOut ?? this.onlySoldOut,
    message: message,
    error: error,
  );

  @override
  List<Object?> get props => [menu, loading, busy, query, onlySoldOut, message, error];
}

/// Sold-out switches for the kitchen and managers; managers can also hide a dish from the
/// menu ([canHide]). Every switch waits for the server (no optimistic flip): a dish shown as
/// available must really be orderable.
class SoldOutCubit extends Cubit<SoldOutState> {
  SoldOutCubit(this._repository, {this._live, this.canHide = false}) : super(const SoldOutState());

  final AvailabilityRepository _repository;
  final LiveUpdates? _live;

  /// The user may take dishes off the menu (`menu.manage`: managers and the owner).
  final bool canHide;
  StreamSubscription<LiveEvent>? _events;

  Future<void> start() async {
    // Another device switched something (or the connection came back): reload.
    _events = _live?.events.listen((event) {
      if (event.isResync || event.type == 'menu.availability_changed') refresh();
    });
    await refresh();
  }

  Future<void> refresh() async {
    try {
      final menu = await _repository.load(includeHidden: canHide);
      if (!isClosed) emit(state.copyWith(menu: menu, loading: false));
    } on ApiException catch (error) {
      if (!isClosed) emit(state.copyWith(loading: false, error: error.message));
    }
  }

  void search(String query) => emit(state.copyWith(query: query));

  void showOnlySoldOut(bool only) => emit(state.copyWith(onlySoldOut: only));

  Future<void> setDish(Switchable dish, {required bool available}) =>
      _set(dish, available, () => _repository.setDish(dish.id, available: available));

  /// Managers: available, sold out or hidden. Availability changes before visibility, so a
  /// dish brought back as sold out is never orderable in between.
  Future<void> setDishState(Switchable dish, DishState target) async {
    if (state.busy.contains(dish.id) || target == dish.dishState) return;
    emit(state.copyWith(busy: {...state.busy, dish.id}));
    try {
      var now = dish;
      if (target == DishState.hidden) {
        now = now.withHidden(!await _repository.setDishShown(dish.id, shown: false));
      } else {
        final available = target == DishState.available;
        if (now.isAvailable != available) {
          now = now.withAvailable(await _repository.setDish(dish.id, available: available));
        }
        if (now.isHidden) now = now.withHidden(!await _repository.setDishShown(dish.id, shown: true));
      }
      if (isClosed) return;
      emit(
        state.copyWith(
          menu: state.menu?.replace(now),
          busy: {...state.busy}..remove(dish.id),
          message: switch (now.dishState) {
            DishState.available => '${dish.name} is available again',
            DishState.soldOut => '${dish.name} is now SOLD OUT',
            DishState.hidden => '${dish.name} is hidden from the menu',
          },
        ),
      );
    } on ApiException catch (error) {
      if (isClosed) return;
      emit(state.copyWith(busy: {...state.busy}..remove(dish.id), message: error.message));
      await refresh();
    }
  }

  Future<void> setOption(Switchable option, {required bool available}) =>
      _set(option, available, () => _repository.setOption(option.id, available: available));

  Future<void> _set(Switchable target, bool available, Future<bool> Function() call) async {
    if (state.busy.contains(target.id)) return;
    emit(state.copyWith(busy: {...state.busy, target.id}));
    try {
      final now = await call();
      if (isClosed) return;
      final menu = state.menu?.replace(target.withAvailable(now));
      emit(
        state.copyWith(
          menu: menu,
          busy: {...state.busy}..remove(target.id),
          message: now ? '${target.name} is available again' : '${target.name} is now SOLD OUT',
        ),
      );
    } on ApiException catch (error) {
      if (isClosed) return;
      emit(state.copyWith(busy: {...state.busy}..remove(target.id), message: error.message));
      await refresh();
    }
  }

  @override
  Future<void> close() async {
    await _events?.cancel();
    return super.close();
  }
}
