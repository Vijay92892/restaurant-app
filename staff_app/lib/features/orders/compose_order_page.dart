import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../../core/widgets/status_badge.dart';
import '../menu/menu_models.dart';
import '../menu/menu_repository.dart';
import 'compose_order_cubit.dart';
import 'order_draft.dart';
import 'orders_repository.dart';

/// Waiter builds an order for one table and sends it to the kitchen (no PIN: the staff role
/// authorises it on the server).
class ComposeOrderPage extends StatelessWidget {
  const ComposeOrderPage({super.key, required this.tableId, required this.label});

  final String tableId;
  final String label;

  @override
  Widget build(BuildContext context) {
    return BlocProvider(
      create: (context) => ComposeOrderCubit(
        tableId: tableId,
        menu: context.read<MenuRepository>(),
        orders: context.read<OrdersRepository>(),
      )..load(),
      child: _ComposeView(label: label),
    );
  }
}

class _ComposeView extends StatelessWidget {
  const _ComposeView({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return BlocConsumer<ComposeOrderCubit, ComposeState>(
      listenWhen: (a, b) => (b.message != null && a.message != b.message) || (b.status != a.status),
      listener: (context, state) {
        if (state.status == ComposeStatus.sent) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text('Order #${state.placed?.orderNumber} sent to the kitchen')));
          Navigator.of(context).pop(true);
          return;
        }
        if (state.message != null) {
          ScaffoldMessenger.of(context)
            ..hideCurrentSnackBar()
            ..showSnackBar(SnackBar(content: Text(state.message!), duration: const Duration(seconds: 2)));
        }
      },
      builder: (context, state) {
        final menu = state.menu;
        return Scaffold(
          appBar: AppBar(title: Text('New order · $label')),
          body: switch (state.status) {
            ComposeStatus.loading => const Center(child: CircularProgressIndicator()),
            ComposeStatus.failed when menu == null => Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(state.message ?? 'Could not load the menu.'),
                  const SizedBox(height: 12),
                  OutlinedButton(
                    onPressed: context.read<ComposeOrderCubit>().load,
                    child: const Text('Try again'),
                  ),
                ],
              ),
            ),
            _ => _MenuList(menu: menu!),
          },
          bottomNavigationBar: state.draft.isEmpty
              ? null
              : SafeArea(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                    child: FilledButton.icon(
                      key: const Key('review-order'),
                      onPressed: () => _showReview(context),
                      icon: const Icon(Icons.receipt_long_rounded),
                      label: Text(
                        'Review order · ${state.draft.totalQuantity} '
                        '${state.draft.totalQuantity == 1 ? 'item' : 'items'}',
                      ),
                    ),
                  ),
                ),
        );
      },
    );
  }

  Future<void> _showReview(BuildContext context) => showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (_) => BlocProvider.value(value: context.read<ComposeOrderCubit>(), child: const _ReviewSheet()),
  );
}

class _MenuList extends StatelessWidget {
  const _MenuList({required this.menu});

  final OrderingMenu menu;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (menu.categories.isEmpty) {
      return const Center(child: Text('The menu is empty. An owner or admin can add dishes.'));
    }
    return ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        for (final category in menu.categories) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 20, 16, 4),
            child: Text(
              category.name,
              style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800),
            ),
          ),
          for (final item in category.items) _ItemTile(item: item, groups: menu.groupsFor(item)),
        ],
      ],
    );
  }
}

class _ItemTile extends StatelessWidget {
  const _ItemTile({required this.item, required this.groups});

  final MenuItem item;
  final List<MenuGroup> groups;

  @override
  Widget build(BuildContext context) {
    final cubit = context.read<ComposeOrderCubit>();
    final unavailable = !item.orderable;
    return ListTile(
      key: Key('item-${item.id}'),
      enabled: !unavailable,
      minVerticalPadding: 12,
      leading: DietMark(item.dietary),
      title: Text(item.name, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 17)),
      subtitle: unavailable
          ? Text(item.isAvailable ? 'Unavailable' : 'Sold out')
          : (item.description == null
                ? null
                : Text(item.description!, maxLines: 1, overflow: TextOverflow.ellipsis)),
      trailing: unavailable
          ? null
          : FilledButton.tonal(
              onPressed: () => groups.isEmpty ? cubit.addItem(item) : _chooseOptions(context),
              child: Text(groups.isEmpty ? 'Add' : 'Choose'),
            ),
      onTap: unavailable ? null : () => groups.isEmpty ? cubit.addItem(item) : _chooseOptions(context),
    );
  }

  Future<void> _chooseOptions(BuildContext context) => showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (_) => BlocProvider.value(
      value: context.read<ComposeOrderCubit>(),
      child: _OptionsSheet(item: item, groups: groups),
    ),
  );
}

class _OptionsSheet extends StatefulWidget {
  const _OptionsSheet({required this.item, required this.groups});

  final MenuItem item;
  final List<MenuGroup> groups;

  @override
  State<_OptionsSheet> createState() => _OptionsSheetState();
}

class _OptionsSheetState extends State<_OptionsSheet> {
  late final Map<String, List<String>> _selected = {for (final g in widget.groups) g.id: <String>[]};
  final Map<String, String?> _errors = {};
  final _notes = TextEditingController();
  int _quantity = 1;

  @override
  void dispose() {
    _notes.dispose();
    super.dispose();
  }

