import 'package:flutter/material.dart';

/// The table PIN, full screen and huge, to show a guest across the table (spec §35: the
/// restaurant may display the PIN in the restaurant). Tap anywhere to close.
Future<void> showPinFullScreen(BuildContext context, String pin) {
  return Navigator.of(context)
      .push<void>(MaterialPageRoute(fullscreenDialog: true, builder: (_) => _PinFullScreen(pin: pin)));
}

class _PinFullScreen extends StatelessWidget {
  const _PinFullScreen({required this.pin});

  final String pin;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      backgroundColor: theme.colorScheme.surface,
      body: SafeArea(
        child: InkWell(
          onTap: () => Navigator.pop(context),
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text('Table PIN', style: theme.textTheme.headlineSmall),
                  const SizedBox(height: 16),
                  FittedBox(
                    child: Text(
                      pin.split('').join(' '),
                      key: const Key('pin-full-screen'),
                      semanticsLabel: 'Table PIN ${pin.split('').join(' ')}',
                      style: theme.textTheme.displayLarge?.copyWith(
                        fontWeight: FontWeight.w900,
                        fontSize: 120,
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text('Enter it on the ordering page', style: theme.textTheme.titleMedium),
                  const SizedBox(height: 32),
                  Text('Tap anywhere to close', style: theme.textTheme.bodySmall),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
