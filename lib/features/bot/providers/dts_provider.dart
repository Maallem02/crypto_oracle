import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/dio_client.dart';

// ── Bot Daily Trend SPOT — Binance TESTNET (2026-10-13) ───────────────────────
// Les règles du Daily Trend en spot (fractions de coin, pas de lot minimum) pour
// un petit capital. Argent FICTIF : les ordres ne partent que vers le testnet
// Binance. Son propre marche/arrêt et ses réglages (capital confié, risque par
// trade, plafond crypto). Endpoints : /trading/dts/*.

const dtsSymbols = ['BTC', 'ETH'];
const dtsRiskMin = 0.1, dtsRiskMax = 5.0;
const dtsCapMin = 0.1, dtsCapMax = 10.0;
const dtsCapitalMin = 20.0, dtsCapitalMax = 100000.0;

class DtsState {
  final bool loaded, enabled, keys, busy, dirty;
  final String? lastError, connectionError, error, notice;
  final double? lastScanAgeMin, usdtFree;
  final double equity, realized, freeCash, openRisk;
  final Map<String, Map<String, dynamic>> symbolInfo;
  final List<Map<String, dynamic>> positions;
  // réglages (éditables)
  final List<String> symbols;
  final double riskPct, capPct, capitalUsd;
  final DateTime? updatedAt;
  // Résultat du dernier ordre de test (bouton « Test order »), étapes comprises.
  final Map<String, dynamic>? testResult;

  const DtsState({
    this.loaded = false, this.enabled = false, this.keys = false, this.busy = false, this.dirty = false,
    this.lastError, this.connectionError, this.error, this.notice,
    this.lastScanAgeMin, this.usdtFree,
    this.equity = 0, this.realized = 0, this.freeCash = 0, this.openRisk = 0,
    this.symbolInfo = const {}, this.positions = const [],
    this.symbols = const ['BTC', 'ETH'], this.riskPct = 2.0, this.capPct = 3.0, this.capitalUsd = 200.0,
    this.updatedAt, this.testResult,
  });

  DtsState copyWith({
    bool? busy, bool? dirty, String? error, String? notice,
    List<String>? symbols, double? riskPct, double? capPct, double? capitalUsd,
    Map<String, dynamic>? testResult,
  }) => DtsState(
    loaded: loaded, enabled: enabled, keys: keys,
    busy: busy ?? this.busy, dirty: dirty ?? this.dirty,
    lastError: lastError, connectionError: connectionError,
    error: error, notice: notice,                     // null = effacés à chaque changement
    lastScanAgeMin: lastScanAgeMin, usdtFree: usdtFree,
    equity: equity, realized: realized, freeCash: freeCash, openRisk: openRisk,
    symbolInfo: symbolInfo, positions: positions,
    symbols: symbols ?? this.symbols, riskPct: riskPct ?? this.riskPct,
    capPct: capPct ?? this.capPct, capitalUsd: capitalUsd ?? this.capitalUsd,
    updatedAt: updatedAt,
    testResult: testResult ?? this.testResult,
  );
}

String _errText(Object e) {
  if (e is DioException) {
    final data = e.response?.data;
    if (data is Map && data['detail'] != null) return data['detail'].toString();
    return e.message ?? e.toString();
  }
  return e.toString();
}

double _d(Object? v, [double dflt = 0]) => (v as num?)?.toDouble() ?? dflt;

class DtsNotifier extends StateNotifier<DtsState> {
  Dio get _dio => DioClient.create(timeout: const Duration(seconds: 30));   // appels Binance

  DtsNotifier({bool autoRefresh = true}) : super(const DtsState()) {
    if (autoRefresh) refresh();
  }

  /// Applique un /dts/status déjà lu (tests de l'écran).
  void applyStatus(Map<String, dynamic> d) => state = _fromStatus(d, keepEdits: false);

  /// Applique un /dts/status. Une saisie en cours (dirty) est conservée.
  DtsState _fromStatus(Map<String, dynamic> d, {bool keepEdits = true}) {
    final st = (d['settings'] as Map?)?.cast<String, dynamic>() ?? {};
    final info = <String, Map<String, dynamic>>{};
    ((d['symbols'] as Map?) ?? {}).forEach((k, v) => info[k as String] = (v as Map).cast<String, dynamic>());
    final keep = keepEdits && state.dirty;
    return DtsState(
      loaded: true,
      enabled: d['enabled'] as bool? ?? false,
      keys: d['keys'] as bool? ?? false,
      lastError: d['last_error'] as String?,
      connectionError: d['connection_error'] as String?,
      lastScanAgeMin: (d['last_scan_age_min'] as num?)?.toDouble(),
      usdtFree: (d['usdt_free'] as num?)?.toDouble(),
      equity: _d(d['equity']), realized: _d(d['realized_usd']),
      freeCash: _d(d['free_cash']), openRisk: _d(d['open_risk_usd']),
      symbolInfo: info,
      positions: ((d['positions'] as List?) ?? const []).map((e) => (e as Map).cast<String, dynamic>()).toList(),
      symbols: keep ? state.symbols : ((st['symbols'] as List?)?.cast<String>() ?? const []),
      riskPct: keep ? state.riskPct : _d(st['risk_pct'], 2.0),
      capPct: keep ? state.capPct : _d(st['max_crypto_risk_pct'], 3.0),
      capitalUsd: keep ? state.capitalUsd : _d(st['capital_usd'], 200.0),
      dirty: keep,
      updatedAt: DateTime.now(),
      testResult: state.testResult,
    );
  }

