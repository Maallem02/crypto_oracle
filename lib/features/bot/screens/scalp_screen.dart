import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/app_colors.dart';
import '../../../widgets/risk_per_trade_field.dart';
import '../../../widgets/manual_lot_fields.dart';
import '../../../widgets/expectancy_card.dart';
import '../providers/scalp_provider.dart';

const _allSymbols    = ['BTC', 'ETH', 'SOL', 'XAUUSD', 'XAGUSD', 'GBPJPY', 'BNB', 'XRP', 'EURUSD', 'USDJPY'];
const _allTimeframes = ['5m', '15m', '30m', '1h'];
const _allHtf        = ['15m', '30m', '1h', '4h'];

String _updatedLabel(DateTime? t) {
  if (t == null) return 'auto-refresh 30 s';
  String two(int n) => n.toString().padLeft(2, '0');
  return 'updated ${two(t.hour)}:${two(t.minute)}:${two(t.second)} · auto 30 s';
}

String _money(num v) => '${v >= 0 ? '+' : '-'}\$${v.abs().toStringAsFixed(2)}';

String _localTime(String? iso) {
  final t = iso == null ? null : DateTime.tryParse(iso)?.toLocal();
  if (t == null) return '--';
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(t.day)}/${two(t.month)} ${two(t.hour)}:${two(t.minute)}';
}

/// "BTCUSDm" -> "BTCUSD" (suffixe de compte Exness)
String _sym(Object? s) {
  final v = (s ?? '').toString();
  return v.endsWith('m') ? v.substring(0, v.length - 1) : v;
}

String _duration(num minutes) {
  final m = minutes.round();
  if (m < 60) return '${m}m';
  if (m < 1440) return '${m ~/ 60}h${(m % 60).toString().padLeft(2, '0')}';
  return '${m ~/ 1440}d ${(m % 1440) ~/ 60}h';
}

const _exitNames = {
  'tp':     'target',
  'sl':     'stop',
  'trail':  'trail / BE stop',
  'manual': 'closed',
  // moitié fermée à +1R, puis le reste (2026-09-28)
  'half+tp':     'half at +1R · rest at target',
  'half+trail':  'half at +1R · rest at entry stop',
  'half+sl':     'half at +1R · rest stopped',
  'half+manual': 'half at +1R · rest closed',
};

class ScalpScreen extends ConsumerStatefulWidget {
  const ScalpScreen({super.key});
  @override
  ConsumerState<ScalpScreen> createState() => _ScalpScreenState();
}

class _ScalpScreenState extends ConsumerState<ScalpScreen> {
  bool _settingsExpanded = true;
  bool _signalsExpanded  = false;   // journal des signaux : replié par défaut

  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(scalpProvider.notifier).refreshStatus());
  }

  @override
  Widget build(BuildContext context) {
    final s   = ref.watch(scalpProvider);
    final ntf = ref.read(scalpProvider.notifier);

    return Scaffold(
      backgroundColor: AppColors.background,
      body: RefreshIndicator(
        color: AppColors.primary,
        onRefresh: ntf.refreshStatus,
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          physics: const AlwaysScrollableScrollPhysics(),
          child: Column(children: [

            // ── Status card ───────────────────────────────────────────────
            Row(children: [
              const Text('⚡ Scalping Bot', style: TextStyle(
                color: AppColors.primary, fontWeight: FontWeight.bold, fontSize: 16)),
              const Spacer(),
              Text(_updatedLabel(s.updatedAt),
                style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
              IconButton(
                icon: const Icon(Icons.refresh, color: AppColors.textSecondary, size: 20),
                onPressed: ntf.refreshStatus,
              ),
            ]),
            const SizedBox(height: 8),
            _StatusCard(s: s, ntf: ntf),
            const SizedBox(height: 12),
            const ExpectancyCard(bot: 'smc'),
            const SizedBox(height: 12),

            // ── Error banner ──────────────────────────────────────────────
            if (s.error != null)
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: AppColors.error.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppColors.error.withValues(alpha: 0.4)),
                ),
                child: Text(s.error!, style: const TextStyle(color: AppColors.error, fontSize: 12)),
              ),

            // ── Résultats, positions ouvertes (comme l'onglet Vol. Profile) ──
            const SizedBox(height: 12),
            const _ScalpResultsCard(),
            const _ScalpOpenCard(),

            // ── Settings ──────────────────────────────────────────────────
            const SizedBox(height: 12),
            _SectionHeader(
              title: '⚙️ Settings',
              expanded: _settingsExpanded,
              onTap: () => setState(() => _settingsExpanded = !_settingsExpanded),
            ),
            if (_settingsExpanded) ...[
              const SizedBox(height: 8),
              _SettingsCard(s: s, ntf: ntf),
            ],
            const SizedBox(height: 12),

            // ── Apply button ──────────────────────────────────────────────
            SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                onPressed: () async {
                  await ntf.applySettings();
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(
                        content: Text('✅ Settings applied'),
                        backgroundColor: AppColors.success,
                        duration: Duration(seconds: 2),
                      ),
                    );
                  }
                },
                icon: const Icon(Icons.save),
                label: Text(s.dirty ? 'Apply Settings (unsaved changes)' : 'Apply Settings'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: AppColors.primary,
                  foregroundColor: AppColors.background,
                  padding: const EdgeInsets.symmetric(vertical: 14),
                ),
              ),
            ),
            const SizedBox(height: 16),

            // ── Trades clôturés (historique MT5) ──────────────────────────
            const _ScalpTradesCard(),
            const SizedBox(height: 16),

            // ── Journal des signaux (ancien "Scalping History") ───────────
            _SectionHeader(
              title: '🧾 Signal log (what the bot evaluated)',
              expanded: _signalsExpanded,
              onTap: () => setState(() => _signalsExpanded = !_signalsExpanded),
            ),
            if (_signalsExpanded) ...[
              const SizedBox(height: 8),
              const _ScalpHistoryCard(),
            ],
            const SizedBox(height: 24),
          ]),
        ),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Status card
