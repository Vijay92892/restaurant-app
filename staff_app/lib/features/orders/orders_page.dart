import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../../core/auth/auth_bloc.dart';
import '../../core/auth/staff_user.dart';
import 'order_card.dart';
import 'order_models.dart';
import 'orders_cubit.dart';

/// All live orders in the restaurant, grouped by what the waiter needs to do next.
class OrdersPage extends StatelessWidget {
  const OrdersPage({super.key});

  @override
  Widget build(BuildContext context) {
    return BlocConsumer<OrdersCubit, OrdersState>(
      listenWhen: (a, b) => b.message != null && a.message != b.message,
      listener: (context, state) =>
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(state.message!))),
      builder: (context, state) {
        if (state.loading && state.orders.isEmpty) return const Center(child: CircularProgressIndicator());
        final decide = context.select((AuthBloc b) => b.state.user?.can(Perm.ordersCancelReview) ?? false);
        final waiting = decide ? state.awaitingDecision : const <StaffOrder>[];
        List<StaffOrder> rest(Set<OrderStatus> statuses) =>
            state.withStatus(statuses).where((o) => !waiting.contains(o)).toList();
        final sections = [
          ('Waiting for your decision', waiting),
          ('Ready to serve', rest({OrderStatus.ready})),
          ('In the kitchen', rest({OrderStatus.placed, OrderStatus.accepted, OrderStatus.preparing})),
          ('Served', rest({OrderStatus.served})),
        ];
        return RefreshIndicator(
          onRefresh: context.read<OrdersCubit>().refresh,
          child: state.orders.isEmpty
              ? ListView(
                  children: const [
                    SizedBox(height: 120),
                    Center(child: Text('No open orders right now.')),
                  ],
                )
              : ListView(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                  children: [
                    for (final (title, orders) in sections)
                      if (orders.isNotEmpty) ...[
                        Padding(
                          padding: const EdgeInsets.fromLTRB(4, 16, 4, 8),
                          child: Text(
                            '$title · ${orders.length}',
                            style: Theme.of(context).textTheme.titleMedium
                                ?.copyWith(fontWeight: FontWeight.w800),
                          ),
                        ),
                        for (final order in orders)
                          Padding(padding: const EdgeInsets.only(bottom: 12), child: OrderCard(order)),
                      ],
                  ],
                ),
        );
      },
    );
  }
}
