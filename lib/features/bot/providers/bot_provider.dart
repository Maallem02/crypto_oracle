import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/api_constants.dart';

class BotState {
  final bool isRunning;
  final Map<String, dynamic>? account;
  final double riskPercent;
  final double minConfidence;
  final String? lastScan;
  final int tradesToday;
  final String? error;

  const BotState({
    this.isRunning     = false,
    this.account,
    this.riskPercent   = 1.0,
    this.minConfidence = 0.75,
    this.lastScan,
    this.tradesToday   = 0,
    this.error,
  });

  BotState copyWith({
    bool? isRunning,
    Map<String, dynamic>? account,
    double? riskPercent,
    double? minConfidence,
    String? lastScan,
    int? tradesToday,
    String? error,
  }) => BotState(
    isRunning:     isRunning     ?? this.isRunning,
    account:       account       ?? this.account,
    riskPercent:   riskPercent   ?? this.riskPercent,
    minConfidence: minConfidence ?? this.minConfidence,
    lastScan:      lastScan      ?? this.lastScan,
    tradesToday:   tradesToday   ?? this.tradesToday,
    error:         error,
  );
}

class BotNotifier extends StateNotifier<BotState> {
  final Dio _dio = Dio(BaseOptions(baseUrl: ApiConstants.baseUrl));
  BotNotifier() : super(const BotState()) {
    _connectMT5();
  }

  Future<void> _connectMT5() async {
    try {
      final response = await _dio.get('/trading/connect');
      state = state.copyWith(account: response.data['account']);
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> refreshStatus() async {
    try {
      final response = await _dio.get('/trading/bot/status');
      final data     = response.data;
      state = state.copyWith(
        isRunning:   data['running'],
        lastScan:    data['last_scan'],
        tradesToday: data['trades_today'],
      );
    } catch (_) {}
  }

  Future<void> start() async {
    try {
      await _dio.post('/trading/bot/start', data: {
        'risk_percent':       state.riskPercent,
        'min_confidence':     state.minConfidence,
        'max_trades':         3,
        'enabled_symbols':    ['BTC', 'XAUUSD', 'GBPJPY'],
        'enabled_timeframes': ['15m', '1h'],
      });
      state = state.copyWith(isRunning: true);
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> stop() async {
    await _dio.post('/trading/bot/stop');
    state = state.copyWith(isRunning: false);
  }

  Future<void> scan() async {
    await _dio.get('/trading/bot/scan');
    await refreshStatus();
  }

  void setRisk(double v)          => state = state.copyWith(riskPercent: v);
  void setMinConfidence(double v) => state = state.copyWith(minConfidence: v);
}

final botProvider = StateNotifierProvider<BotNotifier, BotState>(
  (ref) => BotNotifier());

// Polling trades ouverts toutes les 10 secondes
final openTradesProvider = StreamProvider.autoDispose<List>((ref) async* {
  final dio = Dio(BaseOptions(baseUrl: ApiConstants.baseUrl));
  while (true) {
    try {
      final response = await dio.get('/trading/trades/open');
      yield response.data['trades'] as List;
    } catch (_) {
      yield [];
    }
    await Future.delayed(const Duration(seconds: 10));
  }
});

// Historique des trades
final tradeHistoryProvider = FutureProvider.autoDispose<List>((ref) async {
  final dio      = Dio(BaseOptions(baseUrl: ApiConstants.baseUrl));
  final response = await dio.get('/trading/bot/history');
  return response.data['history'] as List;
});