// ─────────────────────────────────────────────────────────────────────────────

class _StatusCard extends StatelessWidget {
  final ScalpState s;
  final ScalpNotifier ntf;
  const _StatusCard({required this.s, required this.ntf});

  @override
  Widget build(BuildContext context) {
    final pnlColor = s.dailyPnl >= 0 ? AppColors.success : AppColors.error;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: AppColors.cardBackground,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: s.isRunning
            ? AppColors.success.withValues(alpha: 0.5)
            : AppColors.surface,
          width: 1.5,
        ),
      ),
      child: Column(children: [
        Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          Icon(
            s.isRunning ? Icons.electric_bolt : Icons.electric_bolt_outlined,
            color: s.isRunning ? AppColors.success : AppColors.textSecondary,
            size: 32,
          ),
          const SizedBox(width: 10),
          Text(
            s.isRunning ? 'SCALPING ACTIVE' : 'SCALPING STOPPED',
            style: TextStyle(
              color: s.isRunning ? AppColors.success : AppColors.textSecondary,
              fontSize: 18, fontWeight: FontWeight.bold,
            ),
          ),
        ]),
        if (s.stoppedReason != null) ...[
          const SizedBox(height: 6),
          Text(s.stoppedReason!, style: const TextStyle(color: AppColors.error, fontSize: 11)),
        ],
        const SizedBox(height: 16),

        // Stats row
        Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
          _Stat(label: 'Trades Today', value: '${s.tradesToday}'),
          _Stat(label: 'Daily PnL', value: '${s.dailyPnl >= 0 ? '+' : ''}${s.dailyPnl.toStringAsFixed(2)}%', color: pnlColor),
          _Stat(label: 'Last Scan', value: s.lastScan != null
            ? s.lastScan!.substring(11, 16)
            : '--:--'),
        ]),
        const SizedBox(height: 16),

        // Start/Stop
        ElevatedButton.icon(
          onPressed: s.isRunning ? ntf.stop : ntf.start,
          icon: Icon(s.isRunning ? Icons.stop : Icons.play_arrow),
          label: Text(s.isRunning ? 'Stop' : 'Start'),
          style: ElevatedButton.styleFrom(
            backgroundColor: s.isRunning ? AppColors.error : AppColors.success,
            foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(horizontal: 40, vertical: 12),
          ),
        ),
      ]),
    );
  }
}

