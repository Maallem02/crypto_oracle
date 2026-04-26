import 'package:dio/dio.dart';
import '../../../core/storage/cache_storage.dart';

class SmcRepository {
  final Dio _dio = Dio(BaseOptions(
    baseUrl: 'http://127.0.0.1:8000',
    connectTimeout: const Duration(seconds: 15),
    receiveTimeout: const Duration(seconds: 15),
  ));

  Future<Map<String, dynamic>> getAnalysis(String symbol, String timeframe) async {
    try {
      final response = await _dio.get(
        '/smc/analysis/$symbol',
        queryParameters: {'timeframe': timeframe},
      );
      final data = response.data as Map<String, dynamic>;
      await CacheStorage.saveSmcAnalysis(symbol, timeframe, data);
      return data;
    } catch (_) {
      final cached = await CacheStorage.getSmcAnalysis(symbol, timeframe);
      if (cached != null) return cached;
      rethrow;
    }
  }

  Future<Map<String, dynamic>> getMultiTimeframe(String symbol) async {
    final response = await _dio.get('/smc/multi/$symbol');
    return response.data as Map<String, dynamic>;
  }
}