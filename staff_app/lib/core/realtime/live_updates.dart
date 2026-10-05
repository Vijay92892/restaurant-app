import 'package:flutter/foundation.dart';

/// Connection state shown to staff, so nobody trusts a screen that has silently gone stale.
enum LiveStatus {
  /// Not connected; screens fall back to polling and show what they last received.
  offline,

  /// Opening or re-opening the connection.
  connecting,

  /// Connected and receiving this restaurant's events.
  live,
}

/// One server event for this restaurant (`order.created`, `order.updated`, `pin.rotated`…).
class LiveEvent {
  const LiveEvent({
    required this.id,
    required this.type,
    this.orderId,
    this.orderVersion,
    this.payload = const {},
  });

  /// Synthetic event: the connection was (re)established and events may have been missed,
  /// so every screen must reload its data from the REST API.
  static const resync = LiveEvent(id: '', type: 'resync');

  final String id;
  final String type;
  final String? orderId;
  final int? orderVersion;
  final Map<String, dynamic> payload;

  bool get isResync => type == 'resync';

  factory LiveEvent.fromJson(Map<String, dynamic> json) => LiveEvent(
    id: json['event_id'] as String,
    type: json['type'] as String,
    orderId: json['order_id'] as String?,
    orderVersion: json['order_version'] as int?,
    payload: (json['payload'] as Map<String, dynamic>?) ?? const {},
  );
}

/// What screens depend on: a stream of events and the connection state.
abstract interface class LiveUpdates {
  Stream<LiveEvent> get events;
  ValueListenable<LiveStatus> get status;
}
