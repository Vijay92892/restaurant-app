import 'package:equatable/equatable.dart';

import '../../core/network/api_client.dart';

/// A dish or an option that can be switched sold out / available during service.
class Switchable extends Equatable {
  const Switchable({required this.id, required this.name, required this.section, required this.isAvailable});

  final String id;
  final String name;

  /// The dish's category, or the option's group ("Spice level").
  final String section;
  final bool isAvailable;

  Switchable withAvailable(bool value) =>
      Switchable(id: id, name: name, section: section, isAvailable: value);

  @override
  List<Object?> get props => [id, name, section, isAvailable];
}

/// What the kitchen can mark sold out: live dishes (in menu order) and their options.
class AvailabilityMenu extends Equatable {
  const AvailabilityMenu({required this.dishes, required this.options});

  final List<Switchable> dishes;
  final List<Switchable> options;

  int get soldOut => dishes.where((d) => !d.isAvailable).length + options.where((o) => !o.isAvailable).length;

  AvailabilityMenu replace(Switchable changed) => AvailabilityMenu(
    dishes: [for (final d in dishes) d.id == changed.id ? changed : d],
    options: [for (final o in options) o.id == changed.id ? changed : o],
  );

  /// From the staff menu (`GET /menu`). Hidden categories and dishes are left out: customers
  /// cannot order them anyway.
  factory AvailabilityMenu.fromJson(Map<String, dynamic> json) {
    final dishes = <Switchable>[];
    for (final c in json['categories'] as List<dynamic>) {
      final category = c as Map<String, dynamic>;
      if (category['is_active'] != true) continue;
      for (final i in category['items'] as List<dynamic>) {
        final item = i as Map<String, dynamic>;
        if (item['is_active'] != true) continue;
        dishes.add(
          Switchable(
            id: item['id'] as String,
            name: item['name'] as String,
            section: category['name'] as String,
            isAvailable: item['is_available'] as bool,
          ),
        );
      }
    }
    final options = <Switchable>[
      for (final g in json['modifier_groups'] as List<dynamic>)
        for (final m in (g as Map<String, dynamic>)['modifiers'] as List<dynamic>)
          Switchable(
            id: (m as Map<String, dynamic>)['id'] as String,
            name: m['name'] as String,
            section: g['name'] as String,
            isAvailable: m['is_available'] as bool,
          ),
    ];
    return AvailabilityMenu(dishes: dishes, options: options);
  }

  @override
  List<Object?> get props => [dishes, options];
}

class AvailabilityRepository {
  const AvailabilityRepository(this._api);

  final ApiClient _api;

  Future<AvailabilityMenu> load() async =>
      AvailabilityMenu.fromJson(await _api.getJson<Map<String, dynamic>>('/menu'));

  /// Returns the availability the server now has.
  Future<bool> setDish(String id, {required bool available}) async {
    final data = await _api.patchJson<Map<String, dynamic>>(
      '/menu/items/$id/availability',
      data: {'is_available': available},
    );
    return data['is_available'] as bool;
  }

  Future<bool> setOption(String id, {required bool available}) async {
    final data = await _api.patchJson<Map<String, dynamic>>(
      '/menu/modifiers/$id/availability',
      data: {'is_available': available},
    );
    return data['is_available'] as bool;
  }
}
