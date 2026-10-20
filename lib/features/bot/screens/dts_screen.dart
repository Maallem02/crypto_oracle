import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/app_colors.dart';
import '../providers/dts_provider.dart';

// ── Onglet Daily Trend SPOT (Binance TESTNET) ─────────────────────────────────
// Mêmes règles que l'onglet Daily Trend, exécutées en spot sur le testnet Binance
// (argent fictif) : fractions de coin, donc un petit capital suffit.

String _usd(num? v, {bool sign = false}) {
  if (v == null) return '--';
  final s = '\$${v.abs().toStringAsFixed(2)}';
  return sign ? '${v >= 0 ? '+' : '-'}$s' : (v < 0 ? '-$s' : s);
}

String _price(num? v) => v == null ? '--' : v.toStringAsFixed(v >= 1000 ? 0 : 2);

String _localTime(String? iso) {
  final t = iso == null ? null : DateTime.tryParse(iso)?.toLocal();
  if (t == null) return '--';
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(t.day)}/${two(t.month)} ${two(t.hour)}:${two(t.minute)}';
}

class DtsScreen extends ConsumerStatefulWidget {
  const DtsScreen({super.key});
  @override
  ConsumerState<DtsScreen> createState() => _DtsScreenState();
}

class _DtsScreenState extends ConsumerState<DtsScreen> {
  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(dtsProvider.notifier).refresh());
  }

  @override
  Widget build(BuildContext context) {
    final s = ref.watch(dtsProvider);
    final ntf = ref.read(dtsProvider.notifier);
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
              const Expanded(child: Text('🧪 Daily Trend Spot — Binance TESTNET', style: TextStyle(
                color: AppColors.primary, fontWeight: FontWeight.bold, fontSize: 16))),
              IconButton(icon: const Icon(Icons.refresh, color: AppColors.textSecondary, size: 20),
                onPressed: ntf.refresh),
            ]),
            const SizedBox(height: 8),
            const _Banner(color: AppColors.primary,
              text: 'Test money only: orders go to Binance\'s TESTNET (testnet.binance.vision), never to a real '
                  'account. Decisions use the REAL market\'s daily candles.'),
            const SizedBox(height: 12),
            if (s.loaded && !s.keys) ...[
              const _Banner(color: AppColors.warning, text:
                'Not connected yet. 1) Open testnet.binance.vision and log in with GitHub. 2) Generate an HMAC '
                'API key there. 3) Add two lines to the backend .env file:\n'
                'BINANCE_TESTNET_API_KEY=your key\nBINANCE_TESTNET_API_SECRET=your secret\n'
                '4) Restart the backend. The keys stay on your PC; never paste them in a chat.'),
              const SizedBox(height: 12),
            ],
            _StatusCard(s: s, ntf: ntf),
            const SizedBox(height: 12),
            _TestOrderCard(s: s, ntf: ntf),
            const SizedBox(height: 12),
            for (final b in [
              if (s.error != null) (s.error!, AppColors.error),
              if (s.notice != null) (s.notice!, AppColors.success),
              if (s.keys && s.lastError != null) ('Last bot error: ${s.lastError}', AppColors.warning),
              if (s.connectionError != null) ('Binance: ${s.connectionError}', AppColors.warning),
            ]) ...[
              _Banner(text: b.$1, color: b.$2),
              const SizedBox(height: 12),
            ],
            _LevelsCard(s: s),
            const SizedBox(height: 12),
            if (s.positions.isNotEmpty) ...[
              _PositionsCard(s: s),
              const SizedBox(height: 12),
            ],
            _SettingsCard(s: s, ntf: ntf),
            const SizedBox(height: 12),
            const _RulesCard(),
            const SizedBox(height: 12),
            const _HistoryCard(),
            const SizedBox(height: 24),
          ]),
        ),
      ),
    );
  }
}

