import 'package:equatable/equatable.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../network/api_client.dart';
import '../network/api_exception.dart';
import 'staff_user.dart';

// --- events ----------------------------------------------------------------------------------

sealed class AuthEvent {
  const AuthEvent();
}

class AuthStarted extends AuthEvent {
  const AuthStarted();
}

class AuthLoginRequested extends AuthEvent {
  const AuthLoginRequested({required this.restaurantCode, required this.username, required this.password});
  final String restaurantCode;
  final String username;
  final String password;
}

class AuthPasswordChangeRequested extends AuthEvent {
  const AuthPasswordChangeRequested({required this.currentPassword, required this.newPassword});
  final String currentPassword;
  final String newPassword;
}

class AuthLogoutRequested extends AuthEvent {
  const AuthLogoutRequested();
}

class AuthSessionExpired extends AuthEvent {
  const AuthSessionExpired();
}

class AuthUserUpdated extends AuthEvent {
  const AuthUserUpdated(this.user);
  final StaffUser user;
}

// --- state -----------------------------------------------------------------------------------

enum AuthStatus { unknown, unauthenticated, authenticated }

class AuthState extends Equatable {
  const AuthState({this.status = AuthStatus.unknown, this.user, this.busy = false, this.message});

  final AuthStatus status;
  final StaffUser? user;
  final bool busy;

  /// A user-facing message (login failed, session expired…).
  final String? message;

  AuthState copyWith({AuthStatus? status, StaffUser? user, bool? busy, String? message}) => AuthState(
    status: status ?? this.status,
    user: user ?? this.user,
    busy: busy ?? this.busy,
    message: message,
  );

  @override
  List<Object?> get props => [status, user, busy, message];
}

// --- bloc ------------------------------------------------------------------------------------

class AuthBloc extends Bloc<AuthEvent, AuthState> {
  AuthBloc(this._api) : super(const AuthState()) {
    _api.onSessionExpired = () => add(const AuthSessionExpired());
    _api.onUserUpdated = (user) => add(AuthUserUpdated(user));
    on<AuthStarted>(_onStarted);
    on<AuthLoginRequested>(_onLogin);
    on<AuthPasswordChangeRequested>(_onPasswordChange);
    on<AuthLogoutRequested>(_onLogout);
    on<AuthSessionExpired>(_onExpired);
    on<AuthUserUpdated>(_onUserUpdated);
  }

  final ApiClient _api;

  Future<void> _onStarted(AuthStarted event, Emitter<AuthState> emit) async {
    // Offline, the user saved at sign-in is used until the server confirms (or ends) the session.
    final restored = await _api.restore();
    emit(
      restored == null
          ? const AuthState(status: AuthStatus.unauthenticated)
          : AuthState(status: AuthStatus.authenticated, user: restored.user),
    );
  }

  Future<void> _onLogin(AuthLoginRequested event, Emitter<AuthState> emit) async {
    emit(state.copyWith(busy: true));
    try {
      final user = await _api.login(
        restaurantCode: event.restaurantCode,
        username: event.username,
        password: event.password,
      );
      emit(AuthState(status: AuthStatus.authenticated, user: user));
    } on ApiException catch (error) {
      emit(AuthState(status: AuthStatus.unauthenticated, message: _loginMessage(error)));
    }
  }

  String _loginMessage(ApiException error) => switch (error.code) {
    'invalid_credentials' => 'The restaurant code, username or password is not right.',
    'rate_limited' => 'Too many attempts. Please wait a few minutes and try again.',
    'validation_error' => 'Please check the restaurant code and username.',
    _ => error.message,
  };

  Future<void> _onPasswordChange(AuthPasswordChangeRequested event, Emitter<AuthState> emit) async {
    emit(state.copyWith(busy: true));
    try {
      await _api.postJson<void>(
        '/auth/change-password',
        data: {'current_password': event.currentPassword, 'new_password': event.newPassword},
      );
      emit(state.copyWith(busy: false, user: state.user?.copyWith(mustChangePassword: false)));
    } on ApiException catch (error) {
      emit(state.copyWith(busy: false, message: error.message));
    }
  }

  Future<void> _onLogout(AuthLogoutRequested event, Emitter<AuthState> emit) async {
    await _api.logout();
    emit(const AuthState(status: AuthStatus.unauthenticated));
  }

  Future<void> _onExpired(AuthSessionExpired event, Emitter<AuthState> emit) async {
    if (state.status != AuthStatus.authenticated) return;
    await _api.clear();
    emit(
      const AuthState(
        status: AuthStatus.unauthenticated,
        message: 'You were signed out. Please sign in again.',
      ),
    );
  }

  void _onUserUpdated(AuthUserUpdated event, Emitter<AuthState> emit) {
    if (state.status == AuthStatus.authenticated) emit(state.copyWith(user: event.user));
  }
}
