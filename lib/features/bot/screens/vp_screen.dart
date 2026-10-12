import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/app_colors.dart';
import '../providers/vp_provider.dart';
import '../../../widgets/risk_per_trade_field.dart';
import '../../../widgets/manual_lot_fields.dart';
import '../../../widgets/expectancy_card.dart';

// ── Onglet du bot Volume Profile ──────────────────────────────────────────────
// Second bot, indépendant du bot Scalping : son propre Start/Stop et ses propres
// trades. Rien ici ne lit ni ne modifie l'état du bot Scalping.

const _setupNames = {
  'poc_bounce':  'POC bounce',
  'va_reversal': 'Value-area re-entry',
  'va_breakout': 'Breakout',
};

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

class VpScreen extends ConsumerStatefulWidget {
  const VpScreen({super.key});
  @override
  ConsumerState<VpScreen> createState() => _VpScreenState();
}

class _VpScreenState extends ConsumerState<VpScreen> {
  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(vpProvider.notifier).refresh());
  }

  @override
  Widget build(BuildContext context) {
    final s   = ref.watch(vpProvider);
    final ntf = ref.read(vpProvider.notifier);

    return Scaffold(
      backgroundColor: AppColors.background,
      body: RefreshIndicator(
        color: AppColors.primary,
        onRefresh: ntf.refresh,
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          physics: const AlwaysScrollableScrollPhysics(),
          child: Column(children: [
            Row(children: [
              const Text('📊 Volume Profile Bot', style: TextStyle(
                color: AppColors.primary, fontWeight: FontWeight.bold, fontSize: 16)),
              const Spacer(),
              Text(_updatedLabel(s.updatedAt),
                style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
              IconButton(
                icon: const Icon(Icons.refresh, color: AppColors.textSecondary, size: 20),
                onPressed: ntf.refresh,
              ),
            ]),
            const SizedBox(height: 8),
            _VpStatusCard(s: s, ntf: ntf),
            const SizedBox(height: 12),
            const ExpectancyCard(bot: 'vp'),
            const SizedBox(height: 12),

            if (s.error != null) ...[
              _Banner(text: s.error!, color: AppColors.error),
              const SizedBox(height: 12),
            ],
            if (s.notice != null) ...[
              _Banner(text: s.notice!, color: AppColors.success),
              const SizedBox(height: 12),
            ],
            if (s.paused != null) ...[
              _Banner(text: s.paused!, color: AppColors.warning),
              const SizedBox(height: 12),
            ],
            if (s.lastError != null) ...[
              _Banner(text: 'Last bot error: ${s.lastError!}', color: AppColors.warning),
              const SizedBox(height: 12),
            ],

            _VpSetupCard(s: s),
            const SizedBox(height: 12),
            if (s.openPositions.isNotEmpty) ...[
              _VpOpenCard(s: s),
              const SizedBox(height: 12),
            ],
            _VpSettingsCard(s: s, ntf: ntf),
            const SizedBox(height: 12),
            const _VpHistoryCard(),
            const SizedBox(height: 24),
          ]),
        ),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Status + Start/Stop
// ─────────────────────────────────────────────────────────────────────────────

class _VpStatusCard extends StatelessWidget {
  final VpState s;
  final VpNotifier ntf;
  const _VpStatusCard({required this.s, required this.ntf});

  @override
  Widget build(BuildContext context) {
    final all  = s.all;
    final net  = (all['net_profit'] as num?) ?? 0;
    final wr   = all['win_rate'] as num?;
    final age  = s.lastScanAgeMin;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: AppColors.cardBackground,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: s.enabled ? AppColors.success.withValues(alpha: 0.5) : AppColors.surface,
          width: 1.5,
        ),
      ),
      child: Column(children: [
        Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          Icon(Icons.stacked_bar_chart,
            color: s.enabled ? AppColors.success : AppColors.textSecondary, size: 32),
          const SizedBox(width: 10),
          Text(
            !s.loaded ? 'LOADING…' : (s.enabled ? 'VOLUME PROFILE ACTIVE' : 'VOLUME PROFILE STOPPED'),
            style: TextStyle(
              color: s.enabled ? AppColors.success : AppColors.textSecondary,
              fontSize: 17, fontWeight: FontWeight.bold,
            ),
          ),
        ]),
        const SizedBox(height: 6),
        const Text(
          'Independent bot — the Scalping tab\'s Start/Stop does not affect it',
          textAlign: TextAlign.center,
          style: TextStyle(color: AppColors.textSecondary, fontSize: 11),
        ),
        const SizedBox(height: 16),
        Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
          _VpStat(label: 'Trades (30d)', value: '${all['trades'] ?? 0}'),
          _VpStat(label: 'Win rate', value: wr == null ? '—' : '${wr.toStringAsFixed(1)}%'),
          _VpStat(label: 'Net P/L', value: _money(net),
            color: net >= 0 ? AppColors.success : AppColors.error),
          _VpStat(label: 'Last scan',
            value: age == null ? '--' : '${age.toStringAsFixed(age < 10 ? 1 : 0)}m ago',
            color: (s.enabled && age != null && age > 5) ? AppColors.error : null),
        ]),
        const SizedBox(height: 16),
        ElevatedButton.icon(
          onPressed: (s.busy || !s.loaded) ? null : (s.enabled ? ntf.stop : ntf.start),
          icon: Icon(s.enabled ? Icons.stop : Icons.play_arrow),
          label: Text(s.busy ? '…' : (s.enabled ? 'Stop VP bot' : 'Start VP bot')),
          style: ElevatedButton.styleFrom(
            backgroundColor: s.enabled ? AppColors.error : AppColors.success,
            foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(horizontal: 36, vertical: 12),
          ),
        ),
        if (s.enabled) ...[
          const SizedBox(height: 8),
          const Text(
            'Stopping it blocks new trades only — open VP trades keep their stop and target at the broker.',
            textAlign: TextAlign.center,
            style: TextStyle(color: AppColors.textHint, fontSize: 10),
          ),
        ],
      ]),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Results by setup
