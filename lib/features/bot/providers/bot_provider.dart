import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class BotState {
  final bool isRunning;
  final Map<String, dynamic>? account;
  final double riskPercent;
  final double minConfidence;
  final String? error;

  const BotState({
    this.isRunning     = false,
    this.account,
    this.riskPercent   = 1.0,
    this.minConfidence = 0.75,
    this.error,
  });

  BotState copyWith({
    bool? isRunning,
    Map<String, dynamic>? account,
    double? riskPercent,
    double? minConfidence,
    String? error,
  }) => BotState(
    isRunning:     isRunning     ?? this.isRunning,
    account:       account       ?? this.account,
    riskPercent:   riskPercent   ?? this.riskPercent,
    minConfidence: minConfidence ?? this.minConfidence,
    error:         error,
  );
}

class BotNotifier extends StateNotifier<BotState> {
  final Dio _dio = Dio(BaseOptions(baseUrl: 'http://172.20.10.3:8000'));
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
  }

  void setRisk(double v)         => state = state.copyWith(riskPercent: v);
  void setMinConfidence(double v) => state = state.copyWith(minConfidence: v);
}

final botProvider = StateNotifierProvider<BotNotifier, BotState>(
  (ref) => BotNotifier());

final openTradesProvider = FutureProvider.autoDispose((ref) async {
  final dio      = Dio(BaseOptions(baseUrl: 'http://172.20.10.3:8000'));
  final response = await dio.get('/trading/trades/open');
  return response.data['trades'] as List;
});