class _Stat extends StatelessWidget {
  final String label, value;
  final Color? color;
  const _Stat({required this.label, required this.value, this.color});
  @override
  Widget build(BuildContext context) => Column(children: [
    Text(value, style: TextStyle(
      color: color ?? AppColors.textPrimary,
      fontSize: 16, fontWeight: FontWeight.bold)),
    const SizedBox(height: 2),
    Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
  ]);
}

// ─────────────────────────────────────────────────────────────────────────────
// Section header (collapsible)
// ─────────────────────────────────────────────────────────────────────────────

class _SectionHeader extends StatelessWidget {
  final String title;
  final bool expanded;
  final VoidCallback onTap;
  const _SectionHeader({required this.title, required this.expanded, required this.onTap});

  @override
  Widget build(BuildContext context) => GestureDetector(
    onTap: onTap,
    child: Row(children: [
      Text(title, style: const TextStyle(
        color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15)),
      const Spacer(),
      Icon(expanded ? Icons.expand_less : Icons.expand_more, color: AppColors.textSecondary),
    ]),
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Settings card
// ─────────────────────────────────────────────────────────────────────────────

class _SettingsCard extends StatelessWidget {
  final ScalpState s;
  final ScalpNotifier ntf;
  const _SettingsCard({required this.s, required this.ntf});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.cardBackground,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [

        // Risque par trade (2026-10-10) : remplace les lots fixes et le plafond
        RiskPerTradeField(
          inputKey: const Key('scalp_risk_input'),
          value: s.riskPerTradePct,
          onChanged: ntf.setRiskPerTrade,
          note: 'Each trade is sized so that hitting its stop loses this share of the balance '
              '(0.1% to 5%, type any value). If even the smallest lot would risk more, a buy may wait '
              'for a limit entry close enough to the stop; otherwise the trade is skipped, never forced. '
              'Press Apply Settings to send it. Guideline from the video: 0.25% to 2%.',
        ),
        // Lot manuel par symbole (2026-10-12) : vide = Auto, sinon ce lot exactement
        ManualLotFields(
          symbols: s.enabledSymbols,
          lots: s.manualLots,
          rules: s.lotRules,
          onChanged: ntf.setManualLot,
          safetyNote: 'Safety kept: a trade whose stop would lose more than 15% of the balance '
              '(7% on forex) is refused, never resized. Press Apply Settings to send it.',
        ),
        const SizedBox(height: 8),

        // Min Score
        _SliderRow(
          label: 'Min Score',
          value: s.minScore,
          min: 60, max: 100, divisions: 8,
          display: '${s.minScore.toStringAsFixed(0)}',
          color: AppColors.warning,
          onChanged: ntf.setMinScore,
        ),

        // Max Daily Loss %
        _SliderRow(
          label: 'Max Daily Loss %',
          value: s.maxDailyLossPct,
          min: 0, max: 50, divisions: 100,
          display: s.maxDailyLossPct == 0 ? 'OFF' : '${s.maxDailyLossPct.toStringAsFixed(1)}%',
          color: AppColors.error,
          onChanged: ntf.setMaxDailyLoss,
        ),

        const Divider(color: AppColors.surface, height: 24),

        // Steppers row
        Row(children: [
          Expanded(child: _StepperField(label: 'Max Trades',       value: s.maxTrades,      onChanged: ntf.setMaxTrades)),
          const SizedBox(width: 12),
          Expanded(child: _StepperField(label: 'Max Daily Trades', value: s.maxDailyTrades, onChanged: ntf.setMaxDailyTrades, zeroLabel: '∞')),
          const SizedBox(width: 12),
          Expanded(child: _StepperField(label: 'Cooldown (min)',   value: s.cooldownMinutes, onChanged: ntf.setCooldown)),
        ]),

        const Divider(color: AppColors.surface, height: 24),

        // Symbols
        const Text('Symbols', style: TextStyle(color: AppColors.textSecondary, fontSize: 12)),
        const SizedBox(height: 8),
        Wrap(spacing: 8, runSpacing: 6, children: _allSymbols.map((sym) {
          final active = s.enabledSymbols.contains(sym);
          return GestureDetector(
            onTap: () => ntf.toggleSymbol(sym),
            child: Chip(
              label: Text(sym, style: TextStyle(
                color: active ? AppColors.background : AppColors.textSecondary,
                fontSize: 12, fontWeight: FontWeight.bold,
              )),
              backgroundColor: active ? AppColors.primary : AppColors.surface,
              side: BorderSide.none,
            ),
          );
        }).toList()),

        const SizedBox(height: 16),

        // Entry Timeframes
        const Text('Entry Timeframes', style: TextStyle(color: AppColors.textSecondary, fontSize: 12)),
        const SizedBox(height: 8),
        Wrap(spacing: 8, children: _allTimeframes.map((tf) {
          final active = s.enabledTimeframes.contains(tf);
          return GestureDetector(
            onTap: () => ntf.toggleTimeframe(tf),
            child: Chip(
              label: Text(tf, style: TextStyle(
                color: active ? AppColors.background : AppColors.textSecondary,
                fontSize: 12,
              )),
              backgroundColor: active ? AppColors.warning : AppColors.surface,
              side: BorderSide.none,
            ),
          );
        }).toList()),

        const SizedBox(height: 16),

        // HTF Timeframes
        const Text('HTF Filter', style: TextStyle(color: AppColors.textSecondary, fontSize: 12)),
        const SizedBox(height: 8),
        Wrap(spacing: 8, children: _allHtf.map((tf) {
          final active = s.htfTimeframe.contains(tf);
          return GestureDetector(
            onTap: () => ntf.toggleHtf(tf),
            child: Chip(
              label: Text(tf, style: TextStyle(
                color: active ? AppColors.background : AppColors.textSecondary,
                fontSize: 12,
              )),
              backgroundColor: active ? AppColors.success : AppColors.surface,
              side: BorderSide.none,
            ),
          );
        }).toList()),
      ]),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Results by symbol, open positions, closed trades — read from MT5's own
// history (magic 234000), laid out like the Vol. Profile tab.
// ─────────────────────────────────────────────────────────────────────────────

String _px(Object? v) {
  final n = (v as num?) ?? 0;
  return n >= 1000 ? n.toStringAsFixed(2) : (n >= 10 ? n.toStringAsFixed(3) : n.toStringAsFixed(5));
}

List<Map<String, dynamic>> _rows(Object? list) => ((list as List?) ?? const [])
    .map((e) => (e as Map).cast<String, dynamic>()).toList();

class _Card extends StatelessWidget {
  final String title;
  final Widget child;
  const _Card({required this.title, required this.child});

  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(16),
    decoration: BoxDecoration(
      color: AppColors.cardBackground,
      borderRadius: BorderRadius.circular(12),
    ),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(title, style: const TextStyle(
        color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15)),
      const SizedBox(height: 8),
      child,
    ]),
  );
}

