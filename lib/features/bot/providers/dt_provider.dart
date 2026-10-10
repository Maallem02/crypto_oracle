import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/dio_client.dart';

// ── Bot Daily Trend ───────────────────────────────────────────────────────────
// Troisième bot, INDÉPENDANT des bots Scalping et Volume Profile : suivi de
// tendance journalier BTC/ETH (achats seulement). Son propre marche/arrêt, ses
// propres réglages (symboles, risque par trade), ses propres trades (magic
// 234200, table dt_log côté backend). Endpoints : /trading/dt/*.

const dtSymbols = ['BTC', 'ETH'];
const dtRiskMin = 0.1;
const dtRiskMax = 5.0;

class DtState {
  final bool loaded;
  final bool enabled;
  final double? lastScanAgeMin;
  final String? lastError;
  final double balance;
  final bool demo;
  final Map<String, Map<String, dynamic>> symbolInfo;   // niveaux du jour, risque du lot minimum
  final List<Map<String, dynamic>> openPositions;

  // réglages (éditables)
  final List<String> symbols;
  final double riskPct;

  final bool dirty;
  final bool busy;
  final String? error;
  final String? notice;
  final DateTime? updatedAt;

  const DtState({
    this.loaded        = false,
    this.enabled       = false,
    this.lastScanAgeMin,
    this.lastError,
    this.balance       = 0,
    this.demo          = true,
    this.symbolInfo    = const {},
    this.openPositions = const [],
    this.symbols       = const ['BTC', 'ETH'],
    this.riskPct       = 2.0,
    this.dirty         = false,
    this.busy          = false,
    this.error,
    this.notice,
    this.updatedAt,
  });

  DtState copyWith({
    bool? loaded,
    bool? enabled,
    double? lastScanAgeMin,
    String? lastError,
    double? balance,
    bool? demo,
    Map<String, Map<String, dynamic>>? symbolInfo,
    List<Map<String, dynamic>>? openPositions,
    List<String>? symbols,
    double? riskPct,
    bool? dirty,
    bool? busy,
    String? error,
    String? notice,
    DateTime? updatedAt,
  }) => DtState(
    loaded:         loaded         ?? this.loaded,
    enabled:        enabled        ?? this.enabled,
    lastScanAgeMin: lastScanAgeMin ?? this.lastScanAgeMin,
    lastError:      lastError      ?? this.lastError,
    balance:        balance        ?? this.balance,
    demo:           demo           ?? this.demo,
    symbolInfo:     symbolInfo     ?? this.symbolInfo,
    openPositions:  openPositions  ?? this.openPositions,
    symbols:        symbols        ?? this.symbols,
    riskPct:        riskPct        ?? this.riskPct,
    dirty:          dirty          ?? this.dirty,
    busy:           busy           ?? this.busy,
    error:          error,          // null = effacée à chaque changement d'état
    notice:         notice,
    updatedAt:      updatedAt      ?? this.updatedAt,
  );

  /// Solde nécessaire pour que le lot minimum du symbole reste sous le risque choisi.
  double? balanceNeeded(String sym) {
    final usd = (symbolInfo[sym]?['min_lot_risk_usd'] as num?)?.toDouble();
    return (usd == null || riskPct <= 0) ? null : usd / (riskPct / 100);
  }
}

String _errText(Object e) {
  if (e is DioException) {
    final data = e.response?.data;
    if (data is Map && data['detail'] != null) return data['detail'].toString();
    return e.message ?? e.toString();
  }
  return e.toString();
}

class DtNotifier extends StateNotifier<DtState> {
  Dio get _dio => DioClient.create();

  DtNotifier() : super(const DtState()) {
    refresh();
  }

  /// Applique un /dt/status. Une saisie en cours (dirty) est conservée.
  DtState _fromStatus(Map<String, dynamic> d, {bool keepEdits = true}) {
    final st   = (d['settings'] as Map?)?.cast<String, dynamic>() ?? {};
    final acct = (d['account'] as Map?)?.cast<String, dynamic>() ?? {};
    final info = <String, Map<String, dynamic>>{};
    ((d['symbols'] as Map?) ?? {}).forEach((k, v) => info[k as String] = (v as Map).cast<String, dynamic>());
    final keep = keepEdits && state.dirty;
    return DtState(
      loaded:         true,
      enabled:        d['enabled'] as bool? ?? false,
      lastScanAgeMin: (d['last_scan_age_min'] as num?)?.toDouble(),
      lastError:      d['last_error'] as String?,
      balance:        (acct['balance'] as num?)?.toDouble() ?? 0,
      demo:           acct['demo'] as bool? ?? true,
      symbolInfo:     info,
      openPositions:  ((d['open_positions'] as List?) ?? const [])
          .map((e) => (e as Map).cast<String, dynamic>()).toList(),
      symbols:        keep ? state.symbols : ((st['symbols'] as List?)?.cast<String>() ?? const []),
      riskPct:        keep ? state.riskPct : (st['risk_pct'] as num?)?.toDouble() ?? 2.0,
      dirty:          keep,
      updatedAt:      DateTime.now(),
    );
  }

  bool _refreshing = false;

  /// Aussi appelé toutes les 30 s par le rafraîchissement auto (MainShell).
  Future<void> refresh() async {
    if (_refreshing || state.busy) return;
    _refreshing = true;
    try {
      final r = await _dio.get('/trading/dt/status');
      state = _fromStatus((r.data as Map).cast<String, dynamic>());
    } catch (e) {
      state = state.copyWith(error: 'Daily Trend bot unreachable: ${_errText(e)}');
    } finally {
      _refreshing = false;
    }
  }

  Future<void> start() => _switch('/trading/dt/start');
  Future<void> stop()  => _switch('/trading/dt/stop');

  Future<void> _switch(String path) async {
    state = state.copyWith(busy: true);
    try {
      final r = await _dio.post(path);
      state = _fromStatus((r.data as Map).cast<String, dynamic>()).copyWith(busy: false);
    } catch (e) {
      state = state.copyWith(busy: false, error: 'Could not reach the bot: ${_errText(e)}');
    }
  }

  void toggleSymbol(String sym) {
    final list = List<String>.from(state.symbols);
    list.contains(sym) ? list.remove(sym) : list.add(sym);
    state = state.copyWith(symbols: list, dirty: true);
  }

  /// Valeur du curseur ou saisie au clavier, bornée à 0.1-5 % et arrondie à 0.01.
  /// Retourne la valeur retenue (affichée dans le champ de saisie).
  double setRisk(double v) {
    final r = (v.clamp(dtRiskMin, dtRiskMax) * 100).round() / 100;
    state = state.copyWith(riskPct: r, dirty: true);
    return r;
  }

  Future<void> save() async {
    state = state.copyWith(busy: true);
    try {
      final r = await _dio.post('/trading/dt/settings',
          data: {'symbols': state.symbols, 'risk_pct': state.riskPct});
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

final dtProvider = StateNotifierProvider<DtNotifier, DtState>((ref) => DtNotifier());

// Journal du bot Daily Trend : entrées, sorties, signaux refusés.
final dtHistoryProvider = StreamProvider.autoDispose<List>((ref) async* {
  final dio = DioClient.create();
  while (true) {
    try {
      final r = await dio.get('/trading/dt/history', queryParameters: {'limit': 30});
      yield r.data['events'] as List;
    } catch (_) {
      yield [];
    }
    await Future.delayed(const Duration(seconds: 30));
  }
});