// ─────────────────────────────────────────────────────────────────────────────

class _VpSetupCard extends StatelessWidget {
  final VpState s;
  const _VpSetupCard({required this.s});

  @override
  Widget build(BuildContext context) {
    return _Card(
      title: '🎯 Results by setup (30 days)',
      child: Column(children: [
        for (final e in _setupNames.entries) _setupRow(e.value, s.setup(e.key)),
        const SizedBox(height: 6),
        const Text('Breakeven win rate at a 2R target: 33.3%',
          style: TextStyle(color: AppColors.textHint, fontSize: 10)),
      ]),
    );
  }

  Widget _setupRow(String name, Map<String, dynamic> d) {
    final closed = (d['closed'] as num?) ?? 0;
    final net    = (d['net_profit'] as num?) ?? 0;
    final wr     = d['win_rate'] as num?;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(children: [
        Expanded(child: Text(name,
          style: const TextStyle(color: AppColors.textPrimary, fontSize: 13))),
        SizedBox(width: 60, child: Text('${d['trades'] ?? 0} tr',
          textAlign: TextAlign.right,
          style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))),
        SizedBox(width: 60, child: Text(wr == null ? '—' : '${wr.toStringAsFixed(0)}%',
          textAlign: TextAlign.right,
          style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))),
        SizedBox(width: 72, child: Text(closed == 0 ? '—' : _money(net),
          textAlign: TextAlign.right,
          style: TextStyle(
            color: closed == 0 ? AppColors.textSecondary
                 : (net >= 0 ? AppColors.success : AppColors.error),
            fontWeight: FontWeight.bold, fontSize: 12))),
      ]),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Open VP positions
// ─────────────────────────────────────────────────────────────────────────────

class _VpOpenCard extends StatelessWidget {
  final VpState s;
  const _VpOpenCard({required this.s});

  @override
  Widget build(BuildContext context) => _Card(
    title: '📂 Open VP positions',
    child: Column(children: [
      for (final p in s.openPositions)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Row(children: [
            Expanded(child: Text(
              '${p['symbol']} ${(p['side'] as String? ?? '').toUpperCase()} ${p['volume']}'
              '   ${p['comment'] ?? ''}',
              style: const TextStyle(color: AppColors.textPrimary, fontSize: 13))),
            Text(_money((p['profit'] as num?) ?? 0), style: TextStyle(
              color: ((p['profit'] as num?) ?? 0) >= 0 ? AppColors.success : AppColors.error,
              fontWeight: FontWeight.bold, fontSize: 13)),
          ]),
        ),
    ]),
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// The bot's own settings — editable, saved with one button, applied at the
// next scan. Separate from the Scalping settings in every way.
// ─────────────────────────────────────────────────────────────────────────────

class _VpSettingsCard extends StatelessWidget {
  final VpState s;
  final VpNotifier ntf;
  const _VpSettingsCard({required this.s, required this.ntf});

