import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../../core/auth/auth_bloc.dart';

/// Shown when an account must change its temporary password before doing anything else
/// (the server enforces this too: other endpoints answer `password_change_required`).
class ChangePasswordPage extends StatefulWidget {
  const ChangePasswordPage({super.key});

  @override
  State<ChangePasswordPage> createState() => _ChangePasswordPageState();
}

class _ChangePasswordPageState extends State<ChangePasswordPage> {
  final _form = GlobalKey<FormState>();
  final _current = TextEditingController();
  final _next = TextEditingController();
  final _again = TextEditingController();

  @override
  void dispose() {
    _current.dispose();
    _next.dispose();
    _again.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AuthBloc>().state;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Choose a new password'),
        actions: [
          TextButton(
            onPressed: () => context.read<AuthBloc>().add(const AuthLogoutRequested()),
            child: const Text('Sign out'),
          ),
        ],
      ),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: Form(
              key: _form,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text('Your manager gave you a temporary password. Please choose your own.'),
                  const SizedBox(height: 20),
                  TextFormField(
                    controller: _current,
                    obscureText: true,
                    decoration: const InputDecoration(labelText: 'Temporary password'),
                    validator: (v) => (v == null || v.isEmpty) ? 'Enter the temporary password' : null,
                  ),
                  const SizedBox(height: 16),
                  TextFormField(
                    controller: _next,
                    obscureText: true,
                    decoration: const InputDecoration(
                      labelText: 'New password',
                      helperText: 'At least 8 characters; not your username',
                    ),
                    validator: (v) => (v == null || v.length < 8) ? 'Use at least 8 characters' : null,
                  ),
                  const SizedBox(height: 16),
                  TextFormField(
                    controller: _again,
                    obscureText: true,
                    decoration: const InputDecoration(labelText: 'New password again'),
                    validator: (v) => v != _next.text ? 'The passwords do not match' : null,
                  ),
                  if (state.message != null) ...[
                    const SizedBox(height: 16),
                    Text(state.message!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                  ],
                  const SizedBox(height: 24),
                  FilledButton(
                    onPressed: state.busy
                        ? null
                        : () {
                            if (_form.currentState?.validate() ?? false) {
                              context.read<AuthBloc>().add(
                                AuthPasswordChangeRequested(
                                  currentPassword: _current.text,
                                  newPassword: _next.text,
                                ),
                              );
                            }
                          },
                    child: Text(state.busy ? 'Saving…' : 'Save password'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
