import '../../core/network/api_client.dart';
import 'menu_models.dart';

class MenuRepository {
  const MenuRepository(this._api);

  final ApiClient _api;

  Future<OrderingMenu> orderingMenu() async =>
      OrderingMenu.fromJson(await _api.getJson<Map<String, dynamic>>('/menu/ordering'));
}
