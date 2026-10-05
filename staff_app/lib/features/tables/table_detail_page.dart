import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';

import '../../core/auth/auth_bloc.dart';
import '../../core/auth/staff_user.dart';
import '../../core/realtime/live_updates.dart';
import '../orders/order_card.dart';
import '../orders/order_models.dart';
import '../orders/orders_cubit.dart';
import '../orders/orders_repository.dart';
import 'tables_repository.dart';

/// One table: its live orders, a "New order" button and "Close table" when the party leaves.
class TableDetailPage extends StatelessWidget {
  const TableDetailPage({super.key, required this.tableId, required this.label});

  final String tableId;
  final String label;

  @override
  Widget build(BuildContext context) {
    return BlocProvider(
      create: (context) =>
          OrdersCubit(context.read<OrdersRepository>(), tableId: tableId, live: context.read<LiveUpdates>())
            ..start(),
      child: _TableDetailView(tableId: tableId, label: label),
    );
  }
}

class _TableDetailView extends StatelessWidget {
  const _TableDetailView({required this.tableId, required this.label});

  final String tableId;
  final String label;

  Future<void> _newOrder(BuildContext context) async {
    final placed = await context.push<bool>(
      '/tables/$tableId/order?label=${Uri.encodeQueryComponent(label)}',
    );
    if (placed == true && context.mounted) await context.read<OrdersCubit>().refresh();
  }

  Future<void> _closeTable(BuildContext context, List<StaffOrder> orders) async {
    final unfinished = orders.where((o) => o.status != OrderStatus.served).length;
    final ok = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Close $label?'),
        content: Text(
          'Customers at this table will need the PIN again to order. '
          'Served orders will be marked complete.'
          '${unfinished > 0 ? '\n\n$unfinished order${unfinished == 1 ? ' is' : 's are'} not served yet.' : ''}',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Not yet')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Close table')),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await context.read<TablesRepository>().closeTable(tableId);
      messenger.showSnackBar(SnackBar(content: Text('$label closed')));
      if (context.mounted) Navigator.of(context).pop();
    } on Exception catch (error) {
      messenger.showSnackBar(SnackBar(content: Text('Could not close the table: $error')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final user = context.select((AuthBloc b) => b.state.user)!;
    return BlocConsumer<OrdersCubit, OrdersState>(
      listenWhen: (a, b) => b.message != null && a.message != b.message,
      listener: (context, state) =>
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(state.message!))),
      builder: (context, state) {
        return Scaffold(
          appBar: AppBar(
            title: Text('Table $label'),
            actions: [
              if (user.can(Perm.tablesClose))
                TextButton.icon(
                  onPressed: () => _closeTable(context, state.orders),
                  icon: const Icon(Icons.cleaning_services_rounded),
                  label: const Text('Close table'),
                ),
            ],
          ),
          floatingActionButton: user.can(Perm.ordersCreate)
              ? FloatingActionButton.extended(
                  key: const Key('new-order'),
                  onPressed: () => _newOrder(context),
                  icon: const Icon(Icons.add_rounded),
                  label: const Text('New order'),
                )
              : null,
          body: state.loading && state.orders.isEmpty
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: context.read<OrdersCubit>().refresh,
                  child: state.orders.isEmpty
                      ? ListView(
                          children: const [
                            SizedBox(height: 120),
                            Center(child: Text('No open orders at this table.')),
                          ],
                        )
                      : ListView.separated(
                          padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
                          itemCount: state.orders.length,
                          separatorBuilder: (_, _) => const SizedBox(height: 12),
                          itemBuilder: (context, i) => OrderCard(state.orders[i], showTable: false),
                        ),
                ),
        );
      },
    );
  }
}