  @override
  Widget build(BuildContext context) {
    return _Card(
      title: '⚙️ VP settings (separate from Scalping)',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // ── symbols ──
        const _Label('Symbols'),
        if (s.available.isEmpty)
          const Text('Loading the symbols your broker offers…',
            style: TextStyle(color: AppColors.textSecondary, fontSize: 12))
        else
          Wrap(spacing: 6, runSpacing: 6, children: [
            for (final sym in s.available.keys)
              FilterChip(
                label: Text(sym, style: TextStyle(
                  fontSize: 12,
                  color: s.symbols.contains(sym) ? AppColors.background : AppColors.textPrimary)),
                selected: s.symbols.contains(sym),
                showCheckmark: false,
                selectedColor: AppColors.primary,
                backgroundColor: AppColors.surface,
                onSelected: s.busy ? null : (_) => ntf.toggleSymbol(sym),
              ),
          ]),

        // ── risque par trade (2026-10-10) : remplace les lots fixes et le plafond ──
        const SizedBox(height: 12),
        RiskPerTradeField(
          inputKey: const Key('vp_risk_input'),
          value: s.riskPct,
          enabled: !s.busy,
          onChanged: ntf.setRiskPct,
        ),
        // Lot manuel par symbole (2026-10-12) : vide = Auto, sinon ce lot exactement
        ManualLotFields(
          symbols: s.symbols,
          lots: s.manualLots,
          rules: s.available,
          enabled: !s.busy,
          onChanged: ntf.setManualLot,
          safetyNote: 'Safety kept: a trade whose stop would lose more than '
              '${s.riskCapPct.toStringAsFixed(0)}% of the balance is refused, never resized. '
              'Press Save to send it.',
        ),

        // ── daily loss limit ──
        _Label(s.dailyLossPct <= 0
          ? 'Daily loss limit: OFF'
          : 'Daily loss limit: ${s.dailyLossPct.toStringAsFixed(0)}%'),
        Slider(
          value: s.dailyLossPct.clamp(0, 50).toDouble(), min: 0, max: 50, divisions: 50,
          activeColor: AppColors.error,
          label: s.dailyLossPct <= 0 ? 'OFF' : '${s.dailyLossPct.toStringAsFixed(0)}%',
          onChanged: s.busy ? null : ntf.setDailyLoss,
        ),
        const Text('Counts Volume Profile trades only. When reached: no new VP trade '
            'until 00:00 UTC; open trades keep their stop and target.',
          style: TextStyle(color: AppColors.textHint, fontSize: 10)),

        // ── trades per symbol ──
        const _Label('Trades open at once, per symbol'),
        Row(children: [
          IconButton(
            icon: const Icon(Icons.remove_circle_outline, size: 20),
            color: AppColors.textSecondary,
            onPressed: (s.busy || s.maxPerSymbol <= 1) ? null : () => ntf.stepMaxPerSymbol(-1),
          ),
          Container(
            width: 48,
            padding: const EdgeInsets.symmetric(vertical: 6),
            decoration: BoxDecoration(
              color: AppColors.surface, borderRadius: BorderRadius.circular(6)),
            child: Text('${s.maxPerSymbol}', textAlign: TextAlign.center,
              style: const TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.bold)),
          ),
          IconButton(
            icon: const Icon(Icons.add_circle_outline, size: 20),
            color: AppColors.textSecondary,
            onPressed: (s.busy || s.maxPerSymbol >= 5) ? null : () => ntf.stepMaxPerSymbol(1),
          ),
          Expanded(child: Text(
            s.maxPerSymbol == 1 ? 'one trade per symbol'
                : (s.sameDirection ? 'any direction' : 'max 1 buy + 1 sell'),
            textAlign: TextAlign.right,
            style: const TextStyle(color: AppColors.textSecondary, fontSize: 11))),
        ]),
        if (s.maxPerSymbol > 1)
          Row(children: [
            const Expanded(child: Text('Allow several trades in the same direction',
              style: TextStyle(color: AppColors.textPrimary, fontSize: 13))),
            Switch(
              value: s.sameDirection,
              activeThumbColor: AppColors.primary,
              onChanged: s.busy ? null : (_) => ntf.toggleSameDirection(),
            ),
          ]),
        const Text('Each trade risks its own stop, so 3 open trades can mean 3 losses '
            'at once. The risk cap applies per trade, not to the total.',
          style: TextStyle(color: AppColors.textHint, fontSize: 10)),

        // ── value-area width filter ──
        const _Label('Entry filter'),
        Row(children: [
          const Expanded(child: Text('Skip re-entries with little room to run',
            style: TextStyle(color: AppColors.textPrimary, fontSize: 13))),
          Switch(
            value: s.minRoomR > 0,
            activeThumbColor: AppColors.primary,
            onChanged: s.busy ? null : (_) => ntf.toggleRoomFilter(),
          ),
        ]),
        Text(s.minRoomR > 0
            ? 'A re-entry is skipped when the other side of the value area is less than '
              '${s.minRoomR.toStringAsFixed(s.minRoomR == s.minRoomR.roundToDouble() ? 0 : 1)}R '
              'away — the value area is narrow for that stop. 9-week replay: same profit, '
              'worst drawdown 11.2R → 6.9R.'
            : 'Off: every re-entry signal is traded.',
          style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
        Row(children: [
          const Expanded(child: Text('Skip trades where the spread is too big',
            style: TextStyle(color: AppColors.textPrimary, fontSize: 13))),
          Switch(
            value: s.maxSpreadPct > 0,
            activeThumbColor: AppColors.primary,
            onChanged: s.busy ? null : (_) => ntf.toggleSpreadFilter(),
          ),
        ]),
        if (s.maxSpreadPct > 0)
          Slider(
            value: s.maxSpreadPct.clamp(5, 20).toDouble(), min: 5, max: 20, divisions: 15,
            activeColor: AppColors.primary,
            label: '${s.maxSpreadPct.toStringAsFixed(0)}%',
            onChanged: s.busy ? null : ntf.setMaxSpread,
          ),
        Text(s.maxSpreadPct > 0
            ? 'A trade is skipped when the broker spread is ${s.maxSpreadPct.toStringAsFixed(0)}% '
              'or more of the distance to its stop — the fee would eat too much of it. '
              'Mostly hits ETH and USDJPY trades with small stops. 10-week replay: '
              '+33R → +36R, worst drawdown 14R → 9R.'
            : 'Off: the spread is not checked.',
          style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
        Row(children: [
          const Expanded(child: Text('Only the first re-entry per line per day',
            style: TextStyle(color: AppColors.textPrimary, fontSize: 13))),
          Switch(
            value: s.firstPerEdge,
            activeThumbColor: AppColors.primary,
            onChanged: s.busy ? null : (_) => ntf.toggleFirstPerEdge(),
          ),
        ]),
        Text(s.firstPerEdge
            ? 'Only the first buy at VAL and the first sell at VAH each day (UTC) are '
              'traded; later re-entries on the same line are skipped. Same profit in the '
              'replay, but worst losing streak 11 → 8 (5 with the spread filter).'
            : 'Off: every re-entry on the same line is traded.',
          style: const TextStyle(color: AppColors.textHint, fontSize: 10)),

        // ── news pause ──
        const _Label('Economic news'),
        Row(children: [
          const Expanded(child: Text('Pause around high-impact news',
            style: TextStyle(color: AppColors.textPrimary, fontSize: 13))),
          Switch(
            value: s.newsPause,
            activeThumbColor: AppColors.primary,
            onChanged: s.busy ? null : (_) => ntf.toggleNewsPause(),
          ),
        ]),
        const Text('No new VP trade from 15 min before to 18 min after high-impact news '
            '(10 → 5 min for medium) on the currencies that move each symbol — same '
            'calendar as the Scalping bot. A signal during the pause is skipped.',
          style: TextStyle(color: AppColors.textHint, fontSize: 10)),
        Row(children: [
          const Expanded(child: Text('Close open trades before high-impact news',
            style: TextStyle(color: AppColors.textPrimary, fontSize: 13))),
          Switch(
            value: s.newsCloseMin > 0,
            activeThumbColor: AppColors.primary,
            onChanged: s.busy ? null : (_) => ntf.toggleNewsClose(),
          ),
        ]),
        Text(s.newsCloseMin > 0
            ? 'Open VP trades are closed at market ${s.newsCloseMin} min before a '
              'high-impact event on their currency (USD moves every VP symbol), so a '
              'news spike cannot hit their stop — on 02/10 the NFP spike cost two open '
              'trades \$35.'
            : 'Off: open trades stay open through the news, protected only by their stop.',
          style: const TextStyle(color: AppColors.textHint, fontSize: 10)),

        // ── setups ──
        const _Label('Setups'),
        for (final key in vpSetupKeys)
          Row(children: [
            Expanded(child: Text(_setupNames[key] ?? key,
              style: const TextStyle(color: AppColors.textPrimary, fontSize: 13))),
            Switch(
              value: s.setups[key] ?? true,
              activeThumbColor: AppColors.primary,
              onChanged: s.busy ? null : (_) => ntf.toggleSetup(key),
            ),
          ]),

        const SizedBox(height: 4),
        const Text('Trading hours: 24/7 while the market is open  ·  Exits: fixed stop, target 2R',
          style: TextStyle(color: AppColors.textSecondary, fontSize: 11)),
        const SizedBox(height: 12),

        // ── save / discard ──
        Row(children: [
          Expanded(
            child: ElevatedButton.icon(
              onPressed: (s.dirty && !s.busy) ? ntf.save : null,
              icon: const Icon(Icons.save),
              label: Text(s.busy ? 'Saving…' : 'Save VP settings'),
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.primary,
                foregroundColor: AppColors.background,
                padding: const EdgeInsets.symmetric(vertical: 12),
              ),
            ),
          ),
          if (s.dirty) ...[
            const SizedBox(width: 8),
            TextButton(
              onPressed: s.busy ? null : ntf.discard,
              child: const Text('Discard', style: TextStyle(color: AppColors.textSecondary)),
            ),
          ],
        ]),
        if (s.dirty)
          const Padding(
            padding: EdgeInsets.only(top: 6),
            child: Text('Unsaved changes',
              style: TextStyle(color: AppColors.warning, fontSize: 11)),
          ),
      ]),
    );
  }
}

