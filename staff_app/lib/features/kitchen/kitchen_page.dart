import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../core/auth/auth_bloc.dart';
import '../../core/auth/staff_user.dart';
import '../../core/realtime/live_indicator.dart';
import '../../core/realtime/live_updates.dart';
import '../../core/widgets/status_badge.dart';
import '../orders/order_models.dart';
import '../shell/home_shell.dart';
import 'kitchen_cubit.dart';
import 'kitchen_models.dart';

/// Kitchen display. Tablet (landscape): three columns — New, Cooking (with the dishes summed
/// across tables), Ready. Phone: the same three as tabs. Built to be read from a step away.
class KitchenPage extends StatelessWidget {
  const KitchenPage({super.key});

  static const wideLayout = 900.0;

  @override
  Widget build(BuildContext context) {
    final user = context.select((AuthBloc b) => b.state.user);
    return BlocConsumer<KitchenCubit, KitchenState>(
      listenWhen: (previous, current) => current.message != null && current.message != previous.message,
      listener: (context, state) => ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(state.message!))),
      builder: (context, state) {
        final wide = MediaQuery.sizeOf(context).width >= wideLayout;
        final scaffold = Scaffold(
          appBar: AppBar(
            // On phones the tab labels carry the counts.
            title: Row(
              children: [
                const Text('Kitchen'),
                if (wide && !state.loading) ...[
                  const SizedBox(width: 12),
                  Flexible(
                    child: Text(
                      '${state.activeCount} active',
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                ],
              ],
            ),
            actions: [
              // Tablets: in the bar, where cooks look. Phones: in the account menu (no room).
              if (wide && user != null && user.can(Perm.menuAvailability))
                TextButton.icon(
                  onPressed: () => context.push('/sold-out'),
                  icon: const Icon(Icons.no_food_rounded),
                  label: const Text('Sold out'),
                ),
              _SoundToggle(on: state.soundOn, showLabel: wide),
              LiveIndicator(state.live, compact: !wide),
              const SizedBox(width: 4),
              IconButton(
                tooltip: 'Reload orders',
                onPressed: context.read<KitchenCubit>().load,
                icon: const Icon(Icons.refresh_rounded),
              ),
              if (user != null) AccountMenu(user: user, offerKitchen: false),
            ],
            bottom: wide ? null : _tabBar(state),
          ),
          body: _body(context, state, wide),
        );
        return wide ? scaffold : DefaultTabController(length: 3, child: scaffold);
      },
    );
  }

  PreferredSizeWidget _tabBar(KitchenState state) => TabBar(
    tabs: [
      Tab(text: 'New (${state.newOrders.length})'),
      Tab(text: 'Cooking (${state.cooking.length})'),
      Tab(text: 'Ready (${state.ready.length})'),
    ],
  );

  Widget _body(BuildContext context, KitchenState state, bool wide) {
    if (state.loading && state.orders.isEmpty) return const Center(child: CircularProgressIndicator());
    if (state.loadError != null && state.orders.isEmpty && state.syncedAt == null) {
      return _LoadFailed(message: state.loadError!);
    }
    final columns = [
      _Lane(
        showTitle: wide,
        lane: _LaneKind.newOrders,
        count: state.newOrders.length,
        empty: 'No new orders',
        children: [for (final o in state.newOrders) KitchenTicket(o)],
      ),
      _Lane(
        showTitle: wide,
        lane: _LaneKind.cooking,
        count: state.cooking.length,
        empty: 'Nothing cooking',
        header: state.toCook.isEmpty ? null : _ToCook(state.toCook),
        children: [for (final o in state.cooking) KitchenTicket(o)],
      ),
      _Lane(
        showTitle: wide,
        lane: _LaneKind.ready,
        count: state.ready.length,
        empty: 'Nothing waiting for pickup',
        children: [for (final o in state.ready) KitchenTicket(o)],
      ),
    ];
    return Column(
      children: [
        _Notices(state.notices),
        _StaleBanner(state),
        Expanded(
          child: wide
              ? Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    for (final (i, column) in columns.indexed) ...[
                      if (i > 0) const VerticalDivider(width: 1),
                      Expanded(child: column),
                    ],
                  ],
                )
              : TabBarView(children: columns),
        ),
      ],
    );
  }
}

