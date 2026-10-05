import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:intl/intl.dart';

import '../../core/auth/auth_bloc.dart';
import '../../core/auth/staff_user.dart';
import '../tables/pin_display.dart';
import 'manage_cubits.dart';
import 'manage_repositories.dart';

/// Spec §35: the current PIN (hidden until asked), how often it changes, change it now, and
/// sign out every phone at once.
class PinSettingsPage extends StatelessWidget {
  const PinSettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Table PIN')),
      body: BlocConsumer<PinSettingsCubit, ManageState<PinSettings>>(
        listenWhen: (a, b) => b.message != null && a.message != b.message,
        listener: (context, state) => ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text(state.message!))),
        builder: (context, state) {
          final settings = state.data;
          if (settings == null) {
            return Center(
              child: state.loading
                  ? const CircularProgressIndicator()
                  : Text(state.error ?? 'Could not load the PIN.'),
            );
          }
          return _PinSettingsView(settings: settings, busy: state.busy);
        },
      ),
    );
  }
}

class _PinSettingsView extends StatefulWidget {
  const _PinSettingsView({required this.settings, required this.busy});

  final PinSettings settings;
  final bool busy;

  @override
  State<_PinSettingsView> createState() => _PinSettingsViewState();
}

class _PinSettingsViewState extends State<_PinSettingsView> {
  bool _revealed = false;
  late int? _rotation = widget.settings.rotationMinutes;
  late int _length = widget.settings.pinLength;

  @override
  void didUpdateWidget(_PinSettingsView old) {
    super.didUpdateWidget(old);
    if (old.settings.rotationMinutes != widget.settings.rotationMinutes ||
        old.settings.pinLength != widget.settings.pinLength) {
      _rotation = widget.settings.rotationMinutes;
      _length = widget.settings.pinLength;
    }
  }

  bool get _changed => _rotation != widget.settings.rotationMinutes || _length != widget.settings.pinLength;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final user = context.select((AuthBloc b) => b.state.user);
    final canManage = user?.can(Perm.pinManage) ?? false;
    final canEndAll = user?.can(Perm.tableSessionsInvalidateAll) ?? false;
    final settings = widget.settings;
    final cubit = context.read<PinSettingsCubit>();
    final next = settings.nextRotationAt;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Current PIN', style: theme.textTheme.labelLarge),
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        _revealed ? settings.pin : '•' * settings.pinLength,
                        key: const Key('pin-value'),
                        style: theme.textTheme.displaySmall?.copyWith(
                          fontWeight: FontWeight.w800,
                          letterSpacing: 8,
                        ),
                      ),
                    ),
                    IconButton(
                      tooltip: _revealed ? 'Hide PIN' : 'Show PIN',
                      onPressed: () => setState(() => _revealed = !_revealed),
                      icon: Icon(_revealed ? Icons.visibility_off_rounded : Icons.visibility_rounded),
                    ),
                    IconButton(
                      tooltip: 'Show to a guest (full screen)',
                      onPressed: () => showPinFullScreen(context, settings.pin),
                      icon: const Icon(Icons.fullscreen_rounded),
                    ),
                  ],
                ),
                Text(
                  next == null
                      ? 'Changes only when someone changes it here.'
                      : 'Changes automatically at ${DateFormat.jm().format(next)}.',
                  style: theme.textTheme.bodyMedium,
                ),
              ],
            ),
          ),
        ),
        if (canManage) ...[
          const SizedBox(height: 16),
          Text('Change automatically', style: theme.textTheme.titleMedium),
          RadioGroup<int>(
            groupValue: _rotation ?? 0,
            onChanged: (value) => setState(() => _rotation = value == 0 ? null : value),
            child: const Column(
              children: [
                RadioListTile<int>(value: 120, title: Text('Every 2 hours')),
                RadioListTile<int>(value: 240, title: Text('Every 4 hours')),
                RadioListTile<int>(value: 360, title: Text('Every 6 hours')),
                RadioListTile<int>(value: 0, title: Text('Only when changed by hand')),
              ],
            ),
          ),
          const SizedBox(height: 8),
          Text('PIN length', style: theme.textTheme.titleMedium),
          const SizedBox(height: 8),
          SegmentedButton<int>(
            segments: const [
              ButtonSegment(value: 4, label: Text('4 digits')),
              ButtonSegment(value: 6, label: Text('6 digits')),
            ],
            selected: {_length},
            onSelectionChanged: (value) => setState(() => _length = value.single),
          ),
          if (_length != settings.pinLength)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text('Takes effect from the next PIN.', style: theme.textTheme.bodySmall),
            ),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: _changed && !widget.busy
                ? () => cubit.save(rotationMinutes: _rotation, pinLength: _length)
                : null,
            child: const Text('Save'),
          ),
          const Divider(height: 40),
          FilledButton.tonalIcon(
            key: const Key('rotate-now'),
            onPressed: widget.busy ? null : () => _confirmRotate(context, cubit),
            icon: const Icon(Icons.autorenew_rounded),
            label: const Text('Change PIN now'),
          ),
        ],
        if (canEndAll) ...[
          const SizedBox(height: 12),
          OutlinedButton.icon(
            key: const Key('end-all-sessions'),
            style: OutlinedButton.styleFrom(foregroundColor: theme.colorScheme.error),
            onPressed: widget.busy ? null : () => _confirmEndAll(context, cubit),
            icon: const Icon(Icons.phonelink_erase_rounded),
            label: const Text('Sign out every customer phone'),
          ),
          const SizedBox(height: 8),
          Text(
            'Use this if the PIN got out (for example, someone outside is ordering). Orders already '
            'placed are not affected.',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ],
    );
  }

  Future<void> _confirmRotate(BuildContext context, PinSettingsCubit cubit) async {
    var endSessions = false;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, setDialogState) => AlertDialog(
          title: const Text('Change the PIN now?'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('New guests will need the new PIN. Tell your staff.'),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: endSessions,
                onChanged: (value) => setDialogState(() => endSessions = value ?? false),
                title: const Text('Also sign out phones already ordering'),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Keep it')),
            FilledButton(
              onPressed: () => Navigator.pop(dialogContext, true),
              child: const Text('Change PIN'),
            ),
          ],
        ),
      ),
    );
    if (confirmed == true) await cubit.rotate(endSessions: endSessions);
  }

  Future<void> _confirmEndAll(BuildContext context, PinSettingsCubit cubit) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Sign out every customer phone?'),
        content: const Text(
          'Every guest who is ordering must scan the QR code and enter the PIN again. '
          'Consider changing the PIN as well if it got out.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Theme.of(dialogContext).colorScheme.error),
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Sign them out'),
          ),
        ],
      ),
    );
    if (confirmed == true) await cubit.endAllSessions();
  }
}
