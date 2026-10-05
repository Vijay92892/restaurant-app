import 'package:equatable/equatable.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:uuid/uuid.dart';

import '../../core/network/api_exception.dart';
import '../menu/menu_models.dart';
import '../menu/menu_repository.dart';
import 'order_draft.dart';
import 'order_models.dart';
import 'orders_repository.dart';

enum ComposeStatus { loading, ready, sending, sent, failed }

class ComposeState extends Equatable {
  const ComposeState({
    this.status = ComposeStatus.loading,
    this.menu,
    this.draft = const OrderDraft(),
    this.message,
    this.placed,
  });

  final ComposeStatus status;
  final OrderingMenu? menu;
  final OrderDraft draft;
  final String? message;
  final StaffOrder? placed;

  ComposeState copyWith({
    ComposeStatus? status,
    OrderingMenu? menu,
    OrderDraft? draft,
    String? message,
    StaffOrder? placed,
  }) => ComposeState(
    status: status ?? this.status,
    menu: menu ?? this.menu,
    draft: draft ?? this.draft,
    message: message,
    placed: placed ?? this.placed,
  );

  @override
  List<Object?> get props => [status, menu, draft, message, placed];
}

/// Builds an order for one table and sends it to the kitchen.
class ComposeOrderCubit extends Cubit<ComposeState> {
  ComposeOrderCubit({
    required this.tableId,
    required this._menu,
    required this._orders,
    this._uuid = const Uuid(),
  }) : super(const ComposeState());

  final String tableId;
  final MenuRepository _menu;
  final OrdersRepository _orders;
  final Uuid _uuid;

  /// Idempotency key for the current draft: reused on retry, renewed when the draft changes.
  String? _pendingKey;
  OrderDraft? _pendingDraft;

  Future<void> load() async {
    try {
      emit(state.copyWith(status: ComposeStatus.ready, menu: await _menu.orderingMenu()));
    } on ApiException catch (error) {
      emit(state.copyWith(status: ComposeStatus.failed, message: error.message));
    }
  }

  bool addItem(MenuItem item, {int quantity = 1, List<MenuOption> options = const [], String notes = ''}) {
    final menu = state.menu;
    final labels = <String>[];
    if (menu != null) {
      for (final group in menu.groupsFor(item)) {
        for (final option in options.where((o) => group.options.contains(o))) {
          labels.add('${group.name}: ${option.name}');
        }
      }
    }
    final (draft, outcome) = state.draft.add(
      DraftLine(
        itemId: item.id,
        name: item.name,
        quantity: quantity,
        modifierIds: [for (final o in options) o.id],
        modifierLabels: labels,
        notes: notes,
      ),
    );
    if (outcome != AddOutcome.added) {
      emit(state.copyWith(message: 'This order is full. Send it, then start another.'));
      return false;
    }
    emit(
      state.copyWith(
        draft: draft,
        message: quantity == 1 ? 'Added ${item.name}' : 'Added $quantity × ${item.name}',
      ),
    );
    return true;
  }

  void setQuantity(String key, int quantity) =>
      emit(state.copyWith(draft: state.draft.setQuantity(key, quantity)));

  void setNotes(String notes) => emit(state.copyWith(draft: state.draft.withNotes(notes)));

  Future<void> send() async {
    if (state.status == ComposeStatus.sending || state.draft.isEmpty) return;
    if (_pendingDraft != state.draft) {
      _pendingKey = _uuid.v4().replaceAll('-', '');
      _pendingDraft = state.draft;
    }
    emit(state.copyWith(status: ComposeStatus.sending));
    try {
      final order = await _orders.place(tableId: tableId, draft: state.draft, idempotencyKey: _pendingKey!);
      _pendingKey = null;
      _pendingDraft = null;
      emit(state.copyWith(status: ComposeStatus.sent, placed: order, draft: const OrderDraft()));
    } on ApiException catch (error) {
      if (error.isNetwork) {
        emit(
          state.copyWith(
            status: ComposeStatus.ready,
            message: 'No connection — not sent yet. Tap Send again; it will only go to the kitchen once.',
          ),
        );
        return;
      }
      var draft = state.draft;
      final soldOut = error.details['menu_item_id'];
      if (error.statusCode == 409 && soldOut is String) {
        draft = draft.without({soldOut}); // remove the dish that just sold out
        await load();
      }
      emit(state.copyWith(status: ComposeStatus.ready, draft: draft, message: error.message));
    }
  }
}