class _SoundToggle extends StatelessWidget {
  const _SoundToggle({required this.on, required this.showLabel});

  final bool on;
  final bool showLabel;

  @override
  Widget build(BuildContext context) {
    final cubit = context.read<KitchenCubit>();
    final icon = Icon(on ? Icons.volume_up_rounded : Icons.volume_off_rounded);
    final tooltip = on ? 'Sound on — tap to mute this tablet' : 'Sound off — tap to turn it on';
    if (!showLabel) {
      return IconButton(
        key: const Key('sound-toggle'),
        tooltip: tooltip,
        onPressed: cubit.toggleSound,
        icon: icon,
      );
    }
    return Tooltip(
      message: tooltip,
      child: TextButton.icon(
        key: const Key('sound-toggle'),
        style: TextButton.styleFrom(foregroundColor: Theme.of(context).colorScheme.onSurface),
        onPressed: cubit.toggleSound,
        icon: icon,
        label: Text(on ? 'Sound on' : 'Sound off'),
      ),
    );
  }
}

/// "Stop cooking" notices for cancelled orders, until someone taps OK. At most three are shown
/// so the board stays visible; the rest are summarised.
class _Notices extends StatelessWidget {
  const _Notices(this.notices);

  final List<KitchenNotice> notices;

  static const _shown = 3;

  @override
  Widget build(BuildContext context) {
    if (notices.isEmpty) return const SizedBox.shrink();
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final cubit = context.read<KitchenCubit>();
    final shown = notices.reversed.take(_shown).toList(); // newest first
    return Material(
      color: scheme.error,
      child: Column(
        children: [
          for (final notice in shown)
            Semantics(
              liveRegion: true,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 10, 12, 10),
                child: Row(
                  children: [
                    Icon(Icons.do_not_disturb_on_rounded, color: scheme.onError, size: 32),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            notice.title.toUpperCase(),
                            style: text.titleMedium?.copyWith(
                              color: scheme.onError,
                              fontWeight: FontWeight.w900,
                            ),
                          ),
                          Text(notice.detail, style: text.bodyLarge?.copyWith(color: scheme.onError)),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    FilledButton(
                      key: Key('dismiss-${notice.orderId}'),
                      style: FilledButton.styleFrom(
                        backgroundColor: scheme.onError,
                        foregroundColor: scheme.error,
                      ),
                      onPressed: () => cubit.dismissNotice(notice.orderId),
                      child: const Text('OK'),
                    ),
                  ],
                ),
              ),
            ),
          if (notices.length > _shown)
            Padding(
              padding: const EdgeInsets.fromLTRB(60, 0, 12, 6),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      '…and ${notices.length - _shown} more cancelled',
                      style: text.bodyMedium?.copyWith(color: scheme.onError),
                    ),
                  ),
                  TextButton(
                    style: TextButton.styleFrom(foregroundColor: scheme.onError),
                    onPressed: cubit.dismissAllNotices,
                    child: const Text('Clear all'),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// Explains, in words, when the screen may be out of date and why the buttons are paused.
class _StaleBanner extends StatelessWidget {
  const _StaleBanner(this.state);

  final KitchenState state;

  String? get _connection {
    final at = state.syncedAt;
    final asOf = at == null ? '' : ' (as of ${DateFormat.jm().format(at)})';
    if (state.fromSaved) {
      return 'Showing the orders saved on this tablet$asOf. Connecting… Buttons are paused until then.';
    }
    if (state.live == LiveStatus.live) return null;
    if (state.loadError != null) {
      return at == null
          ? 'Can’t reach the server. Retrying…'
          : 'Can’t reach the server — showing orders as of ${DateFormat.jm().format(at)}. '
                'Buttons are paused until it’s back.';
    }
    if (state.live == LiveStatus.offline) {
      return 'Live updates paused — checking for new orders every few seconds.';
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final lines = [
      ?_connection,
      if (state.truncated) 'Showing the oldest 500 orders. Please finish or cancel old orders.',
    ];
    if (lines.isEmpty) return const SizedBox.shrink();
    final scheme = Theme.of(context).colorScheme;
    return Material(
      color: scheme.errorContainer,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        child: Row(
          children: [
            Icon(Icons.cloud_off_rounded, color: scheme.onErrorContainer),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                lines.join('\n'),
                style: TextStyle(color: scheme.onErrorContainer, fontWeight: FontWeight.w600),
              ),
            ),
          ],
        ),
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
            const Icon(Icons.cloud_off_rounded, size: 48),
            const SizedBox(height: 12),
            Text(message, textAlign: TextAlign.center, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: context.read<KitchenCubit>().load,
              icon: const Icon(Icons.refresh_rounded),
              label: const Text('Try again'),
            ),
          ],
        ),
      ),
    );
  }
}

