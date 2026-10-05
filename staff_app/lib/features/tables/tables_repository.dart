import '../../core/network/api_client.dart';
import 'table_models.dart';

class TablesRepository {
  const TablesRepository(this._api);

  final ApiClient _api;

  Future<List<TableSummary>> tables() async {
    final data = await _api.getJson<List<dynamic>>('/tables');
    return [for (final row in data) TableSummary.fromJson(row as Map<String, dynamic>)];
  }

  Future<PinInfo> pin() async => PinInfo.fromJson(await _api.getJson<Map<String, dynamic>>('/pin'));

  /// The party has left: ends the table's customer sessions and completes served orders.
  Future<CloseTableResult> closeTable(String tableId) async {
    final data = await _api.postJson<Map<String, dynamic>>('/tables/$tableId/close');
    return CloseTableResult(
      sessionsEnded: data['sessions_ended'] as int,
      ordersCompleted: data['orders_completed'] as int,
    );
  }
}
