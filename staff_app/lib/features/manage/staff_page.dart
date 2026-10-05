import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:intl/intl.dart';

import '../../core/auth/auth_bloc.dart';
import '../../core/auth/staff_user.dart';
import 'manage_cubits.dart';
import 'manage_repositories.dart';

const _roleLabels = {
  StaffRole.owner: 'Owner',
  StaffRole.admin: 'Manager (admin)',
  StaffRole.waiter: 'Waiter',
  StaffRole.kitchen: 'Kitchen',
  StaffRole.unknown: 'Unknown',
};

/// Staff accounts. New people and password resets get a temporary password, shown once, which
/// they replace at their first sign-in. The server enforces who may change whom.
class StaffPage extends StatelessWidget {
  const StaffPage({super.key});

  @override
  Widget build(BuildContext context) {
    final user = context.select((AuthBloc b) => b.state.user);
    final canManage = user?.can(Perm.staffManage) ?? false;
    return Scaffold(
      appBar: AppBar(title: const Text('Staff')),
      floatingActionButton: canManage
          ? FloatingActionButton.extended(
              onPressed: () => _addSheet(context, user!),
              icon: const Icon(Icons.person_add_alt_1_rounded),
              label: const Text('Add staff'),
            )
          : null,
      body: BlocConsumer<StaffCubit, ManageState<List<StaffMember>>>(
        listenWhen: (a, b) =>
            (b.message != null && a.message != b.message) || (b.secret != null && a.secret != b.secret),
        listener: (context, state) {
          if (state.secret != null) {
            _showPassword(context, state.secret!.name, state.secret!.password);
          } else {
            ScaffoldMessenger.of(context)
              ..hideCurrentSnackBar()
              ..showSnackBar(SnackBar(content: Text(state.message!)));
          }
        },
        builder: (context, state) {
          final members = state.data;
          if (members == null) {
            return Center(
              child: state.loading
                  ? const CircularProgressIndicator()
                  : Text(state.error ?? 'Could not load staff.'),
            );
          }
          return RefreshIndicator(
            onRefresh: context.read<StaffCubit>().load,
            child: ListView(
              padding: const EdgeInsets.only(bottom: 96),
              children: [
                for (final member in members)
                  _MemberTile(
                    member: member,
                    editable: canManage && member.role != StaffRole.owner && member.id != user?.id,
                  ),
              ],
            ),
          );
        },
      ),
    );
  }

