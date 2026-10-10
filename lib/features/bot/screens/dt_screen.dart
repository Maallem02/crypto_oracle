import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/app_colors.dart';
import '../providers/dt_provider.dart';

// ── Onglet du bot Daily Trend ─────────────────────────────────────────────────
// Troisième bot, indépendant : son propre Start/Stop, ses propres réglages et
// trades. Rien ici ne lit ni ne modifie l'état des bots Scalping et VP.

String _updatedLabel(DateTime? t) {
  if (t == null) return 'auto-refresh 30 s';
  String two(int n) => n.toString().padLeft(2, '0');
  return 'updated ${two(t.hour)}:${two(t.minute)}:${two(t.second)} · auto 30 s';
}

String _money(num v) => '${v >= 0 ? '+' : '-'}\$${v.abs().toStringAsFixed(2)}';
String _price(num? v) => v == null ? '--' : v.toStringAsFixed(v >= 1000 ? 0 : 2);
String _pct(double v) => '${v.toStringAsFixed(v == v.roundToDouble() ? 0 : 2)}%';

String _localTime(String? iso) {
  final t = iso == null ? null : DateTime.tryParse(iso)?.toLocal();
  if (t == null) return '--';
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(t.day)}/${two(t.month)} ${two(t.hour)}:${two(t.minute)}';
}

class DtScreen extends ConsumerStatefulWidget {
  const DtScreen({super.key});
  @override
  ConsumerState<DtScreen> createState() => _DtScreenState();
}

class _DtScreenState extends ConsumerState<DtScreen> {
  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(dtProvider.notifier).refresh());
  }

  @override
  Widget build(BuildContext context) {
    final s   = ref.watch(dtProvider);
    final ntf = ref.read(dtProvider.notifier);
    final tooSmall = [
      for (final sym in s.symbols)
        if (s.symbolInfo[sym] != null && s.symbolInfo[sym]!['tradable'] == false) sym
    ];

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
              const Text('📈 Daily Trend Bot', style: TextStyle(
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
            _DtStatusCard(s: s, ntf: ntf),
            const SizedBox(height: 12),
            if (s.loaded && !s.demo) ...[
              const _Banner(text: 'REAL account connected — this bot was only tested, never run with real money.',
                color: AppColors.error),
              const SizedBox(height: 12),
            ],
            for (final sym in tooSmall) ...[
              _Banner(
                color: AppColors.warning,
                text: '$sym: the smallest lot (${s.symbolInfo[sym]!['min_lot']}) would risk '
                    '\$${s.symbolInfo[sym]!['min_lot_risk_usd']} = ${s.symbolInfo[sym]!['min_lot_risk_pct']}% '
                    'of your balance, above your ${_pct(s.riskPct)} limit. $sym signals are skipped until the '
                    'balance is about \$${(s.balanceNeeded(sym) ?? 0).toStringAsFixed(0)}.',
              ),
              const SizedBox(height: 12),
            ],
            if (s.error != null) ...[
              _Banner(text: s.error!, color: AppColors.error),
              const SizedBox(height: 12),
            ],
            if (s.notice != null) ...[
              _Banner(text: s.notice!, color: AppColors.success),
              const SizedBox(height: 12),
            ],
            if (s.lastError != null) ...[
              _Banner(text: 'Last bot error: ${s.lastError!}', color: AppColors.warning),
              const SizedBox(height: 12),
            ],
            _DtLevelsCard(s: s),
            const SizedBox(height: 12),
            if (s.openPositions.isNotEmpty) ...[
              _DtOpenCard(s: s),
              const SizedBox(height: 12),
            ],
            _DtSettingsCard(s: s, ntf: ntf),
            const SizedBox(height: 12),
            const _DtRulesCard(),
            const SizedBox(height: 12),
            const _DtHistoryCard(),
            const SizedBox(height: 24),
          ]),
        ),
      ),
    );
  }
}

// ── Status + Start/Stop ───────────────────────────────────────────────────────
class _DtStatusCard extends StatelessWidget {
  final DtState s;
  final DtNotifier ntf;
  const _DtStatusCard({required this.s, required this.ntf});

