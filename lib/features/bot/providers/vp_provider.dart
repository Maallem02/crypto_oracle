import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/dio_client.dart';

// ── Bot Volume Profile ────────────────────────────────────────────────────────
// Second bot, INDÉPENDANT du bot Scalping (SMC) : son propre marche/arrêt, ses
// propres réglages, ses propres trades (magic 234100, table vp_log côté
// backend). Le Start/Stop et les réglages de l'onglet Scalping ne le touchent
// pas, et inversement. Endpoints : /trading/vp/*.
//
// Réglages : modifiés localement (dirty = true), puis envoyés d'un coup par
// save() à POST /trading/vp/settings. Le backend valide tout (lot minimum et
// pas du broker, bornes) et applique au scan suivant, sans redémarrage.

const vpSetupKeys = ['poc_bounce', 'va_reversal', 'va_breakout'];

class LotRule {
  final double minLot, lotStep, maxLot;
  const LotRule(this.minLot, this.lotStep, this.maxLot);
}

class VpState {
  final bool loaded;
  final bool enabled;
  final double? lastScanAgeMin;
  final String? lastError;          // dernière erreur côté bot (backend)
  final String? paused;             // limite de perte journalière atteinte
  final List<Map<String, dynamic>> openPositions;
  final Map<String, dynamic> stats; // payload de /trading/vp/stats

  // réglages (éditables)
  final List<String> symbols;
  final Map<String, double> lotSizes;
  final double riskCapPct;
  final double dailyLossPct;        // 0 = désactivée
  final Map<String, bool> setups;
  final int maxPerSymbol;           // positions VP simultanées par symbole (1-5)
  final bool sameDirection;         // plusieurs positions VP du même sens
  final bool newsPause;             // pas de nouveau trade autour des annonces fortes
  final double minRoomR;            // bord opposé de la zone de valeur à >= X R (0 = off)
  final int newsCloseMin;           // fermer les positions X min avant une annonce forte (0 = off)
  final double maxSpreadPct;        // spread >= X % de la distance au stop -> trade ignoré (0 = off)
  final bool firstPerEdge;          // seulement la 1re réintégration du jour par bord (VAL / VAH)
  final Map<String, LotRule> available;  // symboles offerts par le broker

  final bool dirty;                // modifs non enregistrées
  final bool busy;
  final String? error;              // erreur de connexion / enregistrement
  final String? notice;             // "Saved"...
  final DateTime? updatedAt;        // dernier /vp/status reçu

  const VpState({
    this.loaded         = false,
    this.enabled        = false,
    this.lastScanAgeMin,
    this.lastError,
    this.paused,
    this.openPositions  = const [],
    this.stats          = const {},
    this.symbols        = const [],
    this.lotSizes       = const {},
    this.riskCapPct     = 15,
    this.dailyLossPct   = 0,
    this.setups         = const {},
    this.maxPerSymbol   = 1,
    this.sameDirection  = false,
    this.newsPause      = true,
    this.minRoomR       = 1.0,
    this.newsCloseMin   = 15,
    this.maxSpreadPct   = 10,
    this.firstPerEdge   = true,
    this.available      = const {},
    this.dirty          = false,
    this.busy           = false,
    this.error,
    this.notice,
    this.updatedAt,
  });

