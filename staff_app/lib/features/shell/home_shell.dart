import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';

import '../../core/auth/auth_bloc.dart';
import '../../core/auth/staff_user.dart';
import '../manage/manage_page.dart';
import '../orders/orders_cubit.dart';
import '../problems/report_problem_sheet.dart';

/// Bottom navigation for floor staff (waiter / admin / owner): Tables and Orders.
class HomeShell extends StatelessWidget {
  const HomeShell({super.key, required this.shell});

  final StatefulNavigationShell shell;

  static const _titles = ['Tables', 'Orders'];

  @override
  Widget build(BuildContext context) {
    final user = context.select((AuthBloc b) => b.state.user);
    final decide = user?.can(Perm.ordersCancelReview) ?? false;
    final waiting = context.select((OrdersCubit c) => decide ? c.state.awaitingDecision.length : 0);
    return Scaffold(
      appBar: AppBar(
        title: Text(_titles[shell.currentIndex]),
        actions: [if (user != null) AccountMenu(user: user)],
      ),
      body: shell,
      bottomNavigationBar: NavigationBar(
        selectedIndex: shell.currentIndex,
        onDestinationSelected: (index) => shell.goBranch(index, initialLocation: index == shell.currentIndex),
        destinations: [
          const NavigationDestination(
            icon: Icon(Icons.table_restaurant_outlined),
            selectedIcon: Icon(Icons.table_restaurant),
            label: 'Tables',
          ),
          NavigationDestination(
            icon: Badge(
              isLabelVisible: waiting > 0,
              label: Text('$waiting'),
              child: const Icon(Icons.receipt_long_outlined),
            ),
            selectedIcon: Badge(
              isLabelVisible: waiting > 0,
              label: Text('$waiting'),
              child: const Icon(Icons.receipt_long),
            ),
            label: 'Orders',
            tooltip: waiting > 0 ? 'Orders, $waiting waiting for your decision' : 'Orders',
          ),
        ],
      ),
    );
  }
}

class AccountMenu extends StatelessWidget {
  const AccountMenu({super.key, required this.user, this.offerKitchen = true});

  final StaffUser user;

  /// Owners and admins can open the kitchen screen from here (kitchen staff live there).
  final bool offerKitchen;

  @override
  Widget build(BuildContext context) {
    final kitchenLink = offerKitchen && user.role != StaffRole.kitchen && user.can(Perm.ordersKitchen);
    return PopupMenuButton<String>(
      tooltip: 'Account',
      icon: CircleAvatar(child: Text(user.displayName.characters.first.toUpperCase())),
      onSelected: (value) {
        switch (value) {
          case 'logout':
            context.read<AuthBloc>().add(const AuthLogoutRequested());
          case 'report':
            showReportProblemSheet(context);
          default:
            context.push(value);
        }
      },
      itemBuilder: (_) => [
        PopupMenuItem<String>(
          enabled: false,
          child: ListTile(
            contentPadding: EdgeInsets.zero,
            title: Text(user.displayName),
            subtitle: Text('${user.role.name} · ${user.restaurantName}'),
          ),
        ),
        const PopupMenuDivider(),
        if (kitchenLink)
          const PopupMenuItem<String>(
            value: '/kitchen',
            child: ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.soup_kitchen_rounded),
              title: Text('Kitchen screen'),
            ),
          ),
        if (canManage(user))
          const PopupMenuItem<String>(
            value: '/manage',
            child: ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.storefront_rounded),
              title: Text('Manage restaurant'),
            ),
          ),
        if (user.can(Perm.menuAvailability))
          const PopupMenuItem<String>(
            value: '/sold-out',
            child: ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.no_food_rounded),
              title: Text('Sold-out dishes'),
            ),
          ),
        if (user.can(Perm.problemReportsCreate))
          const PopupMenuItem<String>(
            value: 'report',
            child: ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.flag_outlined),
              title: Text('Report a problem'),
            ),
          ),
        const PopupMenuItem<String>(
          value: 'logout',
          child: ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.logout_rounded),
            title: Text('Sign out'),
          ),
        ),
      ],
    );
  }
}
