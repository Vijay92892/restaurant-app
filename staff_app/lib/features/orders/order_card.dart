import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:intl/intl.dart';

import '../../core/auth/auth_bloc.dart';
import '../../core/auth/staff_user.dart';
import '../../core/widgets/status_badge.dart';
import '../problems/report_problem_sheet.dart';
import 'order_models.dart';
import 'orders_cubit.dart';

/// Why staff cancel an order directly (the server's reason codes). "Entered by mistake" tells
/// order mistakes apart from guests changing their minds in the pilot report.
const cancelReasons = {
  'customer_changed_mind': 'Guest changed their mind',
  'mistake': 'Entered by mistake',
  'item_unavailable': 'Dish not available',
  'duplicate': 'Duplicate order',
  'other': 'Other',
};

/// One order with the actions this staff member may take on it right now.
class OrderCard extends StatelessWidget {
  const OrderCard(this.order, {super.key, this.showTable = true});

  final StaffOrder order;
  final bool showTable;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final user = context.select((AuthBloc b) => b.state.user);
    final busy = context.select((OrdersCubit c) => c.state.busyOrderId == order.id);
    final cubit = context.read<OrdersCubit>();
    final (_, accent) = StatusBadge.styleFor(order.status, dark: theme.brightness == Brightness.dark);

    return Card(
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: theme.colorScheme.outlineVariant),
      ),
      child: Container(
        decoration: BoxDecoration(
          border: Border(left: BorderSide(color: accent, width: 6)),
          borderRadius: BorderRadius.circular(16),
        ),
        padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                if (showTable) ...[
                  Text(
                    order.tableLabel,
                    style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800),
                  ),
                  const SizedBox(width: 10),
                ],
                Text('#${order.orderNumber}', style: theme.textTheme.titleMedium),
                const Spacer(),
                Text(DateFormat.jm().format(order.createdAt), style: theme.textTheme.bodySmall),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                StatusBadge(order.status, cancelRequested: order.cancelRequested),
                Text(
                  order.fromCustomer ? 'From customer phone' : 'By ${order.placedBy ?? 'staff'}',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
            const SizedBox(height: 10),
            for (final line in order.items)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 40,
                      child: Text(
                        '${line.quantity}×',
                        style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800),
                      ),
                    ),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(line.name, style: theme.textTheme.titleMedium),
                          for (final modifier in line.modifiers)
                            Text(modifier, style: theme.textTheme.bodyMedium),
                          if (line.notes != null)
                            Text(
                              '“${line.notes}”',
                              style: theme.textTheme.bodyMedium?.copyWith(fontStyle: FontStyle.italic),
                            ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            if (order.notes != null)
              Text(
                'Note: ${order.notes}',
                style: theme.textTheme.bodyMedium?.copyWith(fontStyle: FontStyle.italic),
              ),
            if (order.cancelRequested && order.cancelRequestReason != null)
              Text(
                'Cancel requested: ${order.cancelRequestReason}',
                style: TextStyle(color: theme.colorScheme.error),
              ),
            if (user != null) _Actions(order: order, user: user, busy: busy, cubit: cubit),
          ],
        ),
      ),
    );
  }
}

class _Actions extends StatelessWidget {
  const _Actions({required this.order, required this.user, required this.busy, required this.cubit});

  final StaffOrder order;
  final StaffUser user;
  final bool busy;
  final OrdersCubit cubit;

