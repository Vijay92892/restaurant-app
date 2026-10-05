import '../../core/network/api_client.dart';
import 'kitchen_models.dart';

class KitchenRepository {
  const KitchenRepository(this._api);

  final ApiClient _api;

  Future<KitchenBoard> board() async =>
      KitchenBoard.fromJson(await _api.getJson<Map<String, dynamic>>('/kitchen/board'));
}