  @override
  Widget build(BuildContext context) {
    final age = s.lastScanAgeMin;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: AppColors.cardBackground,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: s.enabled ? AppColors.success.withValues(alpha: 0.5) : AppColors.surface, width: 1.5),
      ),
      child: Column(children: [
        Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          Icon(Icons.trending_up, color: s.enabled ? AppColors.success : AppColors.textSecondary, size: 32),
          const SizedBox(width: 10),
          Text(!s.loaded ? 'LOADING…' : (s.enabled ? 'DAILY TREND ACTIVE' : 'DAILY TREND STOPPED'),
            style: TextStyle(color: s.enabled ? AppColors.success : AppColors.textSecondary,
              fontSize: 17, fontWeight: FontWeight.bold)),
        ]),
        const SizedBox(height: 6),
        const Text('Independent bot — daily trend following on BTC & ETH, buys only',
          textAlign: TextAlign.center, style: TextStyle(color: AppColors.textSecondary, fontSize: 11)),
        const SizedBox(height: 16),
        Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
          _Stat(label: 'Balance', value: '\$${s.balance.toStringAsFixed(2)}'),
          _Stat(label: 'Risk / trade', value: _pct(s.riskPct)),
          _Stat(label: 'Open', value: '${s.openPositions.length}'),
          _Stat(label: 'Last check',
            value: age == null ? '--' : '${age.toStringAsFixed(age < 10 ? 1 : 0)}m ago',
            color: (age != null && age > 15) ? AppColors.error : null),
        ]),
        const SizedBox(height: 16),
        ElevatedButton.icon(
          onPressed: (s.busy || !s.loaded) ? null : (s.enabled ? ntf.stop : ntf.start),
          icon: Icon(s.enabled ? Icons.stop : Icons.play_arrow),
          label: Text(s.busy ? '…' : (s.enabled ? 'Stop Daily Trend' : 'Start Daily Trend')),
          style: ElevatedButton.styleFrom(
            backgroundColor: s.enabled ? AppColors.error : AppColors.success,
            foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(horizontal: 36, vertical: 12),
          ),
        ),
        const SizedBox(height: 8),
        const Text('It acts once a day, just after the 00:00 UTC daily close. Stopping it blocks new '
            'entries only: open trades are still closed by the 10-day exit and keep their stop at the broker.',
          textAlign: TextAlign.center, style: TextStyle(color: AppColors.textHint, fontSize: 10)),
      ]),
    );
  }
}

// ── Today's levels per symbol ─────────────────────────────────────────────────
class _DtLevelsCard extends StatelessWidget {
  final DtState s;
  const _DtLevelsCard({required this.s});

  @override
  Widget build(BuildContext context) => _Card(
    title: '🎯 Levels for the next daily close',
    child: Column(children: [
      for (final sym in dtSymbols) _row(sym, s.symbolInfo[sym] ?? const {}),
    ]),
  );

  Widget _row(String sym, Map<String, dynamic> i) {
    final open = s.openPositions.any((p) => (p['symbol'] as String? ?? '').startsWith(sym));
    final check = (i['last_check'] as Map?)?.cast<String, dynamic>();
    final on = s.symbols.contains(sym);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(sym, style: TextStyle(color: on ? AppColors.textPrimary : AppColors.textHint,
            fontWeight: FontWeight.bold, fontSize: 14)),
          const SizedBox(width: 8),
          Text('now ${_price(i['price'] as num?)}',
            style: const TextStyle(color: AppColors.textSecondary, fontSize: 12)),
          const Spacer(),
          Text(!on ? 'off' : (open ? 'in a trade' : 'waiting'),
            style: TextStyle(color: open ? AppColors.success : AppColors.textSecondary, fontSize: 12)),
        ]),
        const SizedBox(height: 2),
        Text(open
            ? 'Exits if a daily close ends below ${_price(i['exit_level'] as num?)} (10-day low)'
            : 'Buys if a daily close ends above ${_price(i['entry_level'] as num?)} (20-day high)',
          style: const TextStyle(color: AppColors.textPrimary, fontSize: 12)),
        if (i['min_lot_risk_usd'] != null)
          Text('Smallest lot ${i['min_lot']} would risk \$${i['min_lot_risk_usd']} '
              '(${i['min_lot_risk_pct']}% of balance) with today\'s 2 ATR stop',
            style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
        if (check != null)
          Text('Last close ${_localTime(DateTime.fromMillisecondsSinceEpoch(((check['bar_time'] as num?) ?? 0).toInt() * 1000, isUtc: true).toIso8601String())}: '
              '${check['why']}',
            style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
      ]),
    );
  }
}