  Future<void> _showPassword(BuildContext context, String name, String password) => showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (dialogContext) => AlertDialog(
      title: Text('Temporary password for $name'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SelectableText(
            password,
            key: const Key('temporary-password'),
            style: Theme.of(dialogContext).textTheme.headlineSmall?.copyWith(fontFamily: 'monospace'),
          ),
          const SizedBox(height: 12),
          const Text(
            'Tell it to them in person. They choose their own password when they first sign in. '
            'It is not shown again.',
          ),
        ],
      ),
      actions: [FilledButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('Done'))],
    ),
  );

  Future<void> _addSheet(BuildContext context, StaffUser me) async {
    final cubit = context.read<StaffCubit>();
    final name = TextEditingController();
    final username = TextEditingController();
    final form = GlobalKey<FormState>();
    var role = StaffRole.waiter;
    final roles = [StaffRole.waiter, StaffRole.kitchen, if (me.can(Perm.staffManageAdmins)) StaffRole.admin];
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) => StatefulBuilder(
        builder: (sheetContext, setSheetState) => Padding(
          padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.viewInsetsOf(sheetContext).bottom),
          child: Form(
            key: form,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Add a staff member', style: Theme.of(sheetContext).textTheme.titleLarge),
                const SizedBox(height: 12),
                TextFormField(
                  controller: name,
                  decoration: const InputDecoration(labelText: 'Name (shown on orders)'),
                  maxLength: 80,
                  validator: (v) => (v ?? '').trim().isEmpty ? 'Enter a name' : null,
                ),
                TextFormField(
                  controller: username,
                  decoration: const InputDecoration(
                    labelText: 'Username (for signing in)',
                    helperText: '3–32 characters: a–z, 0–9, dot, dash, underscore',
                  ),
                  autocorrect: false,
                  validator: (v) => RegExp(r'^[a-z0-9._-]{3,32}$').hasMatch((v ?? '').trim().toLowerCase())
                      ? null
                      : 'Not a valid username',
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<StaffRole>(
                  initialValue: role,
                  decoration: const InputDecoration(labelText: 'Role'),
                  items: [for (final r in roles) DropdownMenuItem(value: r, child: Text(_roleLabels[r]!))],
                  onChanged: (value) => setSheetState(() => role = value ?? role),
                ),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: () async {
                    if (!form.currentState!.validate()) return;
                    final added = await cubit.add(
                      username: username.text.trim().toLowerCase(),
                      displayName: name.text.trim(),
                      role: role,
                    );
                    if (added && sheetContext.mounted) Navigator.pop(sheetContext);
                  },
                  child: const Text('Add and create a temporary password'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _MemberTile extends StatelessWidget {
  const _MemberTile({required this.member, required this.editable});

  final StaffMember member;
  final bool editable;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final status = !member.isActive
        ? (Icons.block_rounded, 'Switched off')
        : member.mustChangePassword
        ? (Icons.key_rounded, 'Has a temporary password')
        : (
            Icons.check_circle_outline_rounded,
            member.lastLoginAt == null
                ? 'Never signed in'
                : 'Last signed in ${DateFormat.MMMd().add_jm().format(member.lastLoginAt!)}',
          );
    return ListTile(
      key: Key('staff-${member.username}'),
      leading: CircleAvatar(child: Text(member.displayName.characters.first.toUpperCase())),
      title: Text('${member.displayName} · ${_roleLabels[member.role]}'),
      subtitle: Row(
        children: [
          Icon(status.$1, size: 16, color: member.isActive ? null : theme.colorScheme.error),
          const SizedBox(width: 4),
          Flexible(child: Text('${member.username} · ${status.$2}', overflow: TextOverflow.ellipsis)),
        ],
      ),
      trailing: editable ? const Icon(Icons.more_vert_rounded) : null,
      onTap: editable ? () => _actions(context) : null,
    );
  }

  Future<void> _actions(BuildContext context) async {
    final cubit = context.read<StaffCubit>();
    final me = context.read<AuthBloc>().state.user!;
    final roles = [StaffRole.waiter, StaffRole.kitchen, if (me.can(Perm.staffManageAdmins)) StaffRole.admin];
    final choice = await showModalBottomSheet<String>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(title: Text(member.displayName, style: Theme.of(sheetContext).textTheme.titleLarge)),
            for (final role in roles)
              if (role != member.role)
                ListTile(
                  leading: const Icon(Icons.swap_horiz_rounded),
                  title: Text('Make ${_roleLabels[role]!.toLowerCase()}'),
                  onTap: () => Navigator.pop(sheetContext, 'role:${role.name}'),
                ),
            ListTile(
              leading: const Icon(Icons.key_rounded),
              title: const Text('Reset password'),
              subtitle: const Text('Signs them out; they set a new password at next sign-in'),
              onTap: () => Navigator.pop(sheetContext, 'reset'),
            ),
            ListTile(
              leading: Icon(member.isActive ? Icons.block_rounded : Icons.check_circle_outline_rounded),
              title: Text(member.isActive ? 'Switch off (cannot sign in)' : 'Switch back on'),
              onTap: () => Navigator.pop(sheetContext, 'active'),
            ),
          ],
        ),
      ),
    );
    if (choice == null) return;
    if (choice == 'reset') {
      await cubit.resetPassword(member);
    } else if (choice == 'active') {
      await cubit.setActive(member, active: !member.isActive);
    } else if (choice.startsWith('role:')) {
      final role = StaffRole.values.byName(choice.substring(5));
      await cubit.setRole(member, role);
    }
  }
}
