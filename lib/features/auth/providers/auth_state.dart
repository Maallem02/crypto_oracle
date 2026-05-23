import '../../../models/user_model.dart';

// ignore: depend_on_referenced_packages
import 'package:flutter/foundation.dart';

@immutable
class AuthState {
  final bool      isLoading;
  final UserModel? user;
  final String?   error;

  const AuthState({
    this.isLoading = false,
    this.user,
    this.error,
  });

  bool get isAuthenticated => user != null;

  AuthState copyWith({
    bool?      isLoading,
    UserModel? user,
    String?    error,
    bool       clearError = false,
    bool       clearUser  = false,
  }) =>
      AuthState(
        isLoading: isLoading ?? this.isLoading,
        user:      clearUser  ? null : user ?? this.user,
        error:     clearError ? null : error ?? this.error,
      );
}