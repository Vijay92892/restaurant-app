import 'package:equatable/equatable.dart';

/// Limits mirror the server's (which re-checks everything); here they give instant feedback.
const maxLines = 30;
const maxLineQuantity = 50;
const maxTotalQuantity = 100;

class DraftLine extends Equatable {
  const DraftLine({
    required this.itemId,
    required this.name,
    required this.quantity,
    this.modifierIds = const [],
    this.modifierLabels = const [],
    this.notes = '',
  });

  final String itemId;
  final String name;
  final int quantity;
  final List<String> modifierIds;
  final List<String> modifierLabels;
  final String notes;

  /// Same dish + same options + same note ⇒ one line.
  String get key => '$itemId|${([...modifierIds]..sort()).join(',')}|${notes.trim()}';

  DraftLine withQuantity(int value) => DraftLine(
    itemId: itemId,
    name: name,
    quantity: value,
    modifierIds: modifierIds,
    modifierLabels: modifierLabels,
    notes: notes,
  );

  @override
  List<Object?> get props => [key, quantity];
}

enum AddOutcome { added, tooManyLines, tooManyDishes }

/// The order a waiter is building for one table (immutable value; the cubit holds it).
class OrderDraft extends Equatable {
  const OrderDraft({this.lines = const [], this.notes = ''});

  final List<DraftLine> lines;
  final String notes;

  int get totalQuantity => lines.fold(0, (sum, line) => sum + line.quantity);
  bool get isEmpty => lines.isEmpty;

  (OrderDraft, AddOutcome) add(DraftLine line) {
    if (totalQuantity + line.quantity > maxTotalQuantity) return (this, AddOutcome.tooManyDishes);
    final index = lines.indexWhere((l) => l.key == line.key);
    if (index >= 0) {
      final merged = (lines[index].quantity + line.quantity).clamp(1, maxLineQuantity);
      final updated = [...lines]..[index] = lines[index].withQuantity(merged);
      return (OrderDraft(lines: updated, notes: notes), AddOutcome.added);
    }
    if (lines.length >= maxLines) return (this, AddOutcome.tooManyLines);
    return (OrderDraft(lines: [...lines, line], notes: notes), AddOutcome.added);
  }

  OrderDraft setQuantity(String key, int quantity) {
    if (quantity <= 0) return OrderDraft(lines: lines.where((l) => l.key != key).toList(), notes: notes);
    final others = totalQuantity - (lines.where((l) => l.key == key).firstOrNull?.quantity ?? 0);
    final allowed = [quantity, maxLineQuantity, maxTotalQuantity - others].reduce((a, b) => a < b ? a : b);
    return OrderDraft(
      lines: [for (final l in lines) l.key == key ? l.withQuantity(allowed) : l],
      notes: notes,
    );
  }

  OrderDraft withNotes(String value) => OrderDraft(lines: lines, notes: value);

  OrderDraft without(Set<String> itemIds) =>
      OrderDraft(lines: lines.where((l) => !itemIds.contains(l.itemId)).toList(), notes: notes);

  /// Request body for `POST /orders` (ids and quantities only — never prices).
  Map<String, Object> toRequest(String tableId) => {
    'table_id': tableId,
    'items': [
      for (final line in lines)
        {
          'menu_item_id': line.itemId,
          'quantity': line.quantity,
          'modifier_ids': line.modifierIds,
          if (line.notes.trim().isNotEmpty) 'notes': line.notes.trim(),
        },
    ],
    if (notes.trim().isNotEmpty) 'notes': notes.trim(),
  };

  @override
  List<Object?> get props => [lines, notes];
}