enum _LaneKind {
  newOrders('NEW ORDERS', Icons.fiber_new_rounded, OrderStatus.placed),
  cooking('COOKING', Icons.local_fire_department_rounded, OrderStatus.preparing),
  ready('READY FOR PICKUP', Icons.check_circle_rounded, OrderStatus.ready);

  const _LaneKind(this.title, this.icon, this.status);

  final String title;
  final IconData icon;
  final OrderStatus status;
}

class _Lane extends StatelessWidget {
  const _Lane({
    required this.lane,
    required this.count,
    required this.empty,
    required this.children,
    this.header,
    this.showTitle = true,
  });

  /// Hidden on phones, where the selected tab already names the lane and its count.
  final bool showTitle;
  final _LaneKind lane;
  final int count;
  final String empty;
  final Widget? header;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final (_, color) = StatusBadge.styleFor(lane.status, dark: theme.brightness == Brightness.dark);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (showTitle)
          Container(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 10),
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: color, width: 3)),
            ),
            child: Semantics(
              header: true,
              child: Row(
                children: [
                  Icon(lane.icon, color: color),
                  const SizedBox(width: 8),
                  Text(
                    lane.title,
                    style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900, color: color),
                  ),
                  const Spacer(),
                  Text(
                    '$count',
                    style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900, color: color),
                  ),
                ],
              ),
            ),
          ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.all(12),
            children: [
              ?header,
              if (children.isEmpty && header == null)
                Padding(
                  padding: const EdgeInsets.only(top: 32),
                  child: Text(
                    empty,
                    textAlign: TextAlign.center,
                    style: theme.textTheme.titleMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                  ),
                ),
              for (final child in children) Padding(padding: const EdgeInsets.only(bottom: 12), child: child),
            ],
          ),
        ),
      ],
    );
  }
}

/// Dishes summed across every order being cooked, each with its per-table breakdown.
class _ToCook extends StatelessWidget {
  const _ToCook(this.groups);

  final List<DishGroup> groups;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      color: theme.colorScheme.surfaceContainerHigh,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('TO COOK', style: theme.textTheme.labelLarge?.copyWith(fontWeight: FontWeight.w900)),
            const SizedBox(height: 8),
            for (final group in groups)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SizedBox(
                          width: 52,
                          child: Text(
                            '${group.total}×',
                            style: theme.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w900),
                          ),
                        ),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                group.name,
                                style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
                              ),
                              if (group.modifiers.isNotEmpty)
                                Text(group.modifiers.join(' · '), style: theme.textTheme.bodyMedium),
                            ],
                          ),
                        ),
                      ],
                    ),
                    Padding(
                      padding: const EdgeInsets.only(left: 52, top: 4),
                      child: Wrap(
                        spacing: 6,
                        runSpacing: 6,
                        children: [for (final p in group.portions) _PortionChip(p)],
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _PortionChip extends StatelessWidget {
  const _PortionChip(this.portion);

  final Portion portion;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final notes = portion.notes;
    final label = '${portion.tableLabel} ×${portion.quantity}';
    return Tooltip(
      message: 'Order #${portion.orderNumber}${notes == null ? '' : ' — “$notes”'}',
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: theme.colorScheme.outline),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(label, style: theme.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w700)),
            if (notes != null) ...[
              const SizedBox(width: 4),
              Icon(Icons.sticky_note_2_outlined, size: 16, semanticLabel: 'has a note: $notes'),
            ],
          ],
        ),
      ),
    );
  }
}