  VpState copyWith({
    bool? loaded,
    bool? enabled,
    double? lastScanAgeMin,
    String? lastError,
    String? paused,
    List<Map<String, dynamic>>? openPositions,
    Map<String, dynamic>? stats,
    List<String>? symbols,
    Map<String, double>? lotSizes,
    double? riskCapPct,
    double? dailyLossPct,
    Map<String, bool>? setups,
    int? maxPerSymbol,
    bool? sameDirection,
    bool? newsPause,
    double? minRoomR,
    int? newsCloseMin,
    double? maxSpreadPct,
    bool? firstPerEdge,
    Map<String, LotRule>? available,
    bool? dirty,
    bool? busy,
    String? error,
    String? notice,
    DateTime? updatedAt,
  }) => VpState(
    loaded:         loaded         ?? this.loaded,
    enabled:        enabled        ?? this.enabled,
    lastScanAgeMin: lastScanAgeMin ?? this.lastScanAgeMin,
    lastError:      lastError      ?? this.lastError,
    paused:         paused         ?? this.paused,
    openPositions:  openPositions  ?? this.openPositions,
    stats:          stats          ?? this.stats,
    symbols:        symbols        ?? this.symbols,
    lotSizes:       lotSizes       ?? this.lotSizes,
    riskCapPct:     riskCapPct     ?? this.riskCapPct,
    dailyLossPct:   dailyLossPct   ?? this.dailyLossPct,
    setups:         setups         ?? this.setups,
    maxPerSymbol:   maxPerSymbol   ?? this.maxPerSymbol,
    sameDirection:  sameDirection  ?? this.sameDirection,
    newsPause:      newsPause      ?? this.newsPause,
    minRoomR:       minRoomR       ?? this.minRoomR,
    newsCloseMin:   newsCloseMin   ?? this.newsCloseMin,
    maxSpreadPct:   maxSpreadPct   ?? this.maxSpreadPct,
    firstPerEdge:   firstPerEdge   ?? this.firstPerEdge,
    available:      available      ?? this.available,
    dirty:          dirty          ?? this.dirty,
    busy:           busy           ?? this.busy,
    error:          error,          // null = effacée à chaque changement d'état
    notice:         notice,
    updatedAt:      updatedAt      ?? this.updatedAt,
  );

  Map<String, dynamic> get all => (stats['all'] as Map?)?.cast<String, dynamic>() ?? {};
  Map<String, dynamic> setup(String key) =>
      ((stats['by_setup'] as Map?)?[key] as Map?)?.cast<String, dynamic>() ?? {};

  Map<String, dynamic> toSettings() => {
    'symbols':        symbols,
    'lot_sizes':      lotSizes,
    'risk_cap_pct':   riskCapPct,
    'daily_loss_pct': dailyLossPct,
    'setups':         setups,
    'max_per_symbol': maxPerSymbol,
    'same_direction': sameDirection,
    'news_pause':     newsPause,
    'min_room_r':     minRoomR,
    'news_close_before_min': newsCloseMin,
    'max_spread_pct': maxSpreadPct,
    'first_per_edge': firstPerEdge,
  };
}

String _errText(Object e) {
  if (e is DioException) {
    final data = e.response?.data;
    if (data is Map && data['detail'] != null) return data['detail'].toString();
    return e.message ?? e.toString();
  }
  return e.toString();
}

class VpNotifier extends StateNotifier<VpState> {
  // Getter et non champ : suit l'adresse du backend si elle change dans l'app.
  Dio get _dio => DioClient.create();

  VpNotifier() : super(const VpState()) {
    refresh();
  }

