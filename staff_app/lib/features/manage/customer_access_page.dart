import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import 'manage_cubits.dart';
import 'manage_repositories.dart';

/// Restaurant settings → Customer access (owner and managers): the table PIN, and/or letting
/// guests at the restaurant order without it after a location check.
class CustomerAccessPage extends StatelessWidget {
  const CustomerAccessPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Customer access')),
      body: BlocConsumer<CustomerAccessCubit, ManageState<CustomerAccess>>(
        listenWhen: (a, b) => b.message != null && a.message != b.message,
        listener: (context, state) => ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text(state.message!))),
        builder: (context, state) {
          final saved = state.data;
          if (saved == null) {
            if (state.loading) return const Center(child: CircularProgressIndicator());
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(state.error ?? 'Could not load the settings.', textAlign: TextAlign.center),
                    const SizedBox(height: 12),
                    FilledButton(
                      onPressed: context.read<CustomerAccessCubit>().load,
                      child: const Text('Try again'),
                    ),
                  ],
                ),
              ),
            );
          }
          return _AccessForm(saved: saved, busy: state.busy);
        },
      ),
    );
  }
}

class _AccessForm extends StatefulWidget {
  const _AccessForm({required this.saved, required this.busy});

  final CustomerAccess saved;
  final bool busy;

  @override
  State<_AccessForm> createState() => _AccessFormState();
}

class _AccessFormState extends State<_AccessForm> {
  final _form = GlobalKey<FormState>();
  final _point = TextEditingController();
  late bool _location;
  late bool _fallback;
  late int _radius;

  @override
  void initState() {
    super.initState();
    _reset();
  }

  @override
  void didUpdateWidget(_AccessForm old) {
    super.didUpdateWidget(old);
    if (old.saved != widget.saved) _reset(); // saved (or reloaded): show what the server has
  }

  void _reset() {
    final saved = widget.saved;
    _location = saved.locationEnabled;
    _fallback = saved.pinFallback;
    _radius = saved.radiusM;
    _point.text = saved.hasPoint ? formatCoordinates(saved.latitude!, saved.longitude!) : '';
  }

  @override
  void dispose() {
    _point.dispose();
    super.dispose();
  }

  String? _checkPoint(String? text) {
    final value = (text ?? '').trim();
    if (value.isEmpty) return _location ? "Enter the restaurant's location first" : null;
    return parseCoordinates(value) == null
        ? 'Paste the two numbers from the map, e.g. 28.2449519, 76.8135882'
        : null;
  }

  Future<void> _save() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    final point = parseCoordinates(_point.text);
    await context.read<CustomerAccessCubit>().save(
      CustomerAccess(
        locationEnabled: _location,
        latitude: point?.$1,
        longitude: point?.$2,
        radiusM: _radius,
        pinFallback: _fallback,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final radii = {...CustomerAccess.radii, _radius}.toList()..sort();
    return Form(
      key: _form,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
        children: [
          Card(
            child: ListTile(
              key: const Key('access-summary'),
              leading: Icon(
                widget.saved.locationEnabled ? Icons.location_on_rounded : Icons.pin_rounded,
                color: theme.colorScheme.primary,
              ),
              title: const Text('Now'),
              subtitle: Text(widget.saved.summary),
            ),
          ),
          const SizedBox(height: 8),
          SwitchListTile(
            key: const Key('location-switch'),
            title: const Text('Guests at the restaurant order without a PIN'),
            subtitle: const Text('Their phone shares its location once to confirm they are here'),
            value: _location,
            onChanged: widget.busy ? null : (value) => setState(() => _location = value),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: TextFormField(
              key: const Key('location-point'),
              controller: _point,
              enabled: !widget.busy,
              keyboardType: const TextInputType.numberWithOptions(signed: true, decimal: true),
              decoration: const InputDecoration(
                labelText: 'Restaurant location',
                hintText: '28.2449519, 76.8135882',
                helperText:
                    'In Google Maps, press and hold on your restaurant and copy the two numbers. '
                    'Check the pin is on your building, not the road.',
                helperMaxLines: 3,
              ),
              validator: _checkPoint,
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: DropdownButtonFormField<int>(
              key: const Key('location-radius'),
              initialValue: _radius,
              decoration: const InputDecoration(
                labelText: 'Allowed distance from that point',
                helperText: 'Enough to cover the dining area and seating outside',
              ),
              items: [for (final r in radii) DropdownMenuItem(value: r, child: Text('$r m'))],
              onChanged: widget.busy ? null : (value) => setState(() => _radius = value ?? _radius),
            ),
          ),
          SwitchListTile(
            key: const Key('fallback-switch'),
            title: const Text('Allow the table PIN as a backup'),
            subtitle: Text(
              _location
                  ? "For guests whose phone can't confirm the location"
                  : 'The PIN is always used while the location check is off',
            ),
            value: _location ? _fallback : true,
            onChanged: widget.busy || !_location ? null : (value) => setState(() => _fallback = value),
          ),
          if (_location && !_fallback)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              child: Text(
                "Guests whose phone can't confirm the location will have to ask a waiter to order.",
                style: TextStyle(color: theme.colorScheme.error),
              ),
            ),
          const SizedBox(height: 16),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: FilledButton(
              key: const Key('save-access'),
              onPressed: widget.busy ? null : _save,
              child: Text(widget.busy ? 'Saving…' : 'Save'),
            ),
          ),
          const SizedBox(height: 20),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Text(
              "A phone's location is what the phone reports, and someone determined can fake it. "
              'Use this for convenience; waiters and the table PIN stay the safe way in. Phones '
              'must report their position to within ${widget.saved.maxAccuracyM} m. Turning this '
              'off stops new PIN-free sessions at once; tables already seated keep ordering until '
              'their session ends (or you sign out every phone on the Table PIN screen).',
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          ),
        ],
      ),
    );
  }
}
