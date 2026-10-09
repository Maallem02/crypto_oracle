import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Token storage.
///
/// Native platforms use flutter_secure_storage (Keychain / Keystore / DPAPI).
///
/// WEB uses SharedPreferences (localStorage) instead. flutter_secure_storage's
/// web backend encrypts through the Web Crypto API, and browsers only expose
/// `window.crypto.subtle` on a SECURE ORIGIN — https, or localhost. Served
/// from http://100.89.32.70:5500 the app is an insecure origin, so
/// crypto.subtle is undefined and the package throws
/// "Null check operator used on a null value" the moment a token is written —
/// which is why login failed on the phone but worked in the desktop build and
/// worked previously over the https tunnel.
///
/// The token therefore sits in localStorage on web, unencrypted. That is
/// acceptable here: the app is only reachable inside the tailnet, and the
/// previous setup put the same token behind a public URL. If you later serve
/// the web app over https (see `tailscale serve`), this file can go back to
/// using secure storage on web too — nothing else needs to change.
class SecureStorage {
  SecureStorage._();

  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  static const String _tokenKey    = 'jwt_token';
  static const String _usernameKey = 'username';

  static Future<void> _write(String key, String value) async {
    if (kIsWeb) {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(key, value);
    } else {
      await _storage.write(key: key, value: value);
    }
  }

  static Future<String?> _read(String key) async {
    if (kIsWeb) {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getString(key);
    }
    return _storage.read(key: key);
  }

  static Future<void> _delete(String key) async {
    if (kIsWeb) {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(key);
    } else {
      await _storage.delete(key: key);
    }
  }

  // Save JWT token after login
  static Future<void> saveToken(String token) => _write(_tokenKey, token);

  // Read JWT token (sent with every API request)
  static Future<String?> getToken() => _read(_tokenKey);

  // Delete token on logout
  static Future<void> deleteToken() => _delete(_tokenKey);

  // Save username for display
  static Future<void> saveUsername(String username) =>
      _write(_usernameKey, username);

  // Read username
  static Future<String?> getUsername() => _read(_usernameKey);

  // Clear everything on logout
  static Future<void> clearAll() async {
    if (kIsWeb) {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_tokenKey);
      await prefs.remove(_usernameKey);
    } else {
      await _storage.deleteAll();
    }
  }
}
