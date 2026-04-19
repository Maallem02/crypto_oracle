import 'package:dio/dio.dart';
import '../../../core/network/dio_client.dart';
import '../../../core/constants/api_constants.dart';
import '../../../core/storage/secure_storage.dart';
import '../../../core/errors/app_exceptions.dart';
import '../../../models/user_model.dart';

class AuthRepository {
  final Dio _dio = DioClient.instance;

  // ─── Register ────────────────────────────────────────────────────────────
  Future<void> register({
    required String username,
    required String email,
    required String password,
  }) async {
    try {
      await _dio.post(
        ApiConstants.register,
        data: {
          'username': username,
          'email':    email,
          'password': password,
        },
      );
    } on DioException catch (e) {
      throw e.error as AppException;
    }
  }

  // ─── Login ───────────────────────────────────────────────────────────────
  Future<UserModel> login({
    required String email,
    required String password,
  }) async {
    try {
      final response = await _dio.post(
        ApiConstants.login,
        data: {
          'email':    email,
          'password': password,
        },
      );

      // Save JWT token securely
      final token = response.data['access_token'] as String;
      await SecureStorage.saveToken(token);

      // Return user info
      return UserModel.fromJson(response.data['user']);
    } on DioException catch (e) {
      throw e.error as AppException;
    }
  }

  // ─── Get current user (called on app start) ──────────────────────────────
  Future<UserModel?> getMe() async {
    try {
      final token = await SecureStorage.getToken();
      if (token == null) return null; // not logged in

      final response = await _dio.get(ApiConstants.me);
      return UserModel.fromJson(response.data);
    } on DioException catch (e) {
      // Token expired or invalid
      await SecureStorage.clearAll();
      throw e.error as AppException;
    }
  }

  // ─── Logout ──────────────────────────────────────────────────────────────
  Future<void> logout() async {
    await SecureStorage.clearAll();
  }
}