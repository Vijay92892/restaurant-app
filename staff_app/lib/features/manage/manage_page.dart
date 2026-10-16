import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';

import '../../core/auth/auth_bloc.dart';
import '../../core/auth/staff_user.dart';

/// May this person open the manager screens at all?
bool canManage(StaffUser user) =>
    user.can(Perm.pinManage) || user.can(Perm.staffRead) || user.can(Perm.problemReportsRead);

/// The owner's/admin's corner: table PIN, staff, sold-out dishes, problem reports.
class ManagePage extends StatelessWidget {
  const ManagePage({super.key});

  @override
  Widget build(BuildContext context) {
    final user = context.select((AuthBloc b) => b.state.user);
    if (user == null) return const SizedBox.shrink();
    return Scaffold(
      appBar: AppBar(title: Text('Manage ${user.restaurantName}')),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
          if (user.can(Perm.pinView))
            const _Entry(
              icon: Icons.pin_rounded,
              title: 'Table PIN',
              subtitle: 'See it, change it now, how often it changes, sign out phones',
              path: '/manage/pin',
            ),
          if (user.can(Perm.restaurantSettingsWrite))
            const _Entry(
              icon: Icons.location_on_rounded,
              title: 'Customer access',
              subtitle: 'Table PIN or a location check: how guests open their table',
              path: '/manage/access',
            ),
          if (user.can(Perm.menuAvailability))
            _Entry(
              icon: Icons.no_food_rounded,
              title: 'Sold-out dishes',
              subtitle: user.can(Perm.menuManage)
                  ? 'Mark dishes sold out, or hide them from the menu'
                  : 'Stop dishes and options from being ordered',
              path: '/sold-out',
            ),
          if (user.can(Perm.staffRead))
            const _Entry(
              icon: Icons.badge_rounded,
              title: 'Staff',
              subtitle: 'Add people, change roles, reset passwords',
              path: '/manage/staff',
            ),
          if (user.can(Perm.problemReportsRead))
            const _Entry(
              icon: Icons.flag_rounded,
              title: 'Problem reports',
              subtitle: 'What staff reported during service',
              path: '/manage/reports',
            ),
        ],
      ),
    );
  }
}

class _Entry extends StatelessWidget {
  const _Entry({required this.icon, required this.title, required this.subtitle, required this.path});

  final IconData icon;
  final String title;
  final String subtitle;
  final String path;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: Icon(icon),
      title: Text(title),
      subtitle: Text(subtitle),
      trailing: const Icon(Icons.chevron_right_rounded),
      onTap: () => context.push(path),
    );
  }
}
