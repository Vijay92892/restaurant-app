import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../core/widgets/status_badge.dart';
import 'pin_display.dart';
import 'table_models.dart';
import 'tables_cubit.dart';

class TablesPage extends StatelessWidget {
  const TablesPage({super.key});

  @override
  Widget build(BuildContext context) {
    return BlocConsumer<TablesCubit, TablesState>(
      listenWhen: (a, b) =>
          (b.error != null && a.error != b.error) || (b.alert != null && a.alert != b.alert),
      listener: (context, state) {
        final messenger = ScaffoldMessenger.of(context);
        if (state.alert != null) {
          messenger.showSnackBar(
            SnackBar(
              duration: const Duration(seconds: 12),
              showCloseIcon: true,
              content: Row(
                children: [
                  const Icon(Icons.gpp_maybe_rounded, color: Colors.amber),
                  const SizedBox(width: 12),
                  Expanded(child: Text(state.alert!)),
                ],
              ),
            ),
          );
        } else {
          messenger.showSnackBar(SnackBar(content: Text(state.error!)));
        }
      },
      builder: (context, state) {
        if (state.loading && state.tables.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }
        return RefreshIndicator(
          onRefresh: context.read<TablesCubit>().refresh,
          child: CustomScrollView(
            slivers: [
              if (state.pin != null) SliverToBoxAdapter(child: _PinBanner(state.pin!)),
              if (state.tables.isEmpty)
                const SliverFillRemaining(
                  child: Center(child: Text('No tables yet. An owner or admin can add them.')),
                )
              else
                SliverPadding(
                  padding: const EdgeInsets.all(16),
                  sliver: SliverGrid.builder(
                    gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                      maxCrossAxisExtent: 220,
                      mainAxisExtent: 150,
                      crossAxisSpacing: 12,
                      mainAxisSpacing: 12,
                    ),
                    itemCount: state.tables.length,
                    itemBuilder: (context, index) => _TableTile(state.tables[index]),
                  ),
                ),
            ],
          ),
        );
      },
    );
  }
}

class _PinBanner extends StatelessWidget {
  const _PinBanner(this.pin);

  final PinInfo pin;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final next = pin.nextRotationAt;
    return Semantics(
      label: 'Table PIN ${pin.pin.split('').join(' ')}',
      hint: 'Show it full screen',
      child: Container(
        margin: const EdgeInsets.fromLTRB(16, 16, 16, 0),
        decoration: BoxDecoration(
          color: theme.colorScheme.secondaryContainer,
          borderRadius: BorderRadius.circular(16),
        ),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: () => showPinFullScreen(context, pin.pin),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: Row(
              children: [
                Icon(Icons.pin_rounded, color: theme.colorScheme.onSecondaryContainer),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Table PIN for customers', style: theme.textTheme.labelLarge),
                      if (next != null)
                        Text('Changes at ${DateFormat.jm().format(next)}', style: theme.textTheme.bodySmall),
                    ],
                  ),
                ),
                Text(
                  pin.pin,
                  key: const Key('table-pin'),
                  style: theme.textTheme.headlineMedium?.copyWith(
                    fontWeight: FontWeight.w800,
                    letterSpacing: 6,
                  ),
                ),
                const SizedBox(width: 4),
                Icon(Icons.fullscreen_rounded, color: theme.colorScheme.onSecondaryContainer),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _TableTile extends StatelessWidget {
  const _TableTile(this.table);

  final TableSummary table;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final ready = table.readyOrders > 0;
    final readyColor = StatusBadge.readyColor(dark: theme.brightness == Brightness.dark);
    return Card(
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: ready ? readyColor : theme.colorScheme.outlineVariant, width: ready ? 3 : 1),
      ),
      color: table.isActive ? null : theme.colorScheme.surfaceContainerHighest,
      child: InkWell(
        onTap: () => context.push('/tables/${table.id}?label=${Uri.encodeQueryComponent(table.label)}'),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(table.label, style: theme.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800)),
              const Spacer(),
              if (!table.isActive)
                const _Fact(icon: Icons.block_rounded, text: 'Switched off')
              else if (!table.isBusy)
                _Fact(icon: Icons.event_seat_rounded, text: 'Free', color: theme.colorScheme.outline)
              else ...[
                if (table.activeSessions > 0)
                  _Fact(
                    icon: Icons.phone_iphone_rounded,
                    text: '${table.activeSessions} phone${table.activeSessions == 1 ? '' : 's'} ordering',
                  ),
                if (table.openOrders > 0)
                  _Fact(
                    icon: Icons.receipt_long_rounded,
                    text: '${table.openOrders} open order${table.openOrders == 1 ? '' : 's'}',
                  ),
              ],
              if (ready)
                _Fact(
                  icon: Icons.check_circle_rounded,
                  text: '${table.readyOrders} ready to serve',
                  color: readyColor,
                  bold: true,
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Fact extends StatelessWidget {
  const _Fact({required this.icon, required this.text, this.color, this.bold = false});

  final IconData icon;
  final String text;
  final Color? color;
  final bool bold;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Row(
        children: [
          Icon(icon, size: 18, color: color),
          const SizedBox(width: 6),
          Flexible(
            child: Text(
              text,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: color, fontWeight: bold ? FontWeight.w800 : FontWeight.w500),
            ),
          ),
        ],
      ),
    );
  }
}