  void _add() {
    setState(() {
      for (final group in widget.groups) {
        _errors[group.id] = group.errorFor(_selected[group.id]!);
      }
    });
    if (_errors.values.any((e) => e != null)) return;
    final options = [
      for (final group in widget.groups)
        for (final id in _selected[group.id]!) group.options.firstWhere((o) => o.id == id),
    ];
    final added = context.read<ComposeOrderCubit>().addItem(
      widget.item,
      quantity: _quantity,
      options: options,
      notes: _notes.text,
    );
    if (added) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  DietMark(widget.item.dietary, size: 18),
                  const SizedBox(width: 8),
                  Expanded(child: Text(widget.item.name, style: theme.textTheme.titleLarge)),
                ],
              ),
              for (final group in widget.groups) ...[
                const SizedBox(height: 16),
                Text.rich(
                  TextSpan(
                    children: [
                      TextSpan(
                        text: group.name,
                        style: const TextStyle(fontWeight: FontWeight.w800),
                      ),
                      TextSpan(text: '   ${group.rule}', style: theme.textTheme.bodySmall),
                    ],
                  ),
                ),
                if (group.isSingleChoice)
                  RadioGroup<String>(
                    groupValue: _selected[group.id]!.firstOrNull,
                    onChanged: (optionId) => setState(() {
                      if (optionId == null) return;
                      _selected[group.id] = group.toggle(_selected[group.id]!, optionId);
                      _errors[group.id] = null;
                    }),
                    child: Column(
                      children: [
                        for (final option in group.options)
                          RadioListTile<String>(
                            value: option.id,
                            enabled: option.isAvailable,
                            title: Text(option.name),
                            subtitle: option.isAvailable ? null : const Text('Not available'),
                          ),
                      ],
                    ),
                  )
                else
                  for (final option in group.options)
                    CheckboxListTile(
                      value: _selected[group.id]!.contains(option.id),
                      title: Text(option.name),
                      subtitle: option.isAvailable ? null : const Text('Not available'),
                      controlAffinity: ListTileControlAffinity.leading,
                      onChanged: option.isAvailable
                          ? (_) => setState(() {
                              _selected[group.id] = group.toggle(_selected[group.id]!, option.id);
                              _errors[group.id] = null;
                            })
                          : null,
                    ),
                if (_errors[group.id] != null)
                  Text(
                    _errors[group.id]!,
                    style: TextStyle(color: theme.colorScheme.error, fontWeight: FontWeight.w600),
                  ),
              ],
              const SizedBox(height: 16),
              TextField(
                controller: _notes,
                maxLength: 200,
                decoration: const InputDecoration(
                  labelText: 'Note for the kitchen (optional)',
                  hintText: 'e.g. no onion',
                ),
              ),
              Row(
                children: [
                  IconButton.outlined(
                    tooltip: 'Fewer',
                    onPressed: _quantity > 1 ? () => setState(() => _quantity--) : null,
                    icon: const Icon(Icons.remove_rounded),
                  ),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Text('$_quantity', style: theme.textTheme.titleLarge),
                  ),
                  IconButton.outlined(
                    tooltip: 'More',
                    onPressed: _quantity < maxLineQuantity ? () => setState(() => _quantity++) : null,
                    icon: const Icon(Icons.add_rounded),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: FilledButton(onPressed: _add, child: const Text('Add to order')),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ReviewSheet extends StatefulWidget {
  const _ReviewSheet();

  @override
  State<_ReviewSheet> createState() => _ReviewSheetState();
}

class _ReviewSheetState extends State<_ReviewSheet> {
  late final _notes = TextEditingController(text: context.read<ComposeOrderCubit>().state.draft.notes);

  @override
  void dispose() {
    _notes.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return BlocConsumer<ComposeOrderCubit, ComposeState>(
      listenWhen: (a, b) => b.draft.isEmpty || b.status == ComposeStatus.sent,
      listener: (context, state) => Navigator.of(context).maybePop(),
      builder: (context, state) {
        final cubit = context.read<ComposeOrderCubit>();
        final sending = state.status == ComposeStatus.sending;
        return SafeArea(
          child: Padding(
            padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text('Review order', style: theme.textTheme.titleLarge),
                  const SizedBox(height: 8),
                  for (final line in state.draft.lines)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(line.name, style: const TextStyle(fontWeight: FontWeight.w700)),
                      subtitle: line.modifierLabels.isEmpty && line.notes.isEmpty
                          ? null
                          : Text(
                              [
                                ...line.modifierLabels,
                                if (line.notes.isNotEmpty) '“${line.notes}”',
                              ].join('\n'),
                            ),
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          IconButton(
                            tooltip: line.quantity == 1 ? 'Remove ${line.name}' : 'One less',
                            onPressed: sending ? null : () => cubit.setQuantity(line.key, line.quantity - 1),
                            icon: Icon(
                              line.quantity == 1 ? Icons.delete_outline_rounded : Icons.remove_rounded,
                            ),
                          ),
                          Text('${line.quantity}', style: theme.textTheme.titleMedium),
                          IconButton(
                            tooltip: 'One more',
                            onPressed: sending ? null : () => cubit.setQuantity(line.key, line.quantity + 1),
                            icon: const Icon(Icons.add_rounded),
                          ),
                        ],
                      ),
                    ),
                  const SizedBox(height: 8),
                  TextField(
                    controller: _notes,
                    maxLength: 500,
                    maxLines: 2,
                    onChanged: cubit.setNotes,
                    decoration: const InputDecoration(labelText: 'Note for the whole order (optional)'),
                  ),
                  const SizedBox(height: 8),
                  FilledButton.icon(
                    key: const Key('send-order'),
                    onPressed: sending ? null : cubit.send,
                    icon: const Icon(Icons.send_rounded),
                    label: Text(sending ? 'Sending…' : 'Send to kitchen'),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}
