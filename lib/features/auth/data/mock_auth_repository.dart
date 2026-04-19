import 'package:crypto_oracle/core/errors/app_exceptions.dart';
import '../../../core/storage/secure_storage.dart';
import '../../../models/user_model.dart';

// Fake in-memory "database"
final _users = <Map<String, String>>[];

class MockAuthRepository {
  // ─── Register ────────────────────────────────────────────────────────────
  Future<void> register({
    required String username,
    required String email,
    required String password,
  }) async {
    await Future.delayed(const Duration(seconds: 1)); // simulate network

    final exists = _users.any((u) => u['email'] == email);
    if (exists) throw const AuthException('Email already registered.');

    _users.add({'username': username, 'email': email, 'password': password});
  }

  // ─── Login ───────────────────────────────────────────────────────────────
  Future<UserModel> login({
    required String email,
    required String password,
  }) async {
    await Future.delayed(const Duration(seconds: 1));

    final user = _users.firstWhere(
      (u) => u['email'] == email && u['password'] == password,
      orElse: () => {},
    );

    if (user.isEmpty) throw const AuthException('Invalid email or password.');

    const fakeToken = 'mock_jwt_token_123';
    await SecureStorage.saveToken(fakeToken);
    await SecureStorage.saveUsername(user['username']!);

    return UserModel(
      id:    email.hashCode.toString(),
      email: email,
      token: fakeToken,
    );
  }

  // ─── Get current user ────────────────────────────────────────────────────
  Future<UserModel?> getMe() async {
    final token = await SecureStorage.getToken();
    if (token == null) return null;

    final username = await SecureStorage.getUsername() ?? 'User';
    return UserModel(
      id:    '1',
      email: username,
      token: token,
    );
  }

  // ─── Logout ──────────────────────────────────────────────────────────────
  Future<void> logout() async {
    await SecureStorage.clearAll();
  }
}