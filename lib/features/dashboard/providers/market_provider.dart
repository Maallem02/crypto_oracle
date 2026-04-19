import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/market_repository.dart';
import '../../../models/prediction_model.dart';

// ── Repository ───────────────────────────────────────────
final marketRepositoryProvider = Provider((ref) => MarketRepository());

// ── Live market data (CoinGecko) ─────────────────────────
final marketsProvider = FutureProvider<List<Map<String, dynamic>>>((ref) async {
  return ref.watch(marketRepositoryProvider).getMarkets();
});

// ── Mock LSTM predictions ─────────────────────────────────
final predictionsProvider = Provider<List<PredictionModel>>((ref) {
  final now = DateTime.now();
  return [
    PredictionModel(
      symbol: 'BTCUSDT', assetType: 'crypto', timeframe: '1h',
      currentPrice: 84000, predictedPrice: 85260,
      changePercent: 1.5, direction: 'up', confidence: 0.82,
      generatedAt: now,
    ),
    PredictionModel(
      symbol: 'ETHUSDT', assetType: 'crypto', timeframe: '1h',
      currentPrice: 1600, predictedPrice: 1572,
      changePercent: -1.75, direction: 'down', confidence: 0.74,
      generatedAt: now,
    ),
    PredictionModel(
      symbol: 'BNBUSDT', assetType: 'crypto', timeframe: '4h',
      currentPrice: 590, predictedPrice: 601.8,
      changePercent: 2.0, direction: 'up', confidence: 0.68,
      generatedAt: now,
    ),
    PredictionModel(
      symbol: 'SOLUSDT', assetType: 'crypto', timeframe: '1h',
      currentPrice: 131, predictedPrice: 128.4,
      changePercent: -2.0, direction: 'down', confidence: 0.71,
      generatedAt: now,
    ),
    PredictionModel(
      symbol: 'XRPUSDT', assetType: 'crypto', timeframe: '4h',
      currentPrice: 2.1, predictedPrice: 2.163,
      changePercent: 3.0, direction: 'up', confidence: 0.77,
      generatedAt: now,
    ),
  ];
});