class _ScalpResultsCard extends ConsumerWidget {
  const _ScalpResultsCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final data  = ref.watch(scalpTradesProvider).valueOrNull;
    final all   = (data?['all'] as Map?)?.cast<String, dynamic>() ?? const <String, dynamic>{};
    final bySym = (data?['by_symbol'] as Map?)?.cast<String, dynamic>() ?? const <String, dynamic>{};
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _Card(
        title: '🎯 Results by symbol (30 days)',
        child: data == null
          ? const Center(child: CircularProgressIndicator())
          : Column(children: [
              _resultRow('All symbols', all, bold: true),
              const Divider(color: AppColors.surface, height: 12),
              for (final e in bySym.entries)
                _resultRow(_sym(e.key), (e.value as Map).cast<String, dynamic>()),
              if (bySym.isEmpty)
                const Text('No closed scalping trade in the last 30 days',
                  style: TextStyle(color: AppColors.textSecondary, fontSize: 12)),
            ]),
      ),
    );
  }

  Widget _resultRow(String name, Map<String, dynamic> d, {bool bold = false}) {
    final n   = (d['trades'] as num?) ?? 0;
    final net = (d['net_profit'] as num?) ?? 0;
    final wr  = d['win_rate'] as num?;
    final w   = bold ? FontWeight.bold : FontWeight.normal;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(children: [
        Expanded(child: Text(name,
          style: TextStyle(color: AppColors.textPrimary, fontSize: 13, fontWeight: w))),
        SizedBox(width: 56, child: Text('$n tr', textAlign: TextAlign.right,
          style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))),
        SizedBox(width: 52, child: Text(wr == null ? '—' : '${wr.toStringAsFixed(0)}%',
          textAlign: TextAlign.right,
          style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))),
        SizedBox(width: 80, child: Text(n == 0 ? '—' : _money(net), textAlign: TextAlign.right,
          style: TextStyle(
            color: n == 0 ? AppColors.textSecondary
                 : (net >= 0 ? AppColors.success : AppColors.error),
            fontWeight: FontWeight.bold, fontSize: 12))),
      ]),
    );
  }
}