// ── Open positions ────────────────────────────────────────────────────────────
class _DtOpenCard extends StatelessWidget {
  final DtState s;
  const _DtOpenCard({required this.s});

  @override
  Widget build(BuildContext context) => _Card(
    title: '📂 Open Daily Trend positions',
    child: Column(children: [
      for (final p in s.openPositions)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Row(children: [
            Expanded(child: Text(
              '${p['symbol']} BUY ${p['volume']} @ ${_price(p['price_open'] as num?)}'
              '   stop ${_price(p['sl'] as num?)}   since ${_localTime(p['since'] as String?)}',
              style: const TextStyle(color: AppColors.textPrimary, fontSize: 12))),
            Text(_money((p['profit'] as num?) ?? 0), style: TextStyle(
              color: ((p['profit'] as num?) ?? 0) >= 0 ? AppColors.success : AppColors.error,
              fontWeight: FontWeight.bold, fontSize: 13)),
          ]),
        ),
    ]),
  );
}

// ── Settings: symbols + risk per trade (slider or typed value) ────────────────
class _DtSettingsCard extends StatefulWidget {
  final DtState s;
  final DtNotifier ntf;
  const _DtSettingsCard({required this.s, required this.ntf});
  @override
  State<_DtSettingsCard> createState() => _DtSettingsCardState();
}

class _DtSettingsCardState extends State<_DtSettingsCard> {
  final _risk = TextEditingController();
  final _focus = FocusNode();

  @override
  void didUpdateWidget(covariant _DtSettingsCard old) {
    super.didUpdateWidget(old);
    if (!_focus.hasFocus) _risk.text = widget.s.riskPct.toString();
  }

  @override
  void initState() {
    super.initState();
    _risk.text = widget.s.riskPct.toString();
    _focus.addListener(() { if (!_focus.hasFocus) _apply(); });
  }

  @override
  void dispose() { _risk.dispose(); _focus.dispose(); super.dispose(); }

  void _apply() {
    final v = double.tryParse(_risk.text.replaceAll(',', '.'));
    _risk.text = (v == null ? widget.s.riskPct : widget.ntf.setRisk(v)).toString();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.s, ntf = widget.ntf;
    return _Card(
      title: '⚙️ Daily Trend settings (separate from the other bots)',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const _Label('Symbols'),
        Wrap(spacing: 6, children: [
          for (final sym in dtSymbols)
            FilterChip(
              label: Text(sym, style: TextStyle(fontSize: 12,
                color: s.symbols.contains(sym) ? AppColors.background : AppColors.textPrimary)),
              selected: s.symbols.contains(sym),
              showCheckmark: false,
              selectedColor: AppColors.primary,
              backgroundColor: AppColors.surface,
              onSelected: s.busy ? null : (_) => ntf.toggleSymbol(sym),
            ),
        ]),
        const Text('Only BTC and ETH were tested for this system.',
          style: TextStyle(color: AppColors.textHint, fontSize: 10)),
        _Label('Risk per trade: ${_pct(s.riskPct)} of the balance'),
        Row(children: [
          Expanded(child: Slider(
            value: s.riskPct.clamp(0.25, dtRiskMax).toDouble(), min: 0.25, max: dtRiskMax, divisions: 19,
            activeColor: AppColors.warning,
            label: _pct(s.riskPct),
            onChanged: s.busy ? null : (v) => ntf.setRisk(v),
          )),
          SizedBox(width: 72, child: TextField(
            key: const Key('dt_risk_input'),
            controller: _risk,
            focusNode: _focus,
            enabled: !s.busy,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9.,]'))],
            onSubmitted: (_) => _apply(),
            style: const TextStyle(color: AppColors.textPrimary, fontSize: 13),
            decoration: const InputDecoration(suffixText: '%', isDense: true,
              border: OutlineInputBorder()),
          )),
        ]),
        Text('Each trade is sized so that hitting its stop loses this share of the balance '
            '($dtRiskMin% to ${dtRiskMax.toStringAsFixed(0)}%, type any value). A trade whose smallest '
            'lot would risk more is skipped, never forced. Tested at 2%: about 1 year in 5 loses money, '
            'typical worst drawdown about 14%.',
          style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(child: ElevatedButton.icon(
            onPressed: (s.dirty && !s.busy) ? ntf.save : null,
            icon: const Icon(Icons.save),
            label: Text(s.busy ? 'Saving…' : 'Save Daily Trend settings'),
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.primary, foregroundColor: AppColors.background,
              padding: const EdgeInsets.symmetric(vertical: 12)),
          )),
          if (s.dirty) ...[
            const SizedBox(width: 8),
            TextButton(onPressed: s.busy ? null : ntf.discard,
              child: const Text('Discard', style: TextStyle(color: AppColors.textSecondary))),
          ],
        ]),
      ]),
    );
  }
}