// ── Status + Start/Stop ───────────────────────────────────────────────────────
class _StatusCard extends StatelessWidget {
  final DtsState s;
  final DtsNotifier ntf;
  const _StatusCard({required this.s, required this.ntf});

  @override
  Widget build(BuildContext context) {
    final age = s.lastScanAgeMin;
    final on = s.enabled;
    return _Card(
      title: on ? 'SPOT BOT ACTIVE (testnet)' : 'SPOT BOT STOPPED',
      child: Column(children: [
        Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
          _Stat(label: 'Bot equity', value: _usd(s.equity)),
          _Stat(label: 'Capital', value: _usd(s.capitalUsd)),
          _Stat(label: 'Result', value: _usd(s.realized, sign: true),
            color: s.realized >= 0 ? AppColors.success : AppColors.error),
          _Stat(label: 'Open', value: '${s.positions.length}'),
          _Stat(label: 'Last check', value: age == null ? '--' : '${age.toStringAsFixed(age < 10 ? 1 : 0)}m'),
        ]),
        const SizedBox(height: 12),
        ElevatedButton.icon(
          onPressed: (s.busy || !s.loaded || (!on && !s.keys)) ? null : (on ? ntf.stop : ntf.start),
          icon: Icon(on ? Icons.stop : Icons.play_arrow),
          label: Text(s.busy ? '…' : (on ? 'Stop spot bot' : 'Start spot bot (testnet)')),
          style: ElevatedButton.styleFrom(
            backgroundColor: on ? AppColors.error : AppColors.success, foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 12)),
        ),
        const SizedBox(height: 6),
        Text('Acts once a day, just after the 00:00 UTC close. Stopping blocks new buys only: open positions keep '
            'their stop at Binance and are still sold on the 10-day exit.'
            '${s.usdtFree != null ? ' Testnet account: ${_usd(s.usdtFree)} USDT free (the bot only uses its capital).' : ''}',
          textAlign: TextAlign.center, style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
      ]),
    );
  }
}

// ── Test order: the whole order chain with ~$6 of test money ─────────────────
class _TestOrderCard extends StatelessWidget {
  final DtsState s;
  final DtsNotifier ntf;
  const _TestOrderCard({required this.s, required this.ntf});

  Future<void> _confirm(BuildContext context) async {
    final go = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.cardBackground,
        title: const Text('Run a test order?', style: TextStyle(color: AppColors.textPrimary)),
        content: const Text(
          'This buys about \$6 of BTC on Binance\'s TEST exchange (fake money), places a stop loss at Binance, '
          'reads it back, cancels it and sells the BTC straight back.\n\n'
          'The bot\'s capital, positions and results are not touched.',
          style: TextStyle(color: AppColors.textSecondary)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          ElevatedButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Run test')),
        ],
      ),
    );
    if (go == true) await ntf.testOrder();
  }

  @override
  Widget build(BuildContext context) {
    final holdsBtc = s.positions.any((p) => p['symbol'] == 'BTC');
    final r = s.testResult;
    final steps = ((r?['steps'] as List?) ?? const []).cast<Map>();
    return _Card(
      title: '🔧 Test the order chain',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('Checks buying, the stop at Binance, cancelling and selling with about \$6 of test money, '
            'without waiting for a real signal.',
          style: TextStyle(color: AppColors.textSecondary, fontSize: 12)),
        const SizedBox(height: 8),
        ElevatedButton.icon(
          key: const Key('dts_test_order'),
          onPressed: (!s.keys || s.busy || holdsBtc) ? null : () => _confirm(context),
          icon: const Icon(Icons.science, size: 18),
          label: Text(s.busy ? 'Working…' : 'Run test order (≈ \$6 of BTC, test money)'),
        ),
        if (!s.keys)
          const Text('Needs the testnet keys in .env.', style: TextStyle(color: AppColors.textHint, fontSize: 10)),
        if (holdsBtc)
          const Text('Disabled while the bot holds BTC, so the test can never touch its position.',
            style: TextStyle(color: AppColors.textHint, fontSize: 10)),
        if (r != null) ...[
          const SizedBox(height: 10),
          Text(r['ok'] == true ? '✅ Test order passed' : '❌ Test order failed',
            style: TextStyle(color: r['ok'] == true ? AppColors.success : AppColors.error,
              fontWeight: FontWeight.bold, fontSize: 13)),
          for (final st in steps)
            Padding(
              padding: const EdgeInsets.only(top: 3),
              child: Text('${st['ok'] == true ? '✅' : '❌'} ${st['step']}'
                  '${(st['detail'] ?? '').toString().isEmpty ? '' : ' — ${st['detail']}'}',
                style: TextStyle(color: st['ok'] == true ? AppColors.textPrimary : AppColors.error, fontSize: 11)),
            ),
          if (r['cost_usd'] != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('Cost of the round trip: \$${(r['cost_usd'] as num).toStringAsFixed(4)} '
                  '(spread + fees, test money)',
                style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
            ),
        ],
      ]),
    );
  }
}

