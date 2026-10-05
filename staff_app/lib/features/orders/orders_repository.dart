import '../../core/network/api_client.dart';
import 'order_draft.dart';
import 'order_models.dart';

class OrdersRepository {
  const OrdersRepository(this._api);

  final ApiClient _api;

  static const activeStatuses = ['placed', 'accepted', 'preparing', 'ready', 'served'];

  /// Server clock minus device clock (see [ApiClient.serverClockOffset]).
  Duration get serverClockOffset => _api.serverClockOffset;

  Future<List<StaffOrder>> orders({List<String> statuses = activeStatuses, String? tableId}) async {
    final data = await _api.getJson<List<dynamic>>(
      '/orders',
      query: {'status': statuses, 'table_id': ?tableId, 'limit': 200},
    );
    return [for (final row in data) StaffOrder.fromJson(row as Map<String, dynamic>)];
  }

  /// Sends a waiter's order. [idempotencyKey] must stay the same across retries of the same
  /// draft, so a lost response can never create a second order.
  Future<StaffOrder> place({
    required String tableId,
    required OrderDraft draft,
    required String idempotencyKey,
  }) async {
    final response = await _api.postResponse<Map<String, dynamic>>(
      '/orders',
      data: draft.toRequest(tableId),
      headers: {'Idempotency-Key': idempotencyKey},
    );
    return StaffOrder.fromJson(response.data!);
  }

  Future<StaffOrder> transition(StaffOrder order, OrderStatus to) async => StaffOrder.fromJson(
    await _api.postJson<Map<String, dynamic>>(
      '/orders/${order.id}/transition',
      data: {'to_status': to.name, 'expected_version': order.version},
    ),
  );

  Future<StaffOrder> cancel(StaffOrder order, {required String reasonCode, String? reason}) async =>
      StaffOrder.fromJson(
        await _api.postJson<Map<String, dynamic>>(
          '/orders/${order.id}/cancel',
          data: {
            'reason_code': reasonCode,
            if (reason != null && reason.isNotEmpty) 'reason': reason,
            'expected_version': order.version,
          },
        ),
      );

  Future<StaffOrder> requestCancellation(StaffOrder order, String reason) async => StaffOrder.fromJson(
    await _api.postJson<Map<String, dynamic>>(
      '/orders/${order.id}/cancellation-request',
      data: {'reason': reason},
    ),
  );

  /// A manager's answer to a cancellation request: approve (the order is cancelled) or reject
  /// (the kitchen carries on).
  Future<StaffOrder> reviewCancellation(StaffOrder order, {required bool approve}) async =>
      StaffOrder.fromJson(
        await _api.postJson<Map<String, dynamic>>(
          '/orders/${order.id}/cancellation-request/${approve ? 'approve' : 'reject'}',
        ),
      );
}
