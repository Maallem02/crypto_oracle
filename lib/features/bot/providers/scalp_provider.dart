import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/api_constants.dart';
import '../../../core/config/api_config.dart';
import '../../../core/network/dio_client.dart';

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
  final double mlThreshold;             // 0 = disabled, 0.62 = only trade if win_prob >= 62%
  // Risque max par trade en % du solde (1-50). null = plafonds par symbole du
  // backend (15 % crypto et métaux, 7 % forex). Un trade dont le stop
  // coûterait plus est refusé — au marché comme en limite.
  final double? riskCapPct;

  // Live status
  final int tradesToday;
  final double dailyPnl;
  final String? lastScan;
  final String? stoppedReason;
  final String? error;
  final DateTime? updatedAt;            // dernier /scalping/status reçu

  // Réglages modifiés dans l'app mais pas encore envoyés (Start / Apply).
  // Tant que c'est vrai, le rafraîchissement auto (30 s) ne met à jour que
  // l'état live et ne remplace pas la saisie par les valeurs du serveur.
  final bool dirty;

  const ScalpState({
    this.isRunning        = false,
    this.riskPercent      = 0.5,
    this.minScore         = 88.0,
    this.maxTrades        = 3,
    this.maxDailyTrades   = 0,
    this.maxDailyLossPct  = 50.0,
    this.enabledSymbols   = const ['BTC', 'ETH', 'XAUUSD', 'GBPJPY'],
    this.enabledTimeframes= const ['15m'],
    this.cooldownMinutes  = 8,
    this.htfTimeframe     = const ['30m'],
    this.lotSizes         = const {
      'BTC': 0.01, 'ETH': 0.05,
      'XAUUSD': 0.01, 'XAGUSD': 0.01, 'GBPJPY': 0.05,
    },
    this.mlThreshold      = 0.62,
    this.riskCapPct,
    this.tradesToday      = 0,
    this.dailyPnl         = 0.0,
    this.lastScan,
    this.stoppedReason,
    this.error,
    this.updatedAt,
    this.dirty            = false,
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
    double? mlThreshold,
    double? riskCapPct,
    bool clearRiskCap = false,          // retour aux plafonds par symbole
    int? tradesToday,
    double? dailyPnl,
    String? lastScan,
    String? stoppedReason,
    String? error,
    DateTime? updatedAt,
    bool? dirty,
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
    mlThreshold:       mlThreshold       ?? this.mlThreshold,
    riskCapPct:        clearRiskCap ? null : (riskCapPct ?? this.riskCapPct),
    tradesToday:       tradesToday       ?? this.tradesToday,
    dailyPnl:          dailyPnl          ?? this.dailyPnl,
    lastScan:          lastScan          ?? this.lastScan,
    stoppedReason:     stoppedReason     ?? this.stoppedReason,
    error:             error,
    updatedAt:         updatedAt         ?? this.updatedAt,
    dirty:             dirty             ?? this.dirty,
  );

  /// État live du serveur, valeurs nulles comprises (copyWith ne sait pas
  /// remettre stoppedReason à null : l'ancienne raison d'arrêt restait
  /// affichée après un redémarrage du bot). Réglages et saisie conservés.
  ScalpState withLive({
    required bool isRunning,
    required int tradesToday,
    required double dailyPnl,
    required String? lastScan,
    required String? stoppedReason,
  }) => ScalpState(
    isRunning:         isRunning,
    riskPercent:       riskPercent,
    minScore:          minScore,
    maxTrades:         maxTrades,
    maxDailyTrades:    maxDailyTrades,
    maxDailyLossPct:   maxDailyLossPct,
    enabledSymbols:    enabledSymbols,
    enabledTimeframes: enabledTimeframes,
    cooldownMinutes:   cooldownMinutes,
    htfTimeframe:      htfTimeframe,
    lotSizes:          lotSizes,
    mlThreshold:       mlThreshold,
    riskCapPct:        riskCapPct,
    tradesToday:       tradesToday,
    dailyPnl:          dailyPnl,
    lastScan:          lastScan,
    stoppedReason:     stoppedReason,
    updatedAt:         DateTime.now(),
    dirty:             dirty,
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
    'ml_threshold':       mlThreshold,
    'risk_cap_pct':       riskCapPct,     // null = plafonds par symbole
  };
}

