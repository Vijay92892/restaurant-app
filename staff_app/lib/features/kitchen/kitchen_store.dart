import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import '../orders/order_models.dart';

/// The kitchen board as this device last knew it, saved so a tablet that restarts (or whose app
/// is killed) during a network outage still shows the orders it had (spec §36).
///
/// It holds what the screen showed — orders with their items and versions, and the versions of
/// orders that left — and nothing secret. It belongs to one restaurant, is ignored once older
/// than [maxAge], and is deleted when the user signs out.
class SavedBoard {
  const SavedBoard({
    required this.restaurantId,
    required this.savedAt,
    required this.syncedAt,
    required this.clockOffset,
    required this.delayMinutes,
    required this.truncated,
    required this.orders,
    required this.tombstones,
  });

  static const format = 1;
  static const maxAge = Duration(hours: 12);

  final String restaurantId;

  /// Device time of the save.
  final DateTime savedAt;

  /// Device time of the last successful load from the server.
  final DateTime? syncedAt;

  /// Server clock minus device clock, as last measured.
  final Duration clockOffset;
  final int delayMinutes;
  final bool truncated;
  final List<StaffOrder> orders;

  /// Versions of orders that left the board (see `OrderBook`).
  final Map<String, int> tombstones;

  Map<String, dynamic> toJson() => {
    'format': format,
    'restaurant_id': restaurantId,
    'saved_at': savedAt.toUtc().toIso8601String(),
    'synced_at': syncedAt?.toUtc().toIso8601String(),
    'clock_offset_ms': clockOffset.inMilliseconds,
    'delay_minutes': delayMinutes,
    'truncated': truncated,
    'orders': [for (final order in orders) order.toJson()],
    'tombstones': tombstones,
  };

  factory SavedBoard.fromJson(Map<String, dynamic> json) {
    if (json['format'] != format) throw const FormatException('unknown saved board format');
    return SavedBoard(
      restaurantId: json['restaurant_id'] as String,
      savedAt: DateTime.parse(json['saved_at'] as String).toLocal(),
      syncedAt: switch (json['synced_at']) {
        final String at => DateTime.parse(at).toLocal(),
        _ => null,
      },
      clockOffset: Duration(milliseconds: json['clock_offset_ms'] as int),
      delayMinutes: json['delay_minutes'] as int,
      truncated: json['truncated'] as bool,
      orders: [
        for (final o in json['orders'] as List<dynamic>) StaffOrder.fromJson(o as Map<String, dynamic>),
      ],
      tombstones: {
        for (final MapEntry(:key, :value) in (json['tombstones'] as Map<String, dynamic>).entries)
          key: value as int,
      },
    );
  }
}

abstract interface class KitchenBoardStore {
  /// The board saved for [restaurantId]; null if there is none, it belongs to another
  /// restaurant, it is too old, or it cannot be read.
  Future<SavedBoard?> load(String restaurantId);
  Future<void> save(SavedBoard board);

  /// Deletes the saved board (sign-out). Later saves are ignored until the next [load], so a
  /// late save from a closing screen cannot bring a signed-out board back.
  Future<void> clear();
}

/// Keeps the board in one JSON file in the app's private storage (never shared storage;
/// Android backups of app data are disabled in the manifest). Writes go to a temporary file
/// that then replaces the old one, so a crash mid-write never leaves a half-written board.
class FileKitchenBoardStore implements KitchenBoardStore {
  FileKitchenBoardStore({Future<Directory> Function()? directory, DateTime Function()? clock})
    : _directory = directory ?? getApplicationSupportDirectory,
      _clock = clock ?? DateTime.now;

  final Future<Directory> Function() _directory;
  final DateTime Function() _clock;

  /// Writes and deletes run one after another, in the order they were asked for.
  Future<void> _queue = Future.value();
  bool _accepting = true;

  Future<File> _file([String suffix = '']) async =>
      File('${(await _directory()).path}${Platform.pathSeparator}kitchen_board.json$suffix');

  Future<void> _enqueue(Future<void> Function() task) {
    final next = _queue.then((_) => task()).catchError((Object error) {
      // Saving is best effort: the screen keeps working from memory either way.
      debugPrint('Kitchen board store: ${error.runtimeType}');
    });
    _queue = next;
    return next;
  }

  @override
  Future<SavedBoard?> load(String restaurantId) async {
    _accepting = true;
    await _queue;
    final File file;
    try {
      file = await _file();
      if (!await file.exists()) return null;
    } on Exception {
      return null;
    }
    try {
      final board = SavedBoard.fromJson(jsonDecode(await file.readAsString()) as Map<String, dynamic>);
      if (board.restaurantId != restaurantId) return null; // another restaurant's: never shown
      // Either direction: a clock that jumped back must not keep an old board alive forever.
      if (_clock().difference(board.savedAt).abs() > SavedBoard.maxAge) {
        await _enqueue(_delete);
        return null;
      }
      return board;
    } catch (_) {
      await _enqueue(_delete); // unreadable (e.g. written by another app version): start fresh
      return null;
    }
  }

  @override
  Future<void> save(SavedBoard board) => _accepting ? _enqueue(() => _write(board)) : Future.value();

  Future<void> _write(SavedBoard board) async {
    final file = await _file();
    final temporary = await _file('.tmp');
    await temporary.writeAsString(jsonEncode(board.toJson()), flush: true);
    try {
      await temporary.rename(file.path);
    } on FileSystemException {
      // Some platforms refuse to rename over an existing file.
      if (await file.exists()) await file.delete();
      await temporary.rename(file.path);
    }
  }

  @override
  Future<void> clear() {
    _accepting = false;
    return _enqueue(_delete);
  }

  Future<void> _delete() async {
    for (final file in [await _file(), await _file('.tmp')]) {
      if (await file.exists()) await file.delete();
    }
  }
}
