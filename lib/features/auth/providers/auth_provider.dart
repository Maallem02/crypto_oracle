import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/auth_repository.dart';
import '../../../models/user_model.dart';

final authRepositoryProvider = Provider<AuthRepository>((ref) => AuthRepository());

class AuthState {
  final UserModel? user;
  final bool       isLoading;
  final String?    error;
  const AuthState({this.user, this.isLoading = false, this.error});
  bool get isAuthenticated => user != null;
  AuthState copyWith({
    UserModel? user, bool? isLoading, String? error,
    bool clearUser = false, bool clearError = false,
  }) => AuthState(
    user:      clearUser  ? null : user      ?? this.user,
    isLoading: isLoading  ?? this.isLoading,
    error:     clearError ? null : error     ?? this.error,
  );
}

class AuthNotifier extends StateNotifier<AuthState> {
  final AuthRepository _repository;
  AuthNotifier(this._repository) : super(const AuthState()) {
    _checkAuthStatus();
  }

  Future<void> _checkAuthStatus() async {
    state = state.copyWith(isLoading: true);
    try {
      final user = await _repository.getMe();
      state = state.copyWith(user: user, isLoading: false);
    } catch (_) {
      state = state.copyWith(isLoading: false, clearUser: true);
    }
  }

  Future<bool> register({
  required String username,
  required String email,
  required String password,
}) async {
  state = state.copyWith(isLoading: true, clearError: true);
  try {
    final user = await _repository.register(username: username, email: email, password: password);
    state = state.copyWith(user: user, isLoading: false);
    return true;
  } catch (e) {
    state = state.copyWith(isLoading: false, error: e.toString());
    return false;
  }
}

  Future<bool> login({
    required String email,
    required String password,
  }) async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final user = await _repository.login(email: email, password: password);
      state = state.copyWith(user: user, isLoading: false);
      return true;
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
      return false;
    }
  }

  Future<void> logout() async {
    await _repository.logout();
    state = const AuthState();
  }
}

final authProvider = StateNotifierProvider<AuthNotifier, AuthState>((ref) {
  return AuthNotifier(ref.read(authRepositoryProvider));
});