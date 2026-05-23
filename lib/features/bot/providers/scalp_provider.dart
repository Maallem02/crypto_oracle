import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

// ── State ─────────────────────────────────────────────────────────────────────

class ScalpState {
  final bool isRunning;
  final double riskPercent;
  final double minScore;
  final int maxTrades;
  final int maxDailyTrades;
  final double maxDailyLossPct;
  final List<String> enabledSymbols;
  final List<String> enabledTimeframes;
  final int cooldownMinutes;
  final List<String> htfTimeframe;
  final Map<String, double> lotSizes;   // symbol → fixed lot (0 = auto)

  // Live status
  final int tradesToday;
  final double dailyPnl;
  final String? lastScan;
  final String? stoppedReason;
  final String? error;

  const ScalpState({
    this.isRunning        = false,
    this.riskPercent      = 0.6,
    this.minScore         = 85.0,
    this.maxTrades        = 5,
    this.maxDailyTrades   = 20,
    this.maxDailyLossPct  = 2.0,
    this.enabledSymbols   = const ['BTC', 'ETH', 'SOL', 'XAUUSD', 'GBPJPY'],
    this.enabledTimeframes= const ['5m', '15m'],
    this.cooldownMinutes  = 10,
    this.htfTimeframe     = const ['30m'],
    this.lotSizes         = const {
      'BTC': 0.01, 'ETH': 0.10, 'SOL': 0.50,
      'XAUUSD': 0.01, 'GBPJPY': 0.02,
    },
    this.tradesToday      = 0,
    this.dailyPnl         = 0.0,
    this.lastScan,
    this.stoppedReason,
    this.error,
  });

  ScalpState copyWith({
    bool? isRunning,
    double? riskPercent,
    double? minScore,
    int? maxTrades,
    int? maxDailyTrades,
    double? maxDailyLossPct,
    List<String>? enabledSymbols,
    List<String>? enabledTimeframes,
    int? cooldownMinutes,
    List<String>? htfTimeframe,
    Map<String, double>? lotSizes,
    int? tradesToday,
    double? dailyPnl,
    String? lastScan,
    String? stoppedReason,
    String? error,
  }) => ScalpState(
    isRunning:         isRunning         ?? this.isRunning,
    riskPercent:       riskPercent       ?? this.riskPercent,
    minScore:          minScore          ?? this.minScore,
    maxTrades:         maxTrades         ?? this.maxTrades,
    maxDailyTrades:    maxDailyTrades    ?? this.maxDailyTrades,
    maxDailyLossPct:   maxDailyLossPct   ?? this.maxDailyLossPct,
    enabledSymbols:    enabledSymbols    ?? this.enabledSymbols,
    enabledTimeframes: enabledTimeframes ?? this.enabledTimeframes,
    cooldownMinutes:   cooldownMinutes   ?? this.cooldownMinutes,
    htfTimeframe:      htfTimeframe      ?? this.htfTimeframe,
    lotSizes:          lotSizes          ?? this.lotSizes,
    tradesToday:       tradesToday       ?? this.tradesToday,
    dailyPnl:          dailyPnl          ?? this.dailyPnl,
    lastScan:          lastScan          ?? this.lastScan,
    stoppedReason:     stoppedReason     ?? this.stoppedReason,
    error:             error,
  );

  Map<String, dynamic> toSettings() => {
    'risk_percent':       riskPercent,
    'min_score':          minScore,
    'max_trades':         maxTrades,
    'max_daily_trades':   maxDailyTrades,
    'max_daily_loss_pct': maxDailyLossPct,
    'enabled_symbols':    enabledSymbols,
    'enabled_timeframes': enabledTimeframes,
    'cooldown_minutes':   cooldownMinutes,
    'htf_timeframe':      htfTimeframe,
    'lot_sizes':          {
      for (final e in lotSizes.entries)
        if (e.value > 0) e.key: e.value
    },
  };
}

// ── Notifier ──────────────────────────────────────────────────────────────────

class ScalpNotifier extends StateNotifier<ScalpState> {
  final Dio _dio = Dio(BaseOptions(baseUrl: 'http://127.0.0.1:8000'));

  ScalpNotifier() : super(const ScalpState()) {
    refreshStatus();
  }

