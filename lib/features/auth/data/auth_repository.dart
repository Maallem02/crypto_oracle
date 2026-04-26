import 'package:dio/dio.dart';
import '../../../core/errors/app_exceptions.dart';
import '../../../core/storage/secure_storage.dart';
import '../../../models/user_model.dart';

class AuthRepository {
  final Dio _dio = Dio(BaseOptions(
    baseUrl: 'http://127.0.0.1:8000',
    connectTimeout: const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 10),
  ));

  Future<UserModel> register({
  required String username,
  required String email,
  required String password,
}) async {
  try {
    final response = await _dio.post('/auth/register', data: {
      'username': username,
      'email': email,
      'password': password,
    });
    final token = response.data['access_token'] as String;
    await SecureStorage.saveToken(token);
    await SecureStorage.saveUsername(username);
    return UserModel(
      id:    email.hashCode.toString(),
      email: email,
      token: token,
    );
  } on DioException catch (e) {
    final msg = e.response?.data?['detail'] ?? 'Registration failed';
    throw AuthException(msg);
  }
}

  Future<UserModel> login({
    required String email,
    required String password,
  }) async {
    try {
      final response = await _dio.post('/auth/login', data: {
        'email': email,
        'password': password,
      });
      final token    = response.data['access_token'] as String;
      final username = response.data['username'] as String;
      await SecureStorage.saveToken(token);
      await SecureStorage.saveUsername(username);
      return UserModel(
        id:    email.hashCode.toString(),
        email: email,
        token: token,
      );
    } on DioException catch (e) {
      final msg = e.response?.data?['detail'] ?? 'Login failed';
      throw AuthException(msg);
    }
  }

  Future<UserModel?> getMe() async {
    final token = await SecureStorage.getToken();
    if (token == null) return null;
    final username = await SecureStorage.getUsername() ?? '';
    return UserModel(id: '1', email: username, token: token);
  }

  Future<void> logout() async {
    await SecureStorage.clearAll();
  }
}