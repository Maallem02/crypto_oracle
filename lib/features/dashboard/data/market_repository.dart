import '../../../core/constants/api_constants.dart';
import '../../../core/network/dio_client.dart';
import '../../../core/storage/cache_storage.dart';

class MarketRepository {
  final _dio = DioClient.createExternal(baseUrl: ApiConstants.coinGeckoBase);

  Future<List<Map<String, dynamic>>> getMarkets() async {
    try {
      final response = await _dio.get(
        ApiConstants.markets,
        queryParameters: {
          'vs_currency':             'usd',
          'ids':                     'bitcoin,ethereum,binancecoin,solana,ripple',
          'order':                   'market_cap_desc',
          'per_page':                5,
          'page':                    1,
          'sparkline':               true,
          'price_change_percentage': '24h',
        },
      );
      final data = List<Map<String, dynamic>>.from(response.data);
      await CacheStorage.saveMarkets(data); // save to cache
      return data;
    } catch (_) {
      // fallback to cache if offline
      final cached = await CacheStorage.getMarkets();
      if (cached != null) return cached;
      rethrow;
    }
  }
}