// ── Next levels + size of the next buy ───────────────────────────────────────
class _LevelsCard extends StatelessWidget {
  final DtsState s;
  const _LevelsCard({required this.s});

  @override
  Widget build(BuildContext context) => _Card(
    title: '🎯 Levels for the next daily close',
    child: Column(children: [for (final sym in dtsSymbols) _row(sym, s.symbolInfo[sym] ?? const {})]),
  );

  Widget _row(String sym, Map<String, dynamic> i) {
    final open = s.positions.any((p) => p['symbol'] == sym);
    final on = s.symbols.contains(sym);
    final check = (i['last_check'] as Map?)?.cast<String, dynamic>();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(sym, style: TextStyle(color: on ? AppColors.textPrimary : AppColors.textHint,
            fontWeight: FontWeight.bold, fontSize: 14)),
          const SizedBox(width: 8),
          Text('now ${_price(i['price'] as num?)}', style: const TextStyle(color: AppColors.textSecondary, fontSize: 12)),
          const Spacer(),
          Text(!on ? 'off' : (open ? 'in a trade' : 'waiting'),
            style: TextStyle(color: open ? AppColors.success : AppColors.textSecondary, fontSize: 12)),
        ]),
        Text(open
            ? 'Sells if a daily close ends below ${_price(i['exit_level'] as num?)} (10-day low)'
            : 'Buys if a daily close ends above ${_price(i['entry_level'] as num?)} (20-day high)',
          style: const TextStyle(color: AppColors.textPrimary, fontSize: 12)),
        if (!open && i['next_order_usd'] != null)
          Text('Next buy ≈ ${_usd(i['next_order_usd'] as num?)} of $sym, stop ${i['stop_pct']}% below '
              '— losing at most ${_usd(i['next_risk_usd'] as num?)} if it is hit',
            style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
        if (check != null)
          Text('Last close: ${check['why']}', style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
      ]),
    );
  }
}

// ── Open positions ────────────────────────────────────────────────────────────
class _PositionsCard extends StatelessWidget {
  final DtsState s;
  const _PositionsCard({required this.s});

  @override
  Widget build(BuildContext context) => _Card(
    title: '📂 Open spot positions',
    child: Column(children: [
      for (final p in s.positions)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Row(children: [
            Expanded(child: Text(
              '${p['symbol']} ${p['qty']} @ ${_price(p['entry'] as num?)} (${_usd(p['cost_usd'] as num?)})'
              '   stop ${_price(p['stop'] as num?)} at Binance   since ${_localTime(p['since'] as String?)}',
              style: const TextStyle(color: AppColors.textPrimary, fontSize: 12))),
            Text(_usd(p['pnl_usd'] as num?, sign: true), style: TextStyle(
              color: ((p['pnl_usd'] as num?) ?? 0) >= 0 ? AppColors.success : AppColors.error,
              fontWeight: FontWeight.bold, fontSize: 13)),
          ]),
        ),
    ]),
  );
}