// ── Notifier ──────────────────────────────────────────────────────────────────

class ScalpNotifier extends StateNotifier<ScalpState> {
  // Primary account Dio (account 1 — status display).
  // A getter, not a field: as a field it captured the URL once at construction
  // and kept talking to the old backend after the address was changed.
  dynamic get _dio => DioClient.create();

  // All accounts Dio instances for broadcast commands.
  // Account 1 follows the runtime URL (ApiConfig) rather than the compiled
  // constant, so changing the backend address in the app reaches the
  // broadcast commands too — otherwise start/stop would still be sent to the
  // old address while the status panel read the new one.
  static List<dynamic> get _allDios => ApiConstants.accounts
      .asMap()
      .entries
      .map((e) => DioClient.create(
            baseUrl: e.key == 0 ? ApiConfig.baseUrl : e.value['url']!,
          ))
      .toList();

  ScalpNotifier() : super(const ScalpState()) {
    refreshStatus();
  }

  bool _refreshing = false;             // le rafraîchissement auto ne s'empile pas

  Future<void> refreshStatus() async {
    if (_refreshing) return;
    _refreshing = true;
    try {
      final r = await _dio.get('/trading/scalping/status');
      final d = r.data as Map<String, dynamic>;
      final s = d['settings'] as Map<String, dynamic>? ?? {};
      final rawLots = s['lot_sizes'] as Map<String, dynamic>? ?? {};
      final live = state.withLive(
        isRunning:         d['running']        as bool?  ?? false,
        tradesToday:       d['trades_today']   as int?   ?? 0,
        dailyPnl:          (d['daily_pnl']     as num?)?.toDouble() ?? 0.0,
        lastScan:          d['last_scan']      as String?,
        stoppedReason:     d['stopped_reason'] as String?,
      );
      if (state.dirty) {                   // saisie en cours : on n'y touche pas
        state = live;
        return;
      }
      state = live.copyWith(
        riskPercent:      (s['risk_percent']      as num?)?.toDouble() ?? state.riskPercent,
        minScore:          (s['min_score']          as num?)?.toDouble() ?? state.minScore,
        maxTrades:         s['max_trades']          as int?   ?? state.maxTrades,
        maxDailyTrades:    s['max_daily_trades']    as int?   ?? state.maxDailyTrades,
        maxDailyLossPct:   (s['max_daily_loss_pct'] as num?)?.toDouble() ?? state.maxDailyLossPct,
        enabledSymbols:    (s['enabled_symbols']    as List?)?.cast<String>() ?? state.enabledSymbols,
        enabledTimeframes: (s['enabled_timeframes'] as List?)?.cast<String>() ?? state.enabledTimeframes,
        cooldownMinutes:   s['cooldown_minutes']    as int?   ?? state.cooldownMinutes,
        htfTimeframe:      (s['htf_timeframe']      as List?)?.cast<String>() ?? state.htfTimeframe,
        lotSizes:          rawLots.map((k, v) => MapEntry(k, (v as num).toDouble())),
        mlThreshold:       (s['ml_threshold'] as num?)?.toDouble() ?? state.mlThreshold,
        riskCapPct:        (s['risk_cap_pct'] as num?)?.toDouble(),
        clearRiskCap:      s['risk_cap_pct'] == null,
      );
    } catch (e) {
      state = state.copyWith(error: e.toString());
    } finally {
      _refreshing = false;
    }
  }

