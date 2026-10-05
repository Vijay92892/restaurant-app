import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import 'availability.dart';
import 'sold_out_cubit.dart';

/// Mark dishes and options sold out (or back) during service. Customers and waiters stop
/// seeing them as orderable at once.
class SoldOutPage extends StatelessWidget {
  const SoldOutPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Sold-out dishes')),
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
                ..._section(context, 'Dishes', dishes, state, cubit.setDish),
                ..._section(context, 'Options', options, state, cubit.setOption),
                const SliverToBoxAdapter(child: SizedBox(height: 24)),
              ],
            ),
          );
        },
      ),
    );
  }

  List<Widget> _section(
    BuildContext context,
    String title,
    List<Switchable> entries,
    SoldOutState state,
    Future<void> Function(Switchable, {required bool available}) set,
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
      rows.add(_SwitchRow(entry: entry, busy: state.busy.contains(entry.id), onChanged: set));
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
            label: Text('Sold out only · $soldOut'),
            selected: state.onlySoldOut,
            onSelected: cubit.showOnlySoldOut,
          ),
        ],
      ),
    );
  }
}

/// One dish/option: its state in words and an icon (never colour alone) and a switch.
class _SwitchRow extends StatelessWidget {
  const _SwitchRow({required this.entry, required this.busy, required this.onChanged});

  final Switchable entry;
  final bool busy;
  final Future<void> Function(Switchable, {required bool available}) onChanged;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final soldOut = !entry.isAvailable;
    final color = soldOut ? theme.colorScheme.error : theme.colorScheme.onSurfaceVariant;
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
            Icon(soldOut ? Icons.block_rounded : Icons.check_circle_outline_rounded, size: 16, color: color),
            const SizedBox(width: 4),
            Text(
              soldOut ? 'SOLD OUT' : 'Available',
              style: TextStyle(color: color, fontWeight: soldOut ? FontWeight.w800 : FontWeight.w500),
            ),
          ],
        ),
        trailing: busy
            ? const SizedBox.square(dimension: 24, child: CircularProgressIndicator(strokeWidth: 3))
            : Switch(
                value: entry.isAvailable,
                onChanged: (value) => onChanged(entry, available: value),
              ),
        onTap: busy ? null : () => onChanged(entry, available: soldOut),
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
