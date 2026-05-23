import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class SecureStorage {
  SecureStorage._();

  // The actual secure storage instance
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  // Keys - same idea as api_constants, avoid typos
  static const String _tokenKey    = 'jwt_token';
  static const String _usernameKey = 'username';

  // Save JWT token after login
  static Future<void> saveToken(String token) async {
    await _storage.write(key: _tokenKey, value: token);
  }

  // Read JWT token (sent with every API request)
  static Future<String?> getToken() async {
    return await _storage.read(key: _tokenKey);
  }

  // Delete token on logout
  static Future<void> deleteToken() async {
    await _storage.delete(key: _tokenKey);
  }

  // Save username for display
  static Future<void> saveUsername(String username) async {
    await _storage.write(key: _usernameKey, value: username);
  }

  // Read username
  static Future<String?> getUsername() async {
    return await _storage.read(key: _usernameKey);
  }

  // Clear everything on logout
  static Future<void> clearAll() async {
    await _storage.deleteAll();
  }
}