/// One order as a kitchen ticket, with the next step as a big button.
class KitchenTicket extends StatelessWidget {
  const KitchenTicket(this.order, {super.key});

  final StaffOrder order;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    final state = context.watch<KitchenCubit>().state;
    final user = context.select((AuthBloc b) => b.state.user);
    final delayed = state.isDelayed(order);
    final busy = state.busy.contains(order.id);
    final (_, accent) = StatusBadge.styleFor(order.status, dark: dark);
    final waited = state.waiting(order).inMinutes;

    return Card(
      key: Key('ticket-${order.id}'),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(
          color: delayed ? _delayedColor(dark) : theme.colorScheme.outlineVariant,
          width: delayed ? 2 : 1,
        ),
      ),
      child: Container(
        decoration: BoxDecoration(
          border: Border(left: BorderSide(color: accent, width: 6)),
          borderRadius: BorderRadius.circular(14),
        ),
        padding: const EdgeInsets.fromLTRB(14, 10, 10, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                Flexible(
                  child: Text(
                    order.tableLabel,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w900),
                  ),
                ),
                const SizedBox(width: 8),
                Text('#${order.orderNumber}', style: theme.textTheme.titleMedium),
                const Spacer(),
                Icon(Icons.schedule_rounded, size: 18, color: theme.colorScheme.onSurfaceVariant),
                const SizedBox(width: 4),
                Text(
                  order.status == OrderStatus.ready ? 'waiting $waited min' : '$waited min',
                  style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                if (order.status != OrderStatus.placed || order.cancelRequested)
                  StatusBadge(order.status, cancelRequested: order.cancelRequested),
                if (delayed) _DelayedBadge(minutes: waited),
                Text(
                  order.fromCustomer ? 'Customer' : 'By ${order.placedBy ?? 'staff'}',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
            const SizedBox(height: 8),
            for (final line in order.items) _Line(line),
            if (order.notes != null) _Note('Order note: ${order.notes}'),
            if (order.cancelRequested && order.cancelRequestReason != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'Cancellation requested: ${order.cancelRequestReason}',
                  style: TextStyle(color: theme.colorScheme.error, fontWeight: FontWeight.w600),
                ),
              ),
            if (user != null) _TicketActions(order: order, user: user, busy: busy || !state.canAct),
          ],
        ),
      ),
    );
  }
}

Color _delayedColor(bool dark) => dark ? const Color(0xFFFF8A80) : const Color(0xFFB3261E);

class _DelayedBadge extends StatelessWidget {
  const _DelayedBadge({required this.minutes});

  final int minutes;

  @override
  Widget build(BuildContext context) {
    final color = _delayedColor(Theme.of(context).brightness == Brightness.dark);
    return Semantics(
      label: 'Delayed: waiting $minutes minutes',
      excludeSemantics: true,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: color),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.warning_amber_rounded, size: 18, color: color),
            const SizedBox(width: 6),
            Text(
              'DELAYED',
              style: TextStyle(color: color, fontWeight: FontWeight.w900),
            ),
          ],
        ),
      ),
    );
  }
}

class _Line extends StatelessWidget {
  const _Line(this.line);

  final OrderLine line;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 44,
            child: Text(
              '${line.quantity}×',
              style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900),
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(line.name, style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w600)),
                for (final modifier in line.modifiers) Text(modifier, style: theme.textTheme.bodyLarge),
                if (line.notes != null) _Note(line.notes!),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Guest notes matter in a kitchen (allergies, "no onion"): shown with an icon and emphasis.