class _ScalpOpenCard extends ConsumerWidget {
  const _ScalpOpenCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final data = ref.watch(scalpPositionsProvider).valueOrNull;
    final pos  = _rows(data?['positions']);
    final pend = _rows(data?['pending']);
    if (pos.isEmpty && pend.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _Card(
        title: '📂 Open scalping positions',
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          for (final p in pos) _position(p),
          if (pend.isNotEmpty) ...[
            if (pos.isNotEmpty) const Divider(color: AppColors.surface, height: 16),
            const Text('Waiting limit orders',
              style: TextStyle(color: AppColors.textSecondary, fontSize: 11,
                fontWeight: FontWeight.w600)),
            for (final o in pend) _pending(o),
          ],
        ]),
      ),
    );
  }

  Widget _position(Map<String, dynamic> p) {
    final profit = (p['profit'] as num?) ?? 0;
    final atSl   = p['at_sl'] as num?;
    final side   = (p['side'] as String? ?? '').toUpperCase();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${_sym(p['symbol'])} $side ${p['volume']}'
                '${(p['comment'] as String? ?? '').startsWith('CryptoOracle-P') ? '  · limit entry' : ''}',
              style: TextStyle(
                color: side == 'BUY' ? AppColors.success : AppColors.error,
                fontWeight: FontWeight.bold, fontSize: 13)),
            Text('entry ${_px(p['entry'])} · SL ${_px(p['sl'])} · TP ${_px(p['tp'])}',
              style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
            if (atSl != null)
              Text(atSl >= 0 ? 'stop locks ${_money(atSl)}' : 'risk at stop ${_money(atSl)}',
                style: TextStyle(
                  color: atSl >= 0 ? AppColors.success : AppColors.textHint, fontSize: 11)),
          ]),
        ),
        Text(_money(profit), style: TextStyle(
          color: profit >= 0 ? AppColors.success : AppColors.error,
          fontWeight: FontWeight.bold, fontSize: 13)),
      ]),
    );
  }

  Widget _pending(Map<String, dynamic> o) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Row(children: [
      Expanded(child: Text(
        '${_sym(o['symbol'])} ${(o['side'] as String? ?? '').toUpperCase()} LIMIT '
        '${o['volume']} @ ${_px(o['price'])}\nSL ${_px(o['sl'])} · TP ${_px(o['tp'])}',
        style: const TextStyle(color: AppColors.textPrimary, fontSize: 12))),
      Text('since ${_localTime(o['placed'] as String?)}',
        style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
    ]),
  );
}

class _ScalpTradesCard extends ConsumerWidget {
  const _ScalpTradesCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(scalpTradesProvider);
    return _Card(
      title: '📜 Scalping trades',
      child: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Text('Error: $e', style: const TextStyle(color: AppColors.error)),
        data: (d) {
          final list = _rows(d['trades']);
          if (list.isEmpty) {
            return Text(d['error'] == true ? 'Bot unreachable' : 'No closed scalping trade yet',
              style: const TextStyle(color: AppColors.textSecondary));
          }
          return Column(children: [
            for (final t in list) _trade(t),
          ]);
        },
      ),
    );
  }

  Widget _trade(Map<String, dynamic> t) {
    final profit = (t['profit'] as num?) ?? 0;
    final win    = profit > 0;
    final exit   = _exitNames[t['exit']] ?? t['exit'] ?? '';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(children: [
        Icon(win ? Icons.check_circle : Icons.cancel,
          color: win ? AppColors.success : AppColors.error, size: 14),
        const SizedBox(width: 8),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${_sym(t['symbol'])} ${(t['direction'] as String? ?? '').toUpperCase()}'
                '  ·  $exit${t['pending_entry'] == true ? '  ·  limit entry' : ''}',
              style: const TextStyle(color: AppColors.textPrimary, fontSize: 13)),
            Text('${_localTime(t['closed'] as String?)}  ·  held ${_duration((t['minutes'] as num?) ?? 0)}',
              style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
          ]),
        ),
        Text(_money(profit), style: TextStyle(
          color: win ? AppColors.success : AppColors.error,
          fontWeight: FontWeight.bold, fontSize: 13)),
      ]),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Signal log (formerly "Scalping History") — what the bot evaluated, not trades