  /// Applique un /vp/status. Si des réglages sont en cours d'édition (dirty),
  /// ils sont CONSERVÉS : un rafraîchissement n'efface pas une saisie.
  VpState _fromStatus(Map<String, dynamic> d, {Map<String, dynamic>? stats, bool keepEdits = true}) {
    final st   = (d['settings'] as Map?)?.cast<String, dynamic>() ?? {};
    final avail = <String, LotRule>{};
    ((d['available_symbols'] as Map?) ?? {}).forEach((k, v) {
      final m = (v as Map).cast<String, dynamic>();
      avail[k as String] = LotRule(
        (m['min_lot'] as num).toDouble(),
        (m['lot_step'] as num).toDouble(),
        (m['max_lot'] as num).toDouble(),
      );
    });
    final keep = keepEdits && state.dirty;
    return VpState(
      loaded:         true,
      enabled:        d['enabled'] as bool? ?? false,
      lastScanAgeMin: (d['last_scan_age_min'] as num?)?.toDouble(),
      lastError:      d['last_error'] as String?,
      paused:         d['paused'] as String?,
      openPositions:  ((d['open_positions'] as List?) ?? const [])
          .map((e) => (e as Map).cast<String, dynamic>()).toList(),
      stats:          stats ?? state.stats,
      available:      avail,
      symbols:        keep ? state.symbols
          : ((st['symbols'] as List?)?.cast<String>() ?? const []),
      lotSizes:       keep ? state.lotSizes
          : ((st['lot_sizes'] as Map?) ?? {}).map(
              (k, v) => MapEntry(k as String, (v as num).toDouble())),
      riskCapPct:     keep ? state.riskCapPct
          : (st['risk_cap_pct'] as num?)?.toDouble() ?? 15,
      dailyLossPct:   keep ? state.dailyLossPct
          : (st['daily_loss_pct'] as num?)?.toDouble() ?? 0,
      setups:         keep ? state.setups
          : ((st['setups'] as Map?) ?? {}).map((k, v) => MapEntry(k as String, v as bool)),
      maxPerSymbol:   keep ? state.maxPerSymbol
          : (st['max_per_symbol'] as num?)?.toInt() ?? 1,
      sameDirection:  keep ? state.sameDirection
          : st['same_direction'] as bool? ?? false,
      newsPause:      keep ? state.newsPause
          : st['news_pause'] as bool? ?? true,
      minRoomR:       keep ? state.minRoomR
          : (st['min_room_r'] as num?)?.toDouble() ?? 1.0,
      newsCloseMin:   keep ? state.newsCloseMin
          : (st['news_close_before_min'] as num?)?.toInt() ?? 15,
      maxSpreadPct:   keep ? state.maxSpreadPct
          : (st['max_spread_pct'] as num?)?.toDouble() ?? 10,
      firstPerEdge:   keep ? state.firstPerEdge
          : st['first_per_edge'] as bool? ?? true,
      dirty:          keep,
      updatedAt:      DateTime.now(),
    );
  }

  bool _refreshing = false;

  /// Aussi appelé toutes les 30 s par le rafraîchissement auto (MainShell) :
  /// sauté pendant un enregistrement, et jamais deux à la fois. Une saisie
  /// en cours (dirty) est conservée par _fromStatus.
  Future<void> refresh() async {
    if (_refreshing || state.busy) return;
    _refreshing = true;
    try {
      final r  = await _dio.get('/trading/vp/status');
      final st = await _dio.get('/trading/vp/stats', queryParameters: {'days': 30});
      state = _fromStatus((r.data as Map).cast<String, dynamic>(),
          stats: (st.data as Map).cast<String, dynamic>());
    } catch (e) {
      state = state.copyWith(error: 'Volume Profile bot unreachable: ${_errText(e)}');
    } finally {
      _refreshing = false;
    }
  }

  Future<void> start() => _switch('/trading/vp/start');
  Future<void> stop()  => _switch('/trading/vp/stop');

  Future<void> _switch(String path) async {
    state = state.copyWith(busy: true);
    try {
      final r = await _dio.post(path);
      state = _fromStatus((r.data as Map).cast<String, dynamic>()).copyWith(busy: false);
    } catch (e) {
      state = state.copyWith(busy: false, error: 'Could not reach the bot: ${_errText(e)}');
    }
  }

  // ── édition des réglages (locale jusqu'à save) ─────────────────────────────
  void toggleSymbol(String sym) {
    final list = List<String>.from(state.symbols);
    final lots = Map<String, double>.from(state.lotSizes);
    if (list.contains(sym)) {
      list.remove(sym);                        // le lot est gardé pour plus tard
    } else {
      list.add(sym);
      lots.putIfAbsent(sym, () => state.available[sym]?.minLot ?? 0.01);
    }
    state = state.copyWith(symbols: list, lotSizes: lots, dirty: true);
  }

