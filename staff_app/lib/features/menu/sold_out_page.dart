import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import 'availability.dart';
import 'sold_out_cubit.dart';

/// Mark dishes and options sold out (or back) during service. Customers and waiters stop
/// seeing them as orderable at once. Managers choose between sold out (still shown) and
/// hidden (taken off the menu) when they switch a dish off.
class SoldOutPage extends StatelessWidget {
  const SoldOutPage({super.key});

  @override
  Widget build(BuildContext context) {
    final canHide = context.read<SoldOutCubit>().canHide;
    return Scaffold(
      appBar: AppBar(title: Text(canHide ? 'Sold-out and hidden dishes' : 'Sold-out dishes')),
      body: BlocConsumer<SoldOutCubit, SoldOutState>(
        listenWhen: (a, b) => b.message != null && a.message != b.message,
        listener: (context, state) => ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text(state.message!))),
        builder: (context, state) {
          final menu = state.menu;
          if (menu == null) {
            if (state.loading) return const Center(child: CircularProgressIndicator());
            return _LoadFailed(message: state.error ?? 'The menu could not be loaded.');
          }
          final cubit = context.read<SoldOutCubit>();
          final dishes = state.visible(menu.dishes);
          final options = state.visible(menu.options);
          return RefreshIndicator(
            onRefresh: cubit.refresh,
            child: CustomScrollView(
              slivers: [
                SliverToBoxAdapter(
                  child: _Filters(state: state, soldOut: menu.soldOut),
                ),
                if (dishes.isEmpty && options.isEmpty)
                  SliverFillRemaining(
                    hasScrollBody: false,
                    child: Center(
                      child: Text(state.onlySoldOut ? 'Nothing is sold out.' : 'No dish or option matches.'),
                    ),
                  ),
                ..._section(
                  context,
                  'Dishes',
                  dishes,
                  state,
                  _dishSwitch(context, cubit),
                  _dishTap(context, cubit),
                ),
                ..._section(context, 'Options', options, state, cubit.setOption, null),
                const SliverToBoxAdapter(child: SizedBox(height: 24)),
              ],
            ),
          );
        },
      ),
    );
  }

  /// The kitchen's switch marks sold out directly; a manager switching a dish off chooses
  /// between sold out and hidden.
  _Set _dishSwitch(BuildContext context, SoldOutCubit cubit) {
    if (!cubit.canHide) return cubit.setDish;
    return (dish, {required available}) async {
      if (available) return cubit.setDishState(dish, DishState.available);
      final choice = await _chooseState(context, dish, offOnly: true);
      if (choice != null) await cubit.setDishState(dish, choice);
    };
  }

  /// Managers: tapping a dish offers all three states.
  void Function(Switchable)? _dishTap(BuildContext context, SoldOutCubit cubit) {
    if (!cubit.canHide) return null;
    return (dish) async {
      final choice = await _chooseState(context, dish, offOnly: false);
      if (choice != null) await cubit.setDishState(dish, choice);
    };
  }

  Future<DishState?> _chooseState(BuildContext context, Switchable dish, {required bool offOnly}) {
    Widget choice(BuildContext sheet, DishState value, IconData icon, String title, String subtitle) =>
        ListTile(
          key: Key('state-${value.name}'),
          leading: Icon(icon),
          title: Text(title),
          subtitle: Text(subtitle),
          trailing: dish.dishState == value ? const Icon(Icons.check_rounded) : null,
          onTap: () => Navigator.pop(sheet, value),
        );
    return showModalBottomSheet<DishState>(
      context: context,
      builder: (sheet) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(title: Text(dish.name, style: Theme.of(sheet).textTheme.titleLarge)),
            if (!offOnly)
              choice(
                sheet,
                DishState.available,
                Icons.check_circle_outline_rounded,
                'Available',
                'Guests can order it',
              ),
            choice(
              sheet,
              DishState.soldOut,
              Icons.block_rounded,
              'Sold out',
              "Guests still see it but can't order it",
            ),
            choice(
              sheet,
              DishState.hidden,
              Icons.visibility_off_rounded,
              'Hide from the menu',
              "Guests and waiters don't see it until you show it again",
            ),
          ],
        ),
      ),
    );
  }

  List<Widget> _section(
    BuildContext context,
    String title,
    List<Switchable> entries,
    SoldOutState state,
    _Set set,
    void Function(Switchable)? tap,
  ) {
    if (entries.isEmpty) return const [];
    final theme = Theme.of(context);
    final rows = <Widget>[];
    String? section;
    for (final entry in entries) {
      if (entry.section != section) {
        section = entry.section;
        rows.add(
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
            child: Text(
              entry.section,
              style: theme.textTheme.titleSmall?.copyWith(color: theme.colorScheme.primary),
            ),
          ),
        );
      }
      rows.add(_SwitchRow(entry: entry, busy: state.busy.contains(entry.id), onChanged: set, onTap: tap));
    }
    return [
      SliverToBoxAdapter(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
          child: Text(title, style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
        ),
      ),
      SliverList.list(children: rows),
    ];
  }
}

