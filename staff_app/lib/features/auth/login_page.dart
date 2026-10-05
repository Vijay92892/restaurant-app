import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../../core/auth/auth_bloc.dart';
import '../../core/network/api_client.dart';
import '../../core/network/api_exception.dart';

/// Asks the server to email a password-reset link (`POST /auth/password-reset/request`).
typedef ResetRequester = Future<void> Function(String restaurantCode, String email);

class LoginPage extends StatefulWidget {
  const LoginPage({super.key, this.rememberedCode, this.requestReset});

  final String? rememberedCode;

  /// Injected in tests; otherwise the app's API client.
  final ResetRequester? requestReset;

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _form = GlobalKey<FormState>();
  late final _code = TextEditingController(text: widget.rememberedCode ?? '');
  final _username = TextEditingController();
  final _password = TextEditingController();
  bool _obscure = true;

  @override
  void dispose() {
    _code.dispose();
    _username.dispose();
    _password.dispose();
    super.dispose();
  }

  void _submit() {
    if (!(_form.currentState?.validate() ?? false)) return;
    context.read<AuthBloc>().add(
      AuthLoginRequested(
        restaurantCode: _code.text.trim(),
        username: _username.text.trim(),
        password: _password.text,
      ),
    );
  }

  Future<void> _forgotPassword() async {
    final request =
        widget.requestReset ??
        (code, email) => context.read<ApiClient>().postJson<void>(
          '/auth/password-reset/request',
          data: {'restaurant_code': code, 'email': email},
        );
    await showDialog<void>(
      context: context,
      builder: (_) => _ForgotPasswordDialog(code: _code.text.trim().toLowerCase(), request: request),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AuthBloc>().state;
    final theme = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: AutofillGroup(
                child: Form(
                  key: _form,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Icon(Icons.restaurant_menu_rounded, size: 56, color: theme.colorScheme.primary),
                      const SizedBox(height: 12),
                      Text(
                        'Staff sign in',
                        textAlign: TextAlign.center,
                        style: theme.textTheme.headlineMedium,
                      ),
                      const SizedBox(height: 28),
                      TextFormField(
                        controller: _code,
                        decoration: const InputDecoration(
                          labelText: 'Restaurant code',
                          helperText: 'Given by your manager, e.g. spice-garden',
                          prefixIcon: Icon(Icons.storefront_rounded),
                        ),
                        textInputAction: TextInputAction.next,
                        autocorrect: false,
                        validator: (v) =>
                            (v == null || v.trim().length < 3) ? 'Enter the restaurant code' : null,
                      ),
                      const SizedBox(height: 16),
                      TextFormField(
                        controller: _username,
                        decoration: const InputDecoration(
                          labelText: 'Username',
                          prefixIcon: Icon(Icons.person_rounded),
                        ),
                        textInputAction: TextInputAction.next,
                        autocorrect: false,
                        autofillHints: const [AutofillHints.username],
                        validator: (v) => (v == null || v.trim().length < 3) ? 'Enter your username' : null,
                      ),
                      const SizedBox(height: 16),
                      TextFormField(
                        controller: _password,
                        obscureText: _obscure,
                        decoration: InputDecoration(
                          labelText: 'Password',
                          prefixIcon: const Icon(Icons.lock_rounded),
                          suffixIcon: IconButton(
                            tooltip: _obscure ? 'Show password' : 'Hide password',
                            icon: Icon(_obscure ? Icons.visibility_rounded : Icons.visibility_off_rounded),
                            onPressed: () => setState(() => _obscure = !_obscure),
                          ),
                        ),
                        autofillHints: const [AutofillHints.password],
                        onFieldSubmitted: (_) => _submit(),
                        validator: (v) => (v == null || v.isEmpty) ? 'Enter your password' : null,
                      ),
                      if (state.message != null) ...[
                        const SizedBox(height: 16),
                        Text(
                          state.message!,
                          key: const Key('login-error'),
                          style: TextStyle(color: theme.colorScheme.error, fontWeight: FontWeight.w600),
                        ),
                      ],
                      const SizedBox(height: 24),
                      FilledButton(
                        onPressed: state.busy ? null : _submit,
                        child: Text(state.busy ? 'Signing in…' : 'Sign in'),
                      ),
                      const SizedBox(height: 8),
                      TextButton(onPressed: _forgotPassword, child: const Text('Forgot password?')),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Owners and managers with an email address get a reset link; everyone else asks a manager,
/// who resets it in the app. The server answers the same whether or not the email matches.
class _ForgotPasswordDialog extends StatefulWidget {
  const _ForgotPasswordDialog({required this.code, required this.request});

  final String code;
  final ResetRequester request;

  @override
  State<_ForgotPasswordDialog> createState() => _ForgotPasswordDialogState();
}

class _ForgotPasswordDialogState extends State<_ForgotPasswordDialog> {
  final _form = GlobalKey<FormState>();
  late final _code = TextEditingController(text: widget.code);
  final _email = TextEditingController();
  bool _sending = false;
  bool _sent = false;
  String? _error;

  @override
  void dispose() {
    _code.dispose();
    _email.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      await widget.request(_code.text.trim().toLowerCase(), _email.text.trim().toLowerCase());
      if (mounted) setState(() => _sent = true);
    } on ApiException catch (error) {
      if (mounted) {
        setState(() {
          _sending = false;
          _error = error.message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_sent) {
      return AlertDialog(
        title: const Text('Check your email'),
        content: Text(
          'If ${_email.text.trim()} belongs to an account at ${_code.text.trim()}, a link to choose a new '
          'password is on its way. It works for one hour.',
        ),
        actions: [FilledButton(onPressed: () => Navigator.pop(context), child: const Text('OK'))],
      );
    }
    return AlertDialog(
      title: const Text('Forgot your password?'),
      content: Form(
        key: _form,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Waiters and kitchen staff: ask your manager — they can reset it in the app.\n\n'
              'Owners and managers: we email a link to the address on your account.',
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _code,
              decoration: const InputDecoration(labelText: 'Restaurant code'),
              autocorrect: false,
              validator: (v) => (v == null || v.trim().length < 3) ? 'Enter the restaurant code' : null,
            ),
            TextFormField(
              controller: _email,
              decoration: const InputDecoration(labelText: 'Email address'),
              keyboardType: TextInputType.emailAddress,
              autocorrect: false,
              validator: (v) => RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch((v ?? '').trim())
                  ? null
                  : 'Enter your email address',
            ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(onPressed: _sending ? null : _send, child: Text(_sending ? 'Sending…' : 'Send link')),
      ],
    );
  }
}