  Future<void> refreshStatus() async {
    try {
      final r = await _dio.get('/trading/scalping/status');
      final d = r.data as Map<String, dynamic>;
      final s = d['settings'] as Map<String, dynamic>? ?? {};
      final rawLots = s['lot_sizes'] as Map<String, dynamic>? ?? {};
      state = state.copyWith(
        isRunning:         d['running']        as bool?  ?? false,
        tradesToday:       d['trades_today']   as int?   ?? 0,
        dailyPnl:          (d['daily_pnl']     as num?)?.toDouble() ?? 0.0,
        lastScan:          d['last_scan']      as String?,
        stoppedReason:     d['stopped_reason'] as String?,
        riskPercent:       (s['risk_percent']      as num?)?.toDouble() ?? state.riskPercent,
        minScore:          (s['min_score']          as num?)?.toDouble() ?? state.minScore,
        maxTrades:         s['max_trades']          as int?   ?? state.maxTrades,
        maxDailyTrades:    s['max_daily_trades']    as int?   ?? state.maxDailyTrades,
        maxDailyLossPct:   (s['max_daily_loss_pct'] as num?)?.toDouble() ?? state.maxDailyLossPct,
        enabledSymbols:    (s['enabled_symbols']    as List?)?.cast<String>() ?? state.enabledSymbols,
        enabledTimeframes: (s['enabled_timeframes'] as List?)?.cast<String>() ?? state.enabledTimeframes,
        cooldownMinutes:   s['cooldown_minutes']    as int?   ?? state.cooldownMinutes,
        htfTimeframe:      (s['htf_timeframe']      as List?)?.cast<String>() ?? state.htfTimeframe,
        lotSizes:          rawLots.map((k, v) => MapEntry(k, (v as num).toDouble())),
      );
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> start() async {
    try {
      await _dio.post('/trading/scalping/start', data: state.toSettings());
      state = state.copyWith(isRunning: true, error: null);
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> stop() async {
    try {
      await _dio.post('/trading/scalping/stop');
      state = state.copyWith(isRunning: false);
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> applySettings() async {
    if (state.isRunning) {
      await stop();
      await start();
    }
  }

  // ── Settings setters ───────────────────────────────────────────────────────
  void setRisk(double v)           => state = state.copyWith(riskPercent: v);
  void setMinScore(double v)       => state = state.copyWith(minScore: v);
  void setMaxTrades(int v)         => state = state.copyWith(maxTrades: v);
  void setMaxDailyTrades(int v)    => state = state.copyWith(maxDailyTrades: v);
  void setMaxDailyLoss(double v)   => state = state.copyWith(maxDailyLossPct: v);
  void setCooldown(int v)          => state = state.copyWith(cooldownMinutes: v);

  void toggleSymbol(String s) {
    final list = List<String>.from(state.enabledSymbols);
    list.contains(s) ? list.remove(s) : list.add(s);
    state = state.copyWith(enabledSymbols: list);
  }

  void toggleTimeframe(String tf) {
    final list = List<String>.from(state.enabledTimeframes);
    list.contains(tf) ? list.remove(tf) : list.add(tf);
    state = state.copyWith(enabledTimeframes: list);
  }

  void toggleHtf(String tf) {
    final list = List<String>.from(state.htfTimeframe);
    list.contains(tf) ? list.remove(tf) : list.add(tf);
    state = state.copyWith(htfTimeframe: list);
  }

  void setLotSize(String symbol, double lot) {
    final map = Map<String, double>.from(state.lotSizes);
    map[symbol] = lot;
    state = state.copyWith(lotSizes: map);
  }
}

// ── Providers ─────────────────────────────────────────────────────────────────

final scalpProvider = StateNotifierProvider<ScalpNotifier, ScalpState>(
  (ref) => ScalpNotifier(),
);

final scalpHistoryProvider = StreamProvider.autoDispose<List>((ref) async* {
  final dio = Dio(BaseOptions(baseUrl: 'http://127.0.0.1:8000'));
  while (true) {
    try {
      final r = await dio.get('/trading/scalping/history');
      yield r.data['history'] as List;
    } catch (_) {
      yield [];
    }
    await Future.delayed(const Duration(seconds: 15));
  }
});
