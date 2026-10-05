import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:staff_app/features/kitchen/kitchen_store.dart';
import 'package:staff_app/features/orders/order_models.dart';

import 'helpers.dart';

void main() {
  late Directory dir;
  late DateTime now;
  late FileKitchenBoardStore store;

  File file() => File('${dir.path}${Platform.pathSeparator}kitchen_board.json');

  setUp(() async {
    dir = await Directory.systemTemp.createTemp('kitchen_store_test');
    now = DateTime(2026, 10, 4, 20);
    store = FileKitchenBoardStore(directory: () async => dir, clock: () => now);
  });

  tearDown(() => dir.delete(recursive: true));

  SavedBoard board({String restaurantId = 'r1', DateTime? savedAt}) => SavedBoard(
    restaurantId: restaurantId,
    savedAt: savedAt ?? now,
    syncedAt: now.subtract(const Duration(minutes: 3)),
    clockOffset: const Duration(seconds: -42),
    delayMinutes: 20,
    truncated: false,
    orders: sampleRush(now),
    tombstones: const {'gone-1': 7},
  );

  test('saves the board and reads it back exactly', () async {
    await store.save(board());
    final loaded = (await store.load('r1'))!;
    expect(loaded.orders, sampleRush(now));
    expect(
      [for (final o in loaded.orders) o.status],
      [OrderStatus.preparing, OrderStatus.accepted, OrderStatus.placed, OrderStatus.ready],
    );
    expect(loaded.orders[1].items.single.notes, 'no onion');
    expect(loaded.tombstones, {'gone-1': 7});
    expect(loaded.clockOffset, const Duration(seconds: -42));
    expect(loaded.delayMinutes, 20);
    expect(loaded.syncedAt, now.subtract(const Duration(minutes: 3)));
    expect(dir.listSync().map((f) => f.uri.pathSegments.last), ['kitchen_board.json']); // no temp file left
  });

  test('another restaurant’s board is never shown', () async {
    await store.save(board(restaurantId: 'r2'));
    expect(await store.load('r1'), isNull);
  });

  test('a board older than 12 hours (or from a clock that jumped) is discarded', () async {
    await store.save(board(savedAt: now.subtract(const Duration(hours: 13))));
    expect(await store.load('r1'), isNull);
    expect(file().existsSync(), isFalse);

    await store.save(board(savedAt: now.add(const Duration(days: 2))));
    expect(await store.load('r1'), isNull);
  });

  test('an unreadable file is discarded instead of crashing the kitchen screen', () async {
    file().writeAsStringSync('{"format": 1, "orders": [tru');
    expect(await store.load('r1'), isNull);
    expect(file().existsSync(), isFalse);

    file().writeAsStringSync('{"format": 99}');
    expect(await store.load('r1'), isNull);
  });

  test('signing out deletes it, and a late save from a closing screen cannot bring it back', () async {
    await store.save(board());
    await store.clear();
    expect(file().existsSync(), isFalse);
    await store.save(board()); // e.g. a timer that fired during sign-out
    expect(file().existsSync(), isFalse);

    expect(await store.load('r1'), isNull); // the next sign-in opens it again
    await store.save(board());
    expect(await store.load('r1'), isNotNull);
  });

  test('saving never throws, even when the folder is gone', () async {
    final missing = FileKitchenBoardStore(directory: () async => Directory('${dir.path}/missing/deeper'));
    await missing.save(board());
    expect(await missing.load('r1'), isNull);
  });
}
