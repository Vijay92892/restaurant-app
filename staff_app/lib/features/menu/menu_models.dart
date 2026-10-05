import 'package:equatable/equatable.dart';

enum Dietary { veg, nonVeg, egg }

Dietary? _dietary(Object? value) => switch (value) {
  'veg' => Dietary.veg,
  'non_veg' => Dietary.nonVeg,
  'egg' => Dietary.egg,
  _ => null,
};

class MenuOption extends Equatable {
  const MenuOption({required this.id, required this.name, required this.isAvailable});

  final String id;
  final String name;
  final bool isAvailable;

  factory MenuOption.fromJson(Map<String, dynamic> json) => MenuOption(
    id: json['id'] as String,
    name: json['name'] as String,
    isAvailable: json['is_available'] as bool,
  );

  @override
  List<Object?> get props => [id, name, isAvailable];
}

class MenuGroup extends Equatable {
  const MenuGroup({
    required this.id,
    required this.name,
    required this.minSelect,
    required this.maxSelect,
    required this.options,
  });

  final String id;
  final String name;
  final int minSelect;
  final int maxSelect;
  final List<MenuOption> options;

  bool get isSingleChoice => maxSelect == 1;

  String get rule {
    if (minSelect == 0) return maxSelect == 1 ? 'Optional' : 'Optional · up to $maxSelect';
    if (minSelect == maxSelect) return 'Choose $minSelect';
    return 'Choose $minSelect–$maxSelect';
  }

  /// null when [selected] satisfies this group's rule.
  String? errorFor(List<String> selected) {
    if (selected.length < minSelect) {
      return minSelect == 1 ? 'Choose a ${name.toLowerCase()}' : 'Choose at least $minSelect';
    }
    if (selected.length > maxSelect) return 'Choose at most $maxSelect';
    return null;
  }

  /// Radio behaviour for single choice, capped checkboxes otherwise; unavailable options ignored.
  List<String> toggle(List<String> selected, String optionId) {
    final option = options.where((o) => o.id == optionId).firstOrNull;
    if (option == null || !option.isAvailable) return selected;
    if (isSingleChoice) {
      return selected.firstOrNull == optionId && minSelect == 0 ? [] : [optionId];
    }
    if (selected.contains(optionId)) return [...selected]..remove(optionId);
    if (selected.length >= maxSelect) return selected;
    return [...selected, optionId];
  }

  factory MenuGroup.fromJson(Map<String, dynamic> json) => MenuGroup(
    id: json['id'] as String,
    name: json['name'] as String,
    minSelect: json['min_select'] as int,
    maxSelect: json['max_select'] as int,
    options: [
      for (final o in json['options'] as List<dynamic>) MenuOption.fromJson(o as Map<String, dynamic>),
    ],
  );

  @override
  List<Object?> get props => [id, name, minSelect, maxSelect, options];
}

class MenuItem extends Equatable {
  const MenuItem({
    required this.id,
    required this.name,
    required this.description,
    required this.dietary,
    required this.isAvailable,
    required this.orderable,
    required this.groupIds,
  });

  final String id;
  final String name;
  final String? description;
  final Dietary? dietary;
  final bool isAvailable;
  final bool orderable;
  final List<String> groupIds;

  factory MenuItem.fromJson(Map<String, dynamic> json) => MenuItem(
    id: json['id'] as String,
    name: json['name'] as String,
    description: json['description'] as String?,
    dietary: _dietary(json['dietary']),
    isAvailable: json['is_available'] as bool,
    orderable: json['orderable'] as bool,
    groupIds: [for (final g in json['modifier_group_ids'] as List<dynamic>) g as String],
  );

  @override
  List<Object?> get props => [id, name, isAvailable, orderable, groupIds];
}

class MenuCategory extends Equatable {
  const MenuCategory({required this.id, required this.name, required this.items});

  final String id;
  final String name;
  final List<MenuItem> items;

  factory MenuCategory.fromJson(Map<String, dynamic> json) => MenuCategory(
    id: json['id'] as String,
    name: json['name'] as String,
    items: [for (final i in json['items'] as List<dynamic>) MenuItem.fromJson(i as Map<String, dynamic>)],
  );

  @override
  List<Object?> get props => [id, name, items];
}

/// What can be ordered right now (`GET /menu/ordering`).
class OrderingMenu extends Equatable {
  const OrderingMenu({required this.categories, required this.groups});

  final List<MenuCategory> categories;
  final Map<String, MenuGroup> groups;

  List<MenuGroup> groupsFor(MenuItem item) => [
    for (final id in item.groupIds)
      if (groups[id] != null) groups[id]!,
  ];

  factory OrderingMenu.fromJson(Map<String, dynamic> json) => OrderingMenu(
    categories: [
      for (final c in json['categories'] as List<dynamic>) MenuCategory.fromJson(c as Map<String, dynamic>),
    ],
    groups: {
      for (final g in json['modifier_groups'] as List<dynamic>)
        (g as Map<String, dynamic>)['id'] as String: MenuGroup.fromJson(g),
    },
  );

  @override
  List<Object?> get props => [categories, groups];
}