class _Filters extends StatelessWidget {
  const _Filters({required this.state, required this.soldOut});

  final SoldOutState state;
  final int soldOut;

  @override
  Widget build(BuildContext context) {
    final cubit = context.read<SoldOutCubit>();
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            decoration: const InputDecoration(
              prefixIcon: Icon(Icons.search_rounded),
              hintText: 'Find a dish or option',
            ),
            onChanged: cubit.search,
          ),
          const SizedBox(height: 8),
          FilterChip(
            avatar: const Icon(Icons.block_rounded, size: 18),
            label: Text(cubit.canHide ? 'Sold out or hidden · $soldOut' : 'Sold out only · $soldOut'),
            selected: state.onlySoldOut,
            onSelected: cubit.showOnlySoldOut,
          ),
        ],
      ),
    );
  }
}

typedef _Set = Future<void> Function(Switchable, {required bool available});

/// One dish/option: its state in words and an icon (never colour alone) and a switch.
class _SwitchRow extends StatelessWidget {
  const _SwitchRow({required this.entry, required this.busy, required this.onChanged, this.onTap});

  final Switchable entry;
  final bool busy;
  final _Set onChanged;

  /// Managers: open the choice of states instead of flipping the switch.
  final void Function(Switchable)? onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final soldOut = entry.isOff;
    final color = soldOut ? theme.colorScheme.error : theme.colorScheme.onSurfaceVariant;
    final (icon, label) = switch (entry.dishState) {
      DishState.hidden => (Icons.visibility_off_rounded, 'HIDDEN'),
      DishState.soldOut => (Icons.block_rounded, 'SOLD OUT'),
      DishState.available => (Icons.check_circle_outline_rounded, 'Available'),
    };
    return MergeSemantics(
      child: ListTile(
        key: Key('switch-${entry.id}'),
        title: Text(
          entry.name,
          style: soldOut
              ? TextStyle(color: theme.colorScheme.onSurfaceVariant, decoration: TextDecoration.lineThrough)
              : null,
        ),
        subtitle: Row(
          children: [
            Icon(icon, size: 16, color: color),
            const SizedBox(width: 4),
            Text(
              label,
              style: TextStyle(color: color, fontWeight: soldOut ? FontWeight.w800 : FontWeight.w500),
            ),
          ],
        ),
        trailing: busy
            ? const SizedBox.square(dimension: 24, child: CircularProgressIndicator(strokeWidth: 3))
            : Switch(
                value: !soldOut,
                onChanged: (value) => onChanged(entry, available: value),
              ),
        onTap: busy
            ? null
            : onTap != null
            ? () => onTap!(entry)
            : () => onChanged(entry, available: soldOut),
      ),
    );
  }
}

class _LoadFailed extends StatelessWidget {
  const _LoadFailed({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off_rounded, size: 40),
            const SizedBox(height: 12),
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 12),
            FilledButton(onPressed: context.read<SoldOutCubit>().refresh, child: const Text('Try again')),
          ],
        ),
      ),
    );
  }
}
