import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/app_colors.dart';
import '../providers/scalp_provider.dart';

const _allSymbols    = ['BTC', 'ETH', 'SOL', 'XAUUSD', 'GBPJPY', 'BNB', 'XRP', 'EURUSD', 'USDJPY'];
const _allTimeframes = ['5m', '15m', '30m', '1h'];
const _allHtf        = ['15m', '30m', '1h', '4h'];

class ScalpScreen extends ConsumerStatefulWidget {
  const ScalpScreen({super.key});
  @override
  ConsumerState<ScalpScreen> createState() => _ScalpScreenState();
}

class _ScalpScreenState extends ConsumerState<ScalpScreen> {
  bool _settingsExpanded = true;
  bool _lotsExpanded     = true;

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
              IconButton(
                icon: const Icon(Icons.refresh, color: AppColors.textSecondary, size: 20),
                onPressed: ntf.refreshStatus,
              ),
            ]),
            const SizedBox(height: 8),
            _StatusCard(s: s, ntf: ntf),
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

            // ── Lot sizes ─────────────────────────────────────────────────
            _SectionHeader(
              title: '📦 Lot Sizes (0 = auto)',
              expanded: _lotsExpanded,
              onTap: () => setState(() => _lotsExpanded = !_lotsExpanded),
            ),
            if (_lotsExpanded) ...[
              const SizedBox(height: 8),
              _LotSizesCard(s: s, ntf: ntf),
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
                label: const Text('Apply Settings'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: AppColors.primary,
                  foregroundColor: AppColors.background,
                  padding: const EdgeInsets.symmetric(vertical: 14),
                ),
              ),
            ),
            const SizedBox(height: 16),

            // ── Scalping history ──────────────────────────────────────────
            _ScalpHistoryCard(),
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

        // Risk %
        _SliderRow(
          label: 'Risk %',
          value: s.riskPercent,
          min: 0.1, max: 3.0, divisions: 29,
          display: '${s.riskPercent.toStringAsFixed(1)}%',
          color: AppColors.success,
          onChanged: ntf.setRisk,
        ),

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
          min: 0, max: 10, divisions: 20,
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
// Lot sizes card
// ─────────────────────────────────────────────────────────────────────────────

class _LotSizesCard extends StatelessWidget {
  final ScalpState s;
  final ScalpNotifier ntf;
  const _LotSizesCard({required this.s, required this.ntf});

  static const double _step = 0.01;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.cardBackground,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(children: [
        ...s.enabledSymbols.map((sym) {
          final current = s.lotSizes[sym] ?? 0.0;
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Row(children: [

              // Symbol name
              SizedBox(
                width: 72,
                child: Text(sym, style: const TextStyle(
                  color: AppColors.textPrimary,
                  fontWeight: FontWeight.bold,
                  fontSize: 13,
                )),
              ),

              // − button
              GestureDetector(
                onTap: () {
                  final next = (current - _step).clamp(0.0, 100.0);
                  ntf.setLotSize(sym, double.parse(next.toStringAsFixed(2)));
                },
                child: Container(
                  width: 32, height: 32,
                  decoration: BoxDecoration(
                    color: AppColors.error.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: const Icon(Icons.remove, color: AppColors.error, size: 18),
                ),
              ),
              const SizedBox(width: 10),

              // Value display
              Expanded(
                child: Container(
                  height: 36,
                  decoration: BoxDecoration(
                    color: AppColors.surface,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    current == 0 ? 'AUTO' : current.toStringAsFixed(2),
                    style: TextStyle(
                      color: current == 0 ? AppColors.textSecondary : AppColors.primary,
                      fontWeight: FontWeight.bold,
                      fontSize: 14,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 10),

              // + button
              GestureDetector(
                onTap: () {
                  final next = (current + _step).clamp(0.0, 100.0);
                  ntf.setLotSize(sym, double.parse(next.toStringAsFixed(2)));
                },
                child: Container(
                  width: 32, height: 32,
                  decoration: BoxDecoration(
                    color: AppColors.success.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: const Icon(Icons.add, color: AppColors.success, size: 18),
                ),
              ),
              const SizedBox(width: 10),

              // Reset to AUTO (long press hint)
              GestureDetector(
                onTap: () => ntf.setLotSize(sym, 0),
                child: Text(
                  'AUTO',
                  style: TextStyle(
                    color: current == 0
                      ? AppColors.primary
                      : AppColors.textHint,
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ]),
          );
        }),
        const SizedBox(height: 10),
        const Text('Tap AUTO to reset to risk-based calculation',
          style: TextStyle(color: AppColors.textHint, fontSize: 11)),
      ]),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Scalping history
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
        const Text('📜 Scalping History',
          style: TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15)),
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

