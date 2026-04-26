import 'package:shared_preferences/shared_preferences.dart';
import 'dart:convert';

class CacheStorage {
  CacheStorage._();

  static Future<void> saveMarkets(List<Map<String, dynamic>> data) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('cached_markets', jsonEncode(data));
    await prefs.setInt('cached_markets_ts', DateTime.now().millisecondsSinceEpoch);
  }

  static Future<List<Map<String, dynamic>>?> getMarkets() async {
    final prefs = await SharedPreferences.getInstance();
    final raw   = prefs.getString('cached_markets');
    if (raw == null) return null;
    final ts    = prefs.getInt('cached_markets_ts') ?? 0;
    final age   = DateTime.now().millisecondsSinceEpoch - ts;
    if (age > 5 * 60 * 1000) return null; // expire après 5 min
    return List<Map<String, dynamic>>.from(jsonDecode(raw));
  }

  static Future<void> saveSmcAnalysis(String symbol, String tf, Map<String, dynamic> data) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('smc_${symbol}_$tf', jsonEncode(data));
    await prefs.setInt('smc_${symbol}_${tf}_ts', DateTime.now().millisecondsSinceEpoch);
  }

  static Future<Map<String, dynamic>?> getSmcAnalysis(String symbol, String tf) async {
    final prefs = await SharedPreferences.getInstance();
    final raw   = prefs.getString('smc_${symbol}_$tf');
    if (raw == null) return null;
    final ts    = prefs.getInt('smc_${symbol}_${tf}_ts') ?? 0;
    final age   = DateTime.now().millisecondsSinceEpoch - ts;
    if (age > 15 * 60 * 1000) return null; // expire après 15 min
    return Map<String, dynamic>.from(jsonDecode(raw));
  }
}