// ── Settings ──────────────────────────────────────────────────────────────────
class _SettingsCard extends StatelessWidget {
  final DtsState s;
  final DtsNotifier ntf;
  const _SettingsCard({required this.s, required this.ntf});

  @override
  Widget build(BuildContext context) => _Card(
    title: '⚙️ Spot bot settings',
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _NumRow(label: 'Capital given to the bot', value: s.capitalUsd, suffix: '\$', enabled: !s.busy,
        fieldKey: const Key('dts_capital'), onChanged: ntf.setCapital,
        note: 'The money the bot may use (\$${dtsCapitalMin.toStringAsFixed(0)} minimum). It never buys for more '
            'than this plus its own results, even if the account holds more.'),
      _NumRow(label: 'Risk per trade', value: s.riskPct, suffix: '%', enabled: !s.busy,
        fieldKey: const Key('dts_risk'), onChanged: ntf.setRisk,
        note: 'Share of the bot\'s equity lost if a stop is hit, Binance fees included. Tested at 2%.'),
      _NumRow(label: 'Max crypto risk, BTC + ETH together', value: s.capPct, suffix: '%', enabled: !s.busy,
        fieldKey: const Key('dts_cap'), onChanged: ntf.setCap,
        note: 'The second open trade only gets what is left of this cap.'),
      const SizedBox(height: 8),
      Wrap(spacing: 6, children: [
        for (final sym in dtsSymbols)
          FilterChip(
            label: Text(sym, style: TextStyle(fontSize: 12,
              color: s.symbols.contains(sym) ? AppColors.background : AppColors.textPrimary)),
            selected: s.symbols.contains(sym), showCheckmark: false,
            selectedColor: AppColors.primary, backgroundColor: AppColors.surface,
            onSelected: s.busy ? null : (_) => ntf.toggleSymbol(sym),
          ),
      ]),
      const SizedBox(height: 12),
      Row(children: [
        Expanded(child: ElevatedButton.icon(
          onPressed: (s.dirty && !s.busy) ? ntf.save : null,
          icon: const Icon(Icons.save),
          label: Text(s.busy ? 'Saving…' : 'Save spot settings'),
          style: ElevatedButton.styleFrom(backgroundColor: AppColors.primary, foregroundColor: AppColors.background,
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

class _NumRow extends StatefulWidget {
  final String label, suffix, note;
  final double value;
  final bool enabled;
  final Key fieldKey;
  final double Function(double) onChanged;
  const _NumRow({required this.label, required this.value, required this.suffix, required this.note,
    required this.enabled, required this.fieldKey, required this.onChanged});
  @override
  State<_NumRow> createState() => _NumRowState();
}

class _NumRowState extends State<_NumRow> {
  final _text = TextEditingController();
  final _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _text.text = widget.value.toString();
    _focus.addListener(() { if (!_focus.hasFocus) _apply(); });
  }

  @override
  void didUpdateWidget(covariant _NumRow old) {
    super.didUpdateWidget(old);
    if (!_focus.hasFocus) _text.text = widget.value.toString();
  }

  @override
  void dispose() { _text.dispose(); _focus.dispose(); super.dispose(); }

  void _apply() {
    final v = double.tryParse(_text.text.replaceAll(',', '.'));
    _text.text = (v == null ? widget.value : widget.onChanged(v)).toString();
  }

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 10),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Expanded(child: Text(widget.label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))),
        SizedBox(width: 110, child: TextField(
          key: widget.fieldKey, controller: _text, focusNode: _focus, enabled: widget.enabled,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9.,]'))],
          onSubmitted: (_) => _apply(),
          style: const TextStyle(color: AppColors.textPrimary, fontSize: 13),
          decoration: InputDecoration(suffixText: widget.suffix, isDense: true, border: const OutlineInputBorder()),
        )),
      ]),
      Text(widget.note, style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
    ]),
  );
}