// ─────────────────────────────────────────────────────────────────────────────

class _ScalpHistoryCard extends ConsumerWidget {
  const _ScalpHistoryCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final history = ref.watch(scalpHistoryProvider);
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.cardBackground,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('Latest signals — setups found, not closed trades',
          style: TextStyle(color: AppColors.textSecondary, fontSize: 12)),
        const SizedBox(height: 8),
        history.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => Text('Error: $e', style: const TextStyle(color: AppColors.error)),
          data: (list) => list.isEmpty
            ? const Text('No scalp trades yet', style: TextStyle(color: AppColors.textSecondary))
            : Column(children: list.reversed.take(15).map((t) {
                final result  = t['result'] as Map? ?? {};
                final success = result['success'] == true;
                final bias    = t['action'] as String? ?? '';
                final score   = t['score'];
                final profit  = result['profit'];
                return Padding(
                  padding: const EdgeInsets.symmetric(vertical: 5),
                  child: Row(children: [
                    Icon(
                      success ? Icons.check_circle : Icons.cancel,
                      color: success ? AppColors.success : AppColors.error,
                      size: 14,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(
                          '${t['symbol']} ${bias.toUpperCase()} ${t['timeframe']}',
                          style: const TextStyle(color: AppColors.textPrimary, fontSize: 13),
                        ),
                        Text(
                          'HTF: ${t['htf_consensus'] ?? '-'}  |  Score: $score',
                          style: const TextStyle(color: AppColors.textSecondary, fontSize: 11),
                        ),
                      ]),
                    ),
                    if (profit != null)
                      Text(
                        '${(profit as num) >= 0 ? '+' : ''}\$${(profit as num).toStringAsFixed(2)}',
                        style: TextStyle(
                          color: (profit as num) >= 0 ? AppColors.success : AppColors.error,
                          fontWeight: FontWeight.bold, fontSize: 13,
                        ),
                      )
                    else if (success)
                      Text('ticket ${result['ticket']}',
                        style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
                  ]),
                );
              }).toList(),
            ),
        ),      // closes history.when(
      ]),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Helper widgets
// ─────────────────────────────────────────────────────────────────────────────

class _SliderRow extends StatelessWidget {
  final String label, display;
  final double value, min, max;
  final int divisions;
  final Color color;
  final ValueChanged<double> onChanged;
  const _SliderRow({
    required this.label, required this.value, required this.min,
    required this.max, required this.divisions, required this.display,
    required this.color, required this.onChanged,
  });

  @override
  Widget build(BuildContext context) => Column(children: [
    Row(children: [
      Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 13)),
      const Spacer(),
      Text(display, style: TextStyle(color: color, fontWeight: FontWeight.bold, fontSize: 13)),
    ]),
    Slider(
      value: value, min: min, max: max, divisions: divisions,
      activeColor: color,
      inactiveColor: AppColors.surface,
      onChanged: onChanged,
    ),
  ]);
}

class _StepperField extends StatelessWidget {
  final String label;
  final int value;
  final String? zeroLabel;
  final ValueChanged<int> onChanged;
  const _StepperField({
    required this.label, required this.value,
    required this.onChanged, this.zeroLabel,
  });

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(10),
    decoration: BoxDecoration(
      color: AppColors.surface,
      borderRadius: BorderRadius.circular(10),
    ),
    child: Column(children: [
      Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 10),
        textAlign: TextAlign.center),
      const SizedBox(height: 6),
      Row(mainAxisAlignment: MainAxisAlignment.center, children: [
        GestureDetector(
          onTap: () { if (value > 0) onChanged(value - 1); },
          child: const Icon(Icons.remove_circle_outline, color: AppColors.error, size: 20),
        ),
        const SizedBox(width: 8),
        Text(
          (zeroLabel != null && value == 0) ? zeroLabel! : '$value',
          style: const TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15),
        ),
        const SizedBox(width: 8),
        GestureDetector(
          onTap: () => onChanged(value + 1),
          child: const Icon(Icons.add_circle_outline, color: AppColors.success, size: 20),
        ),
      ]),
    ]),
  );
}

