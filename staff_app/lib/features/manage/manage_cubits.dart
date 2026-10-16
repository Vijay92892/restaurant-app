import 'dart:async';

import 'package:equatable/equatable.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../../core/auth/staff_user.dart';
import '../../core/network/api_exception.dart';
import '../../core/realtime/live_updates.dart';
import '../problems/problem_reports.dart';
import 'manage_repositories.dart';

/// A screen's data, plus one-shot feedback. Shared by the manager screens.
class ManageState<T> extends Equatable {
  const ManageState({
    this.data,
    this.loading = true,
    this.busy = false,
    this.message,
    this.error,
    this.secret,
  });

  final T? data;
  final bool loading;

  /// A change is on its way to the server (its buttons are disabled meanwhile).
  final bool busy;

  /// One-shot feedback for a snackbar.
  final String? message;

  /// Loading failed.
  final String? error;

  /// A temporary password to show once, in a dialog (never stored).
  final ({String name, String password})? secret;

  ManageState<T> copyWith({
    T? data,
    bool? loading,
    bool? busy,
    String? message,
    String? error,
    ({String name, String password})? secret,
  }) => ManageState<T>(
    data: data ?? this.data,
    loading: loading ?? this.loading,
    busy: busy ?? this.busy,
    message: message,
    error: error,
    secret: secret,
  );

  @override
  List<Object?> get props => [data, loading, busy, message, error, secret];
}

abstract class _ManageCubit<T> extends Cubit<ManageState<T>> {
  _ManageCubit() : super(ManageState<T>());

  Future<T> fetch();

  Future<void> load() async {
    try {
      final data = await fetch();
      if (!isClosed) emit(state.copyWith(data: data, loading: false));
    } on ApiException catch (error) {
      if (!isClosed) emit(state.copyWith(loading: false, error: error.message));
    }
  }

  /// Runs one change; on failure says why and reloads (someone else may have changed it).
  Future<bool> act(Future<void> Function() change) async {
    if (state.busy) return false;
    emit(state.copyWith(busy: true));
    try {
      await change();
      return true;
    } on ApiException catch (error) {
      if (isClosed) return false;
      emit(state.copyWith(busy: false, message: error.message));
      await load();
      return false;
    }
  }
}

/// How guests open their table (owner and managers): the PIN and/or the location check.
class CustomerAccessCubit extends _ManageCubit<CustomerAccess> {
  CustomerAccessCubit(this._repository);

  final CustomerAccessRepository _repository;

  @override
  Future<CustomerAccess> fetch() => _repository.current();

  Future<bool> save(CustomerAccess access) => act(() async {
    final saved = await _repository.save(access);
    if (!isClosed) emit(state.copyWith(data: saved, busy: false, message: 'Customer access saved'));
  });
}

/// The table PIN screen (spec §35): see it, change it now, how often it changes, and ending
/// every customer session at once.
class PinSettingsCubit extends _ManageCubit<PinSettings> {
  PinSettingsCubit(this._repository, {this._live});

  final PinRepository _repository;
  final LiveUpdates? _live;
  StreamSubscription<LiveEvent>? _events;

  Future<void> start() async {
    _events = _live?.events.listen((event) {
      if (event.isResync || event.type == 'pin.rotated') load();
    });
    await load();
  }

  @override
  Future<PinSettings> fetch() => _repository.current();

  Future<void> rotate({required bool endSessions}) => act(() async {
    final (settings, ended) = await _repository.rotate(endSessions: endSessions);
    final sessions = endSessions ? ' $ended phone${ended == 1 ? '' : 's'} must enter it.' : '';
    if (!isClosed) {
      emit(state.copyWith(data: settings, busy: false, message: 'New PIN: ${settings.pin}.$sessions'));
    }
  });

  Future<void> save({required int? rotationMinutes, required int pinLength}) => act(() async {
    final settings = await _repository.save(rotationMinutes: rotationMinutes, pinLength: pinLength);
    if (!isClosed) emit(state.copyWith(data: settings, busy: false, message: 'PIN settings saved'));
  });

  Future<void> endAllSessions() => act(() async {
    final ended = await _repository.endAllSessions();
    if (!isClosed) {
      emit(
        state.copyWith(
          busy: false,
          message: ended == 0
              ? 'No phones were ordering'
              : '$ended phone${ended == 1 ? '' : 's'} signed out — guests enter the PIN again',
        ),
      );
    }
  });

  @override
  Future<void> close() async {
    await _events?.cancel();
    return super.close();
  }
}

/// Staff accounts: add people, change roles, switch accounts off, reset passwords.
class StaffCubit extends _ManageCubit<List<StaffMember>> {
  StaffCubit(this._repository, {String Function()? passwords}) : _passwords = passwords ?? temporaryPassword;

  final StaffRepository _repository;
  final String Function() _passwords;

  @override
  Future<List<StaffMember>> fetch() => _repository.list();

  String _passwordFor(String username) {
    var password = _passwords();
    while (password.contains(username)) {
      password = _passwords(); // the server refuses passwords containing the username
    }
    return password;
  }

  Future<bool> add({required String username, required String displayName, required StaffRole role}) =>
      act(() async {
        final password = _passwordFor(username);
        final member = await _repository.add(
          username: username,
          displayName: displayName,
          role: role,
          temporaryPassword: password,
        );
        if (isClosed) return;
        emit(
          state.copyWith(
            data: [...?state.data, member],
            busy: false,
            secret: (name: member.displayName, password: password),
          ),
        );
      });

  Future<bool> setRole(StaffMember member, StaffRole role) => _update(
    member,
    () => _repository.update(member.id, role: role),
    '${member.displayName} is now ${role.name}',
  );

  Future<bool> setActive(StaffMember member, {required bool active}) => _update(
    member,
    () => _repository.update(member.id, isActive: active),
    active
        ? '${member.displayName} can sign in again'
        : '${member.displayName} is switched off and signed out',
  );

  Future<bool> resetPassword(StaffMember member) => act(() async {
    final password = _passwordFor(member.username);
    await _repository.resetPassword(member.id, password);
    if (isClosed) return;
    final data = [
      for (final m in state.data ?? <StaffMember>[])
        m.id == member.id
            ? StaffMember(
                id: m.id,
                username: m.username,
                displayName: m.displayName,
                role: m.role,
                isActive: m.isActive,
                mustChangePassword: true,
                lastLoginAt: m.lastLoginAt,
              )
            : m,
    ];
    emit(state.copyWith(data: data, busy: false, secret: (name: member.displayName, password: password)));
  });

  Future<bool> _update(StaffMember member, Future<StaffMember> Function() call, String done) => act(() async {
    final updated = await call();
    if (isClosed) return;
    emit(
      state.copyWith(
        data: [for (final m in state.data ?? <StaffMember>[]) m.id == member.id ? updated : m],
        busy: false,
        message: done,
      ),
    );
  });
}

class ProblemReportsCubit extends _ManageCubit<List<ProblemReport>> {
  ProblemReportsCubit(this._repository);

  final ProblemReportsRepository _repository;

  @override
  Future<List<ProblemReport>> fetch() => _repository.recent();
}