class _Label extends StatelessWidget {
  final String text;
  const _Label(this.text);

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 12, bottom: 6),
    child: Text(text, style: const TextStyle(
      color: AppColors.textSecondary, fontSize: 12, fontWeight: FontWeight.w600)),
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Its own trade history
// ─────────────────────────────────────────────────────────────────────────────

class _VpHistoryCard extends ConsumerWidget {
  const _VpHistoryCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final history = ref.watch(vpHistoryProvider);
    return _Card(
      title: '📜 Volume Profile trades',
      child: history.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Text('Error: $e', style: const TextStyle(color: AppColors.error)),
        data: (list) => list.isEmpty
          ? const Text('No Volume Profile trades yet',
              style: TextStyle(color: AppColors.textSecondary))
          : Column(children: list.map((raw) {
              final t      = (raw as Map).cast<String, dynamic>();
              final state  = t['state'] as String? ?? 'open';
              final profit = t['profit'] as num?;
              final icon   = state == 'win'  ? Icons.check_circle
                           : state == 'loss' ? Icons.cancel
                           : Icons.timelapse;
              final color  = state == 'win'  ? AppColors.success
                           : state == 'loss' ? AppColors.error
                           : AppColors.warning;
              final setup  = _setupNames[t['setup']] ?? t['setup'] ?? '';
              return Padding(
                padding: const EdgeInsets.symmetric(vertical: 5),
                child: Row(children: [
                  Icon(icon, color: color, size: 14),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(
                        '${t['symbol']} ${(t['direction'] as String? ?? '').toUpperCase()}  ·  $setup',
                        style: const TextStyle(color: AppColors.textPrimary, fontSize: 13),
                      ),
                      Text(
                        '${_localTime(t['timestamp'] as String?)}'
                        '${t['exit'] != null ? '  ·  exit: ${t['exit']}' : ''}',
                        style: const TextStyle(color: AppColors.textSecondary, fontSize: 11),
                      ),
                    ]),
                  ),
                  Text(
                    profit != null ? _money(profit) : 'open',
                    style: TextStyle(
                      color: profit == null ? AppColors.warning
                           : (profit >= 0 ? AppColors.success : AppColors.error),
                      fontWeight: FontWeight.bold, fontSize: 13,
                    ),
                  ),
                ]),
              );
            }).toList()),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Small helpers
// ─────────────────────────────────────────────────────────────────────────────

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

class _Banner extends StatelessWidget {
  final String text;
  final Color color;
  const _Banner({required this.text, required this.color});

  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(
      color: color.withValues(alpha: 0.1),
      borderRadius: BorderRadius.circular(8),
      border: Border.all(color: color.withValues(alpha: 0.4)),
    ),
    child: Text(text, style: TextStyle(color: color, fontSize: 12)),
  );
}

class _VpStat extends StatelessWidget {
  final String label, value;
  final Color? color;
  const _VpStat({required this.label, required this.value, this.color});

  @override
  Widget build(BuildContext context) => Column(children: [
    Text(value, style: TextStyle(
      color: color ?? AppColors.textPrimary, fontSize: 15, fontWeight: FontWeight.bold)),
    const SizedBox(height: 2),
    Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 10)),
  ]);
}