  bool _refreshing = false;

  /// Aussi appelé toutes les 30 s par le rafraîchissement auto (app.dart).
  Future<void> refresh() async {
    if (_refreshing || state.busy) return;
    _refreshing = true;
    try {
      final r = await _dio.get('/trading/dts/status');
      state = _fromStatus((r.data as Map).cast<String, dynamic>());
    } catch (e) {
      state = state.copyWith(error: 'Spot bot unreachable: ${_errText(e)}');
    } finally {
      _refreshing = false;
    }
  }

  Future<void> start() => _post('/trading/dts/start');
  Future<void> stop() => _post('/trading/dts/stop');

  Future<void> _post(String path) async {
    state = state.copyWith(busy: true);
    try {
      final r = await _dio.post(path);
      state = _fromStatus((r.data as Map).cast<String, dynamic>()).copyWith(busy: false);
    } catch (e) {
      state = state.copyWith(busy: false, error: 'Could not reach the bot: ${_errText(e)}');
    }
  }

  /// Bouton « Test order » : ~6$ de BTC achetés puis revendus sur le TESTNET,
  /// stop posé, relu et annulé entre les deux. Les comptes du bot ne bougent pas.
  Future<void> testOrder() async {
    state = state.copyWith(busy: true);
    try {
      final r = await _dio.post('/trading/dts/test-order', queryParameters: {'symbol': 'BTC'});
      state = state.copyWith(busy: false, testResult: (r.data as Map).cast<String, dynamic>());
    } catch (e) {
      state = state.copyWith(busy: false, error: 'Test order not run: ${_errText(e)}');
    }
    await refresh();
  }

  /// Affiche un résultat de test déjà lu (tests de l'écran).
  void applyTestResult(Map<String, dynamic> r) => state = state.copyWith(testResult: r);

  void toggleSymbol(String sym) {
    final list = List<String>.from(state.symbols);
    list.contains(sym) ? list.remove(sym) : list.add(sym);
    state = state.copyWith(symbols: list, dirty: true);
  }

  double _round(double v, double lo, double hi) => (v.clamp(lo, hi) * 100).round() / 100;

  /// Chaque setter borne et arrondit la valeur, et retourne celle retenue.
  double setRisk(double v) { final r = _round(v, dtsRiskMin, dtsRiskMax); state = state.copyWith(riskPct: r, dirty: true); return r; }
  double setCap(double v) { final r = _round(v, dtsCapMin, dtsCapMax); state = state.copyWith(capPct: r, dirty: true); return r; }
  double setCapital(double v) { final r = _round(v, dtsCapitalMin, dtsCapitalMax); state = state.copyWith(capitalUsd: r, dirty: true); return r; }

  Future<void> save() async {
    state = state.copyWith(busy: true);
    try {
      final r = await _dio.post('/trading/dts/settings', data: {
        'symbols': state.symbols, 'risk_pct': state.riskPct,
        'max_crypto_risk_pct': state.capPct, 'capital_usd': state.capitalUsd,
      });
      state = _fromStatus((r.data as Map).cast<String, dynamic>(), keepEdits: false)
          .copyWith(busy: false, notice: 'Saved — used from the next daily signal');
    } catch (e) {
      state = state.copyWith(busy: false, error: 'Not saved: ${_errText(e)}');
    }
  }

  Future<void> discard() async {
    state = state.copyWith(dirty: false);
    await refresh();
  }
}

final dtsProvider = StateNotifierProvider<DtsNotifier, DtsState>((ref) => DtsNotifier());

// Journal du bot spot : achats, ventes, stops, signaux sautés.
final dtsHistoryProvider = StreamProvider.autoDispose<List>((ref) async* {
  final dio = DioClient.create();
  while (true) {
    try {
      final r = await dio.get('/trading/dts/history', queryParameters: {'limit': 30});
      yield r.data['events'] as List;
    } catch (_) {
      yield [];
    }
    await Future.delayed(const Duration(seconds: 30));
  }
});