class _Note extends StatelessWidget {
  const _Note(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(top: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.sticky_note_2_outlined, size: 18, color: theme.colorScheme.tertiary),
          const SizedBox(width: 4),
          Expanded(
            child: Text(
              text,
              style: theme.textTheme.bodyLarge?.copyWith(
                fontStyle: FontStyle.italic,
                fontWeight: FontWeight.w600,
                color: theme.colorScheme.tertiary,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _TicketActions extends StatelessWidget {
  const _TicketActions({required this.order, required this.user, required this.busy});

  final StaffOrder order;
  final StaffUser user;

  /// An action is in flight, or changes are paused (offline): buttons are disabled.
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final cubit = context.read<KitchenCubit>();
    final canCook = user.can(Perm.ordersKitchen);
    final buttons = <Widget>[
      if (canCook && order.status == OrderStatus.placed) ...[
        OutlinedButton(
          key: Key('accept-${order.id}'),
          onPressed: busy ? null : () => cubit.accept(order),
          child: const Text('Accept'),
        ),
        FilledButton.icon(
          key: Key('cook-${order.id}'),
          onPressed: busy ? null : () => cubit.startCooking(order),
          icon: const Icon(Icons.local_fire_department_rounded),
          label: const Text('Start cooking'),
        ),
      ],
      if (canCook && order.status == OrderStatus.accepted)
        FilledButton.icon(
          key: Key('cook-${order.id}'),
          onPressed: busy ? null : () => cubit.startCooking(order),
          icon: const Icon(Icons.local_fire_department_rounded),
          label: const Text('Start cooking'),
        ),
      if (canCook && order.status == OrderStatus.preparing)
        FilledButton.icon(
          key: Key('ready-${order.id}'),
          style: FilledButton.styleFrom(
            backgroundColor: StatusBadge.readyColor(dark: Theme.of(context).brightness == Brightness.dark),
            foregroundColor: Theme.of(context).brightness == Brightness.dark ? Colors.black : Colors.white,
          ),
          onPressed: busy ? null : () => cubit.markReady(order),
          icon: const Icon(Icons.check_circle_rounded),
          label: const Text('Ready'),
        ),
    ];
    final canFlag = user.can(Perm.ordersCancelRequest) && order.status.inKitchen && !order.cancelRequested;
    if (buttons.isEmpty && !canFlag) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Row(
        children: [
          for (final (i, button) in buttons.indexed) ...[
            if (i > 0) const SizedBox(width: 8),
            Expanded(flex: button is FilledButton ? 2 : 1, child: button),
          ],
          if (buttons.isEmpty) const Spacer(),
          if (canFlag)
            IconButton(
              tooltip: 'Can’t make it — ask a manager to cancel',
              onPressed: busy ? null : () => _flag(context, cubit),
              icon: const Icon(Icons.more_vert_rounded),
            ),
        ],
      ),
    );
  }

  Future<void> _flag(BuildContext context, KitchenCubit cubit) async {
    final reason = TextEditingController();
    final send = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (sheet) => Padding(
        padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.viewInsetsOf(sheet).bottom),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              'Can’t make ${order.tableLabel} #${order.orderNumber}?',
              style: Theme.of(sheet).textTheme.titleLarge,
            ),
            const SizedBox(height: 8),
            const Text('A manager will approve or reject the cancellation. Keep cooking until then.'),
            const SizedBox(height: 16),
            TextField(
              controller: reason,
              autofocus: true,
              maxLength: 300,
              decoration: const InputDecoration(labelText: 'Reason', hintText: 'e.g. out of paneer'),
            ),
            const SizedBox(height: 8),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: Theme.of(sheet).colorScheme.error),
              onPressed: () => Navigator.pop(sheet, true),
              child: const Text('Send request'),
            ),
            TextButton(onPressed: () => Navigator.pop(sheet, false), child: const Text('Back')),
          ],
        ),
      ),
    );
    final text = reason.text.trim();
    reason.dispose();
    if (send == true) await cubit.requestCancellation(order, text.isEmpty ? 'Kitchen can’t make it' : text);
  }
}
