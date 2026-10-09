import 'package:shared_preferences/shared_preferences.dart';
import '../constants/api_constants.dart';

/// Runtime-configurable backend URL.
///
/// The URL used to live only in [ApiConstants.baseUrl], a compile-time const.
/// Every time the tunnel handed out a new address the app had to be edited and
/// both build scripts re-run — and until that happened every request failed
/// with a DioException, including login, which made the app unusable rather
/// than merely stale.
///
/// The value is stored in SharedPreferences and loaded before the first frame.
/// [ApiConstants.baseUrl] remains the default for a fresh install.
class ApiConfig {
  ApiConfig._();

  static const _prefsKey = 'backend_base_url';

  static String _baseUrl = ApiConstants.baseUrl;

  /// Current backend URL. Always safe to read — falls back to the compiled
  /// default until [load] completes.
  static String get baseUrl => _baseUrl;

  /// True when the user has overridden the compiled default.
  static bool get isCustom => _baseUrl != ApiConstants.baseUrl;

  /// Read the saved URL. Call once from main() before runApp().
  static Future<void> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getString(_prefsKey);
      if (saved != null && saved.trim().isNotEmpty) {
        _baseUrl = _normalise(saved);
      }
    } catch (_) {
      // A broken prefs store must not stop the app booting — the compiled
      // default still works.
    }
  }

  /// Persist a new URL and rebuild the shared Dio so it takes effect at once.
  static Future<void> setBaseUrl(String url) async {
    _baseUrl = _normalise(url);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_prefsKey, _baseUrl);
    } catch (_) {
      // Keep the in-memory value even if persistence fails — the user can
      // still work this session.
    }
  }

  /// Drop the override and go back to the compiled default.
  static Future<void> reset() async {
    _baseUrl = ApiConstants.baseUrl;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_prefsKey);
    } catch (_) {}
  }

  /// Tolerate what people actually paste: a bare host, a trailing slash, or a
  /// copied URL with a path on the end.
  static String _normalise(String raw) {
    var s = raw.trim();
    if (s.isEmpty) return ApiConstants.baseUrl;
    if (!s.startsWith('http://') && !s.startsWith('https://')) {
      // Plain IPs and .local names are LAN targets and have no TLS.
      final looksLocal = RegExp(r'^(\d{1,3}\.){3}\d{1,3}(:\d+)?$').hasMatch(s) ||
          s.startsWith('localhost') ||
          s.endsWith('.local');
      s = (looksLocal ? 'http://' : 'https://') + s;
    }
    while (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    return s;
  }
}
