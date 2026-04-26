import 'package:dio/dio.dart';

class SmcRepository {
  final Dio _dio = Dio(BaseOptions(
    baseUrl: 'http://127.0.0.1:8000',
    connectTimeout: const Duration(seconds: 15),
    receiveTimeout: const Duration(seconds: 15),
  ));

  Future<Map<String, dynamic>> getAnalysis(String symbol, String timeframe) async {
    final response = await _dio.get(
      '/smc/analysis/$symbol',
      queryParameters: {'timeframe': timeframe},
    );
    return response.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getMultiTimeframe(String symbol) async {
    final response = await _dio.get('/smc/multi/$symbol');
    return response.data as Map<String, dynamic>;
  }
}