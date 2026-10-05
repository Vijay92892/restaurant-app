import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:staff_app/features/menu/menu_models.dart';
import 'package:staff_app/features/orders/order_draft.dart';

DraftLine line(String id, {int quantity = 1, List<String> mods = const [], String notes = ''}) =>
    DraftLine(itemId: id, name: 'Dish $id', quantity: quantity, modifierIds: mods, notes: notes);

OrderDraft build(List<DraftLine> lines) {
  var draft = const OrderDraft();
  for (final l in lines) {
    final (next, outcome) = draft.add(l);
    expect(outcome, AddOutcome.added);
    draft = next;
  }
  return draft;
}

void main() {
  group('OrderDraft', () {
    test('identical dish + options + note merge; different ones stay separate', () {
      final draft = build([
        line('a', mods: ['x', 'y']),
        line('a', quantity: 2, mods: ['y', 'x']),
        line('a', mods: ['x']),
        line('a', mods: ['x'], notes: 'no onion'),
      ]);
      expect(draft.lines.length, 3);
      expect(draft.lines.first.quantity, 3);
      expect(draft.totalQuantity, 5);
    });

    test('limits: 50 per line, 100 dishes, 30 lines', () {
      var draft = build([line('a', quantity: 40), line('a', quantity: 30)]);
      expect(draft.lines.single.quantity, 50);
      draft = build([line('a', quantity: 50), line('b', quantity: 50)]);
      expect(draft.add(line('c')).$2, AddOutcome.tooManyDishes);
      final wide = build([for (var i = 0; i < maxLines; i++) line('i$i')]);
      expect(wide.add(line('extra')).$2, AddOutcome.tooManyLines);
    });

    test('setQuantity clamps to the order total and removes at zero', () {
      var draft = build([line('a', quantity: 2), line('b', quantity: 90)]);
      final key = draft.lines.first.key;
      draft = draft.setQuantity(key, 99);
      expect(draft.lines.first.quantity, 10);
      draft = draft.setQuantity(key, 0);
      expect(draft.lines.map((l) => l.itemId), ['b']);
    });

    test('request carries ids and quantities only — no prices or totals', () {
      final draft = build([
        line('a', quantity: 2, mods: ['x'], notes: ' less oil '),
        line('b'),
      ]).withNotes(' VIP ');
      final request = draft.toRequest('table-1');
      expect(request, {
        'table_id': 'table-1',
        'items': [
          {
            'menu_item_id': 'a',
            'quantity': 2,
            'modifier_ids': ['x'],
            'notes': 'less oil',
          },
          {'menu_item_id': 'b', 'quantity': 1, 'modifier_ids': <String>[]},
        ],
        'notes': 'VIP',
      });
      expect(jsonEncode(request), isNot(matches(RegExp('price|total|amount', caseSensitive: false))));
    });

    test('without() drops every line of the given dishes', () {
      final draft = build([
        line('a', mods: ['x']),
        line('a', mods: ['y']),
        line('b'),
      ]);
      expect(draft.without({'a'}).lines.map((l) => l.itemId), ['b']);
    });
  });

  group('MenuGroup rules', () {
    MenuGroup group(int min, int max, {Set<String> off = const {}}) => MenuGroup(
      id: 'g',
      name: 'Spice level',
      minSelect: min,
      maxSelect: max,
      options: [
        for (final id in ['a', 'b', 'c']) MenuOption(id: id, name: id, isAvailable: !off.contains(id)),
      ],
    );

    test('single choice behaves like radio buttons', () {
      final g = group(1, 1);
      expect(g.toggle([], 'a'), ['a']);
      expect(g.toggle(['a'], 'b'), ['b']);
      expect(g.toggle(['a'], 'a'), ['a']);
      expect(group(0, 1).toggle(['a'], 'a'), <String>[]);
    });

    test('multi choice is capped and skips unavailable options', () {
      final g = group(0, 2, off: {'c'});
      expect(g.toggle(['a', 'b'], 'c'), ['a', 'b']);
      expect(g.toggle(['a'], 'c'), ['a']);
      expect(g.toggle(['a', 'b'], 'a'), ['b']);
    });

    test('rules and errors', () {
      expect(group(1, 1).rule, 'Choose 1');
      expect(group(0, 3).rule, 'Optional · up to 3');
      expect(group(1, 1).errorFor([]), 'Choose a spice level');
      expect(group(2, 3).errorFor(['a']), 'Choose at least 2');
      expect(group(1, 1).errorFor(['a']), isNull);
    });
  });
}