// ── The rules, in plain words ─────────────────────────────────────────────────
class _DtRulesCard extends StatelessWidget {
  const _DtRulesCard();

  @override
  Widget build(BuildContext context) => const _Card(
    title: '📘 How it trades',
    child: Text(
      '• Daily candles, 00:00 to 24:00 UTC. Buys only.\n'
      '• Buys when a daily close ends above the highest high of the previous 20 days.\n'
      '• Sells when a daily close ends below the lowest low of the previous 10 days.\n'
      '• Protective stop at the broker: 2 × the daily ATR (14 days) below the entry.\n'
      '• No target: it lets trends run. Most trades are small losses; a few big trends pay for them.\n'
      '• Tested on Exness 2018–2026: BTC +2.29R per trade, ETH +1.08R per trade, '
      'about 11 trades a year for both together.',
      style: TextStyle(color: AppColors.textSecondary, fontSize: 12, height: 1.5),
    ),
  );
}

// ── Journal ───────────────────────────────────────────────────────────────────
class _DtHistoryCard extends ConsumerWidget {
  const _DtHistoryCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final hist = ref.watch(dtHistoryProvider);
    return _Card(
      title: '🧾 Entries, exits and skipped signals',
      child: hist.when(
        loading: () => const Text('Loading…', style: TextStyle(color: AppColors.textSecondary, fontSize: 12)),
        error: (_, __) => const Text('Journal unavailable', style: TextStyle(color: AppColors.error, fontSize: 12)),
        data: (rows) => rows.isEmpty
            ? const Text('Nothing yet — the bot acts at most once a day, and only on a 20-day breakout.',
                style: TextStyle(color: AppColors.textSecondary, fontSize: 12))
            : Column(children: [
                for (final r in rows.cast<Map>()) _row(r.cast<String, dynamic>()),
              ]),
      ),
    );
  }

  Widget _row(Map<String, dynamic> r) {
    final ev = r['event'] as String? ?? '';
    final profit = r['profit'] as num?;
    final color = ev == 'entry' ? AppColors.primary
        : ev == 'exit' ? AppColors.success
        : ev == 'skip' ? AppColors.warning : AppColors.error;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(_localTime(r['timestamp'] as String?),
            style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
          const SizedBox(width: 8),
          Text('${r['symbol']} ${ev.toUpperCase()}',
            style: TextStyle(color: color, fontWeight: FontWeight.bold, fontSize: 12)),
          const SizedBox(width: 8),
          if (r['volume'] != null)
            Text('${r['volume']} @ ${_price(r['price'] as num?)}',
              style: const TextStyle(color: AppColors.textPrimary, fontSize: 12)),
          const Spacer(),
          if (profit != null)
            Text(_money(profit), style: TextStyle(
              color: profit >= 0 ? AppColors.success : AppColors.error, fontWeight: FontWeight.bold, fontSize: 12)),
        ]),
        if (r['reason'] != null)
          Text(r['reason'] as String, style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
      ]),
    );
  }
}

// ── small shared widgets (same look as the VP tab) ────────────────────────────
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

class _Card extends StatelessWidget {
  final String title;
  final Widget child;
  const _Card({required this.title, required this.child});
  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(16),
    decoration: BoxDecoration(color: AppColors.cardBackground, borderRadius: BorderRadius.circular(12)),
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

class _Stat extends StatelessWidget {
  final String label, value;
  final Color? color;
  const _Stat({required this.label, required this.value, this.color});
  @override
  Widget build(BuildContext context) => Column(children: [
    Text(value, style: TextStyle(color: color ?? AppColors.textPrimary, fontSize: 15, fontWeight: FontWeight.bold)),
    const SizedBox(height: 2),
    Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 10)),
  ]);
}