// ── Rules ─────────────────────────────────────────────────────────────────────
class _RulesCard extends StatelessWidget {
  const _RulesCard();
  @override
  Widget build(BuildContext context) => const _Card(
    title: '📘 How it trades',
    child: Text(
      '• Same rules as the Daily Trend tab: buys BTC / ETH when a daily close ends above the 20-day high, '
      'sells when one ends below the 10-day low. Buys only.\n'
      '• Spot: it buys fractions of a coin (Binance minimum order \$5), no leverage, no overnight swap.\n'
      '• Right after each buy, a STOP_LOSS order (2 × daily ATR below) is placed at Binance. If it cannot be '
      'placed, the coins are sold straight back: never a position without a stop.\n'
      '• Tested on real Binance prices 2018–2026 with \$200: same trades as the study, +22% a year on '
      'average with a worst drop near −18% (last 3 years: about +11% a year). Past results, not a promise.',
      style: TextStyle(color: AppColors.textSecondary, fontSize: 12, height: 1.5)),
  );
}

// ── Journal ───────────────────────────────────────────────────────────────────
class _HistoryCard extends ConsumerWidget {
  const _HistoryCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final hist = ref.watch(dtsHistoryProvider);
    return _Card(
      title: '🧾 Buys, sells, stops and skipped signals',
      child: hist.when(
        loading: () => const Text('Loading…', style: TextStyle(color: AppColors.textSecondary, fontSize: 12)),
        error: (_, __) => const Text('Journal unavailable', style: TextStyle(color: AppColors.error, fontSize: 12)),
        data: (rows) => rows.isEmpty
            ? const Text('Nothing yet — the bot acts at most once a day, and only on a 20-day breakout.',
                style: TextStyle(color: AppColors.textSecondary, fontSize: 12))
            : Column(children: [for (final r in rows.cast<Map>()) _row(r.cast<String, dynamic>())]),
      ),
    );
  }

  Widget _row(Map<String, dynamic> r) {
    final ev = r['event'] as String? ?? '';
    final profit = r['profit'] as num?;
    final color = switch (ev) {
      'entry' => AppColors.primary, 'exit' => AppColors.success, 'stop' => AppColors.error,
      'skip' || 'warn' => AppColors.warning, _ => AppColors.error };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(_localTime(r['timestamp'] as String?), style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
          const SizedBox(width: 8),
          Text('${r['symbol']} ${ev.toUpperCase()}', style: TextStyle(color: color, fontWeight: FontWeight.bold, fontSize: 12)),
          const SizedBox(width: 8),
          if (r['qty'] != null && ev == 'entry')
            Text('${r['qty']} @ ${_price(r['price'] as num?)}', style: const TextStyle(color: AppColors.textPrimary, fontSize: 12)),
          const Spacer(),
          if (profit != null)
            Text(_usd(profit, sign: true), style: TextStyle(
              color: profit >= 0 ? AppColors.success : AppColors.error, fontWeight: FontWeight.bold, fontSize: 12)),
        ]),
        if (r['reason'] != null)
          Text(r['reason'] as String, style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
      ]),
    );
  }
}

// ── small shared widgets (same look as the other bot tabs) ────────────────────
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
      Text(title, style: const TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15)),
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
    decoration: BoxDecoration(color: color.withValues(alpha: 0.1), borderRadius: BorderRadius.circular(8),
      border: Border.all(color: color.withValues(alpha: 0.4))),
    child: Text(text, style: TextStyle(color: color, fontSize: 12)),
  );
}

class _Stat extends StatelessWidget {
  final String label, value;
  final Color? color;
  const _Stat({required this.label, required this.value, this.color});
  @override
  Widget build(BuildContext context) => Column(children: [
    Text(value, style: TextStyle(color: color ?? AppColors.textPrimary, fontSize: 14, fontWeight: FontWeight.bold)),
    const SizedBox(height: 2),
    Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 10)),
  ]);
}