  void stepLot(String sym, int direction) {
    final rule = state.available[sym] ?? const LotRule(0.01, 0.01, 100);
    final cur  = state.lotSizes[sym] ?? rule.minLot;
    var next   = cur + direction * rule.lotStep;
    next = next.clamp(rule.minLot, rule.maxLot).toDouble();
    next = (next / rule.lotStep).round() * rule.lotStep;       // aligné sur le pas
    final lots = Map<String, double>.from(state.lotSizes)
      ..[sym] = double.parse(next.toStringAsFixed(4));
    state = state.copyWith(lotSizes: lots, dirty: true);
  }

  /// Valeur saisie au clavier : bornée au min/max du broker et alignée sur son pas.
  void setLot(String sym, double value) {
    final rule = state.available[sym] ?? const LotRule(0.01, 0.01, 100);
    var v = value.clamp(rule.minLot, rule.maxLot).toDouble();
    v = (v / rule.lotStep).round() * rule.lotStep;
    final lots = Map<String, double>.from(state.lotSizes)
      ..[sym] = double.parse(v.toStringAsFixed(4));
    state = state.copyWith(lotSizes: lots, dirty: true);
  }

  void setRiskCap(double v)   => state = state.copyWith(riskCapPct: v.roundToDouble(), dirty: true);
  void setDailyLoss(double v) => state = state.copyWith(dailyLossPct: v.roundToDouble(), dirty: true);

  void stepMaxPerSymbol(int direction) => state = state.copyWith(
      maxPerSymbol: (state.maxPerSymbol + direction).clamp(1, 5).toInt(), dirty: true);

  void toggleSameDirection() =>
      state = state.copyWith(sameDirection: !state.sameDirection, dirty: true);

  void toggleNewsPause() =>
      state = state.copyWith(newsPause: !state.newsPause, dirty: true);

  // interrupteur : 1R (valeur de l'étude) ou 0 (désactivé)
  void toggleRoomFilter() =>
      state = state.copyWith(minRoomR: state.minRoomR > 0 ? 0.0 : 1.0, dirty: true);

  // interrupteur : 15 min avant l'annonce, ou 0 (désactivé)
  void toggleNewsClose() =>
      state = state.copyWith(newsCloseMin: state.newsCloseMin > 0 ? 0 : 15, dirty: true);

  // interrupteur : 10 % (valeur de l'étude) ou 0 (désactivé) ; curseur 5-20 % quand actif
  void toggleSpreadFilter() =>
      state = state.copyWith(maxSpreadPct: state.maxSpreadPct > 0 ? 0.0 : 10.0, dirty: true);
  void setMaxSpread(double v) =>
      state = state.copyWith(maxSpreadPct: v.roundToDouble(), dirty: true);

  void toggleFirstPerEdge() =>
      state = state.copyWith(firstPerEdge: !state.firstPerEdge, dirty: true);

  void toggleSetup(String key) {
    final m = Map<String, bool>.from(state.setups);
    m[key] = !(m[key] ?? true);
    state = state.copyWith(setups: m, dirty: true);
  }

  Future<void> save() async {
    state = state.copyWith(busy: true);
    try {
      final r = await _dio.post('/trading/vp/settings', data: state.toSettings());
      state = _fromStatus((r.data as Map).cast<String, dynamic>(), keepEdits: false)
          .copyWith(busy: false, notice: 'Saved — applies from the next scan (within a minute)');
    } catch (e) {
      // réglages refusés : on garde la saisie pour que l'utilisateur la corrige
      state = state.copyWith(busy: false, error: 'Not saved: ${_errText(e)}');
    }
  }

  Future<void> discard() async {
    state = state.copyWith(dirty: false);
    await refresh();
  }
}

final vpProvider = StateNotifierProvider<VpNotifier, VpState>(
  (ref) => VpNotifier(),
);

// Trades du bot Volume Profile uniquement — jamais mélangés à l'historique Scalping.
final vpHistoryProvider = StreamProvider.autoDispose<List>((ref) async* {
  final dio = DioClient.create();
  while (true) {
    try {
      final r = await dio.get('/trading/vp/history', queryParameters: {'limit': 30});
      yield r.data['trades'] as List;
    } catch (_) {
      yield [];
    }
    await Future.delayed(const Duration(seconds: 15));
  }
});