  Future<void> start() async {
    try {
      // Broadcast START to all accounts simultaneously
      await Future.wait(
        _allDios.map((dio) => dio.post('/trading/scalping/start',
            data: state.toSettings()).catchError((_) {})),
      );
      state = state.copyWith(isRunning: true, error: null, dirty: false);   // réglages envoyés
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> stop() async {
    try {
      // Broadcast STOP to all accounts simultaneously
      await Future.wait(
        _allDios.map((dio) => dio.post('/trading/scalping/stop')
            .catchError((_) {})),
      );
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
  // Chaque modification marque la saisie "dirty" jusqu'au prochain Start/Apply.
  void setRisk(double v)           => state = state.copyWith(riskPercent: v, dirty: true);
  void setMinScore(double v)       => state = state.copyWith(minScore: v, dirty: true);
  void setMaxTrades(int v)         => state = state.copyWith(maxTrades: v, dirty: true);
  void setMaxDailyTrades(int v)    => state = state.copyWith(maxDailyTrades: v, dirty: true);
  void setMaxDailyLoss(double v)   => state = state.copyWith(maxDailyLossPct: v, dirty: true);
  void setCooldown(int v)          => state = state.copyWith(cooldownMinutes: v, dirty: true);

  void toggleSymbol(String s) {
    final list = List<String>.from(state.enabledSymbols);
    list.contains(s) ? list.remove(s) : list.add(s);
    state = state.copyWith(enabledSymbols: list, dirty: true);
  }

  void toggleTimeframe(String tf) {
    final list = List<String>.from(state.enabledTimeframes);
    list.contains(tf) ? list.remove(tf) : list.add(tf);
    state = state.copyWith(enabledTimeframes: list, dirty: true);
  }

  void toggleHtf(String tf) {
    final list = List<String>.from(state.htfTimeframe);
    list.contains(tf) ? list.remove(tf) : list.add(tf);
    state = state.copyWith(htfTimeframe: list, dirty: true);
  }

  void setMlThreshold(double v)    => state = state.copyWith(mlThreshold: v, dirty: true);
  void setRiskCap(double v)        => state = state.copyWith(riskCapPct: v.roundToDouble(), dirty: true);
  void resetRiskCap()              => state = state.copyWith(clearRiskCap: true, dirty: true);

  void setLotSize(String symbol, double lot) {
    final map = Map<String, double>.from(state.lotSizes);
    map[symbol] = lot;
    state = state.copyWith(lotSizes: map, dirty: true);
  }
}

// ── Providers ─────────────────────────────────────────────────────────────────

final scalpProvider = StateNotifierProvider<ScalpNotifier, ScalpState>(
  (ref) => ScalpNotifier(),
);

// Positions ouvertes + ordres limite du bot Scalping (magic 234000) — 10 s.
final scalpPositionsProvider =
    StreamProvider.autoDispose<Map<String, dynamic>>((ref) async* {
  final dio = DioClient.create();
  while (true) {
    try {
      final r = await dio.get('/trading/scalping/positions');
      yield (r.data as Map).cast<String, dynamic>();
    } catch (_) {
      yield const {'positions': [], 'pending': [], 'error': true};
    }
    await Future.delayed(const Duration(seconds: 10));
  }
});

// Trades CLÔTURÉS du bot Scalping, lus dans l'historique MT5 (30 jours), avec
// le résumé par symbole — 30 s.
final scalpTradesProvider =
    StreamProvider.autoDispose<Map<String, dynamic>>((ref) async* {
  final dio = DioClient.create();
  while (true) {
    try {
      final r = await dio.get('/trading/scalping/trades',
          queryParameters: {'days': 30, 'limit': 30});
      yield (r.data as Map).cast<String, dynamic>();
    } catch (_) {
      yield const {'trades': [], 'all': {}, 'by_symbol': {}, 'error': true};
    }
    await Future.delayed(const Duration(seconds: 30));
  }
});

// Journal des SIGNAUX évalués par le bot (pas les trades clôturés).
final scalpHistoryProvider = StreamProvider.autoDispose<List>((ref) async* {
  final dio = DioClient.create();
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