  @override
  Widget build(BuildContext context) {
    final primary = switch (order.status) {
      OrderStatus.ready when user.can(Perm.ordersServe) => FilledButton.icon(
        key: Key('serve-${order.id}'),
        onPressed: busy ? null : () => cubit.markServed(order),
        icon: const Icon(Icons.room_service_rounded),
        label: const Text('Mark served'),
      ),
      OrderStatus.served when user.can(Perm.ordersServe) => OutlinedButton.icon(
        onPressed: busy ? null : () => cubit.markCompleted(order),
        icon: const Icon(Icons.done_all_rounded),
        label: const Text('Complete'),
      ),
      _ => null,
    };
    final canCancel =
        order.status.isActive &&
        order.status != OrderStatus.served &&
        (user.can(Perm.ordersCancel) ||
            user.can(Perm.ordersCancelPrivileged) ||
            user.can(Perm.ordersCancelRequest));
    final decide = order.cancelRequested && order.status.isActive && user.can(Perm.ordersCancelReview);
    final canReport = user.can(Perm.problemReportsCreate);
    if (primary == null && !canCancel && !decide && !canReport) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (decide) _Decision(order: order, busy: busy, cubit: cubit),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              if (primary != null) Expanded(child: primary),
              if (canCancel || canReport)
                PopupMenuButton<String>(
                  tooltip: 'Cancel or report a problem',
                  enabled: !busy,
                  icon: const Icon(Icons.more_vert_rounded),
                  onSelected: (value) => value == 'cancel'
                      ? _cancelSheet(context)
                      : showReportProblemSheet(context, order: order),
                  itemBuilder: (_) => [
                    if (canCancel && !order.cancelRequested)
                      PopupMenuItem(
                        value: 'cancel',
                        child: ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: const Icon(Icons.cancel_outlined),
                          title: Text(_mayCancelDirectly ? 'Cancel order' : 'Ask a manager to cancel'),
                        ),
                      ),
                    if (canReport)
                      const PopupMenuItem(
                        value: 'report',
                        child: ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: Icon(Icons.flag_outlined),
                          title: Text('Report a problem'),
                        ),
                      ),
                  ],
                ),
            ],
          ),
        ],
      ),
    );
  }

  /// Cancel directly when allowed at this stage; otherwise ask a manager.
  bool get _mayCancelDirectly {
    if (user.can(Perm.ordersCancelPrivileged)) return true;
    return user.can(Perm.ordersCancel) &&
        (order.status == OrderStatus.placed || order.status == OrderStatus.accepted);
  }

  Future<void> _cancelSheet(BuildContext context) async {
    final reason = TextEditingController();
    final direct = _mayCancelDirectly;
    var reasonCode = 'customer_changed_mind';
    final confirmed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) => StatefulBuilder(
        builder: (sheetContext, setSheetState) => Padding(
          padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.viewInsetsOf(sheetContext).bottom),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                direct
                    ? 'Cancel order #${order.orderNumber}?'
                    : 'Ask a manager to cancel #${order.orderNumber}',
                style: Theme.of(sheetContext).textTheme.titleLarge,
              ),
              const SizedBox(height: 8),
              Text(
                direct
                    ? 'The kitchen will see it as cancelled.'
                    : 'The kitchen has started. A manager will approve or reject the request.',
              ),
              if (direct) ...[
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final MapEntry(key: code, value: label) in cancelReasons.entries)
                      ChoiceChip(
                        label: Text(label),
                        selected: reasonCode == code,
                        onSelected: (_) => setSheetState(() => reasonCode = code),
                      ),
                  ],
                ),
              ],
              const SizedBox(height: 16),
              TextField(
                controller: reason,
                maxLength: 300,
                decoration: const InputDecoration(
                  labelText: 'Reason',
                  hintText: 'e.g. guest changed their mind',
                ),
              ),
              const SizedBox(height: 8),
              FilledButton(
                style: FilledButton.styleFrom(backgroundColor: Theme.of(sheetContext).colorScheme.error),
                onPressed: () => Navigator.pop(sheetContext, true),
                child: Text(direct ? 'Cancel order' : 'Send request'),
              ),
              TextButton(
                onPressed: () => Navigator.pop(sheetContext, false),
                child: const Text('Keep order'),
              ),
            ],
          ),
        ),
      ),
    );
    if (confirmed != true) return;
    final text = reason.text.trim();
    if (direct) {
      await cubit.cancel(order, reasonCode: reasonCode, reason: text);
    } else {
      await cubit.requestCancellation(order, text.isEmpty ? 'Requested by staff' : text);
    }
  }
}

/// A manager decides on a cancellation request: cancel the order, or tell the kitchen to carry
/// on. Shown on the order itself, so the reason and the dishes are in view.
class _Decision extends StatelessWidget {
  const _Decision({required this.order, required this.busy, required this.cubit});

  final StaffOrder order;
  final bool busy;
  final OrdersCubit cubit;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: theme.colorScheme.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Icon(Icons.report_rounded, color: theme.colorScheme.onErrorContainer),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'Cancel this order? The kitchen has started.',
                  style: TextStyle(color: theme.colorScheme.onErrorContainer, fontWeight: FontWeight.w700),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  key: Key('reject-${order.id}'),
                  onPressed: busy ? null : () => cubit.rejectCancellation(order),
                  child: const Text('Keep cooking'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: FilledButton(
                  key: Key('approve-${order.id}'),
                  style: FilledButton.styleFrom(backgroundColor: theme.colorScheme.error),
                  onPressed: busy ? null : () => cubit.approveCancellation(order),
                  child: const Text('Yes, cancel it'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
