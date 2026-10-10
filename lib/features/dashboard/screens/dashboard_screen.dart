import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/app_colors.dart';
import '../../../features/auth/providers/auth_provider.dart';
import '../providers/market_provider.dart';
import '../providers/stats_provider.dart';
import 'coin_detail_screen.dart';
import '../../../widgets/shimmer_card.dart';

// ── Tableau de bord — refait le 2026-09-26 ───────────────────────────────────
// Compte, performance par période (jour / semaine / mois / 30 j / tout), par
// bot et par symbole, courbe du solde, risque en cours, calendrier économique
// et titres. Tout vient du backend (/dashboard/*), calculé sur l'historique
// MT5 du compte connecté. Les "prédictions LSTM" qui étaient ici étaient des
// valeurs fixes écrites dans le code (mock) : retirées.

const _periodNames = {
  'today': 'Today',
  'week':  'This week',
  'month': 'This month',
  'd30':   '30 days',
  'all':   'All time',
};

const _botNames = {
  'smc':    'Scalping',
  'vp':     'Vol. Profile',
  'dt':     'Daily Trend',
  'manual': 'Manual',
  'other':  'Other',
};

Map<String, dynamic> _m(Object? v) =>
    (v as Map?)?.cast<String, dynamic>() ?? const <String, dynamic>{};

List<Map<String, dynamic>> _l(Object? v) => ((v as List?) ?? const [])
    .map((e) => (e as Map).cast<String, dynamic>()).toList();

num _n(Object? v) => (v as num?) ?? 0;

String _money(num v) => '${v >= 0 ? '+' : '-'}\$${v.abs().toStringAsFixed(2)}';

String _usd(num v) => '\$${v.toStringAsFixed(2)}';

Color _pnl(num v) => v >= 0 ? AppColors.success : AppColors.error;

String _two(int n) => n.toString().padLeft(2, '0');

const _days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

String _dayTime(DateTime t) => '${_days[t.weekday - 1]} ${_two(t.hour)}:${_two(t.minute)}';

DateTime? _parse(Object? iso) => iso == null ? null : DateTime.tryParse(iso.toString())?.toLocal();

String _until(DateTime t) {
  final d = t.difference(DateTime.now());
  if (d.isNegative) {
    final m = -d.inMinutes;
    return m < 1 ? 'now' : '$m min ago';
  }
  if (d.inMinutes < 60) return 'in ${d.inMinutes} min';
  if (d.inHours < 24) return 'in ${d.inHours}h${_two(d.inMinutes % 60)}';
  return 'in ${d.inDays}d ${d.inHours % 24}h';
}

String _ago(DateTime t) {
  final d = DateTime.now().difference(t);
  if (d.inMinutes < 60) return '${d.inMinutes.clamp(0, 59)} min ago';
  if (d.inHours < 24) return '${d.inHours}h ago';
  return '${d.inDays}d ago';
}

class DashboardScreen extends ConsumerStatefulWidget {
  const DashboardScreen({super.key});
  @override
  ConsumerState<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends ConsumerState<DashboardScreen> {
  String _period = 'week';

  @override
  Widget build(BuildContext context) {
    final summary = ref.watch(dashboardSummaryProvider);
    final news    = ref.watch(dashboardNewsProvider).valueOrNull;
    final markets = ref.watch(marketsProvider);
    final d       = summary.valueOrNull;
    final period  = _m(_m(d?['periods'])[_period]);

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.background,
        elevation: 0,
        title: const Text(
          ' CryptoOracle',
          style: TextStyle(color: AppColors.primary, fontWeight: FontWeight.bold, fontSize: 20),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.logout, color: AppColors.textSecondary),
            onPressed: () async {
              await ref.read(authProvider.notifier).logout();
            },
          ),
        ],
      ),
      body: RefreshIndicator(
        color: AppColors.primary,
        onRefresh: () {
          ref.invalidate(dashboardSummaryProvider);
          ref.invalidate(dashboardNewsProvider);
          return ref.refresh(marketsProvider.future);
        },
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (d == null)
                const ShimmerList(count: 3)
              else ...[
                if (d['error'] != null)
                  const _Banner(
                    text: 'Backend unreachable — showing the last known figures',
                    color: AppColors.warning,
                  ),
                if (d['account'] == null)
                  const _Banner(
                    text: 'No account data yet — is the backend running?',
                    color: AppColors.error,
                  )
                else ...[
                  _AccountCard(d: d),
                  const SizedBox(height: 16),
                  _PeriodChips(
                    selected: _period,
                    onSelect: (p) => setState(() => _period = p),
                  ),
                  const SizedBox(height: 10),
                  _PerformanceCard(p: period, label: _periodNames[_period]!),
                  const SizedBox(height: 12),
                  _BalanceCard(curve: _l(d['curve'])),
                  const SizedBox(height: 12),
                  _SymbolsCard(rows: _l(period['by_symbol']), label: _periodNames[_period]!),
                  const SizedBox(height: 12),
                  _RiskCard(r: _m(d['risk']), streak: _m(d['streak'])),
                ],
              ],
              const SizedBox(height: 12),
              _NewsCard(news: news),
              const SizedBox(height: 12),
              _HeadlinesCard(news: news),
              const SizedBox(height: 24),

              // ── Live Prices ──────────────────────────────────
              const _SectionTitle(title: '📈 Live Prices', subtitle: 'via CoinGecko'),
              const SizedBox(height: 12),
              markets.when(
                // rafraîchi toutes les 30 s : un échec ponctuel de CoinGecko
                // garde les derniers prix au lieu d'afficher une erreur
                skipError: true,
                loading: () => const ShimmerList(count: 5),
                error:   (e, _) => _ErrorCard(message: e.toString()),
                data:    (data) => Column(
                  children: data.map((coin) => _CoinCard(coin: coin)).toList(),
                ),
              ),
              const SizedBox(height: 24),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Shared pieces ────────────────────────────────────────
class _Card extends StatelessWidget {
  final String title;
  final String? trailing;
  final Widget child;
  const _Card({required this.title, required this.child, this.trailing});

  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(16),
    decoration: BoxDecoration(
      color: AppColors.cardBackground,
      borderRadius: BorderRadius.circular(14),
      border: Border.all(color: AppColors.cardBorder, width: 0.5),
    ),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(title, style: const TextStyle(
        color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15)),
      // sous le titre plutôt qu'à côté : tient sur un écran de téléphone
      if (trailing != null)
        Padding(
          padding: const EdgeInsets.only(top: 2),
          child: Text(trailing!, maxLines: 1, overflow: TextOverflow.ellipsis,
            style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
        ),
      const SizedBox(height: 10),
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
    margin: const EdgeInsets.only(bottom: 12),
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(
      color: color.withValues(alpha: 0.1),
      borderRadius: BorderRadius.circular(8),
      border: Border.all(color: color.withValues(alpha: 0.4)),
    ),
    child: Text(text, style: TextStyle(color: color, fontSize: 12)),
  );
}

class _Kv extends StatelessWidget {
  final String label, value;
  final Color? color;
  const _Kv(this.label, this.value, {this.color});

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
      const SizedBox(height: 2),
      Text(value, style: TextStyle(
        color: color ?? AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 14)),
    ],
  );
}

class _Tile extends StatelessWidget {
  final String label, value;
  final Color? color;
  const _Tile(this.label, this.value, {this.color});

  @override
  Widget build(BuildContext context) => Container(
    width: 100,
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
    decoration: BoxDecoration(
      color: AppColors.surface,
      borderRadius: BorderRadius.circular(8),
    ),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 10)),
      const SizedBox(height: 3),
      Text(value, style: TextStyle(
        color: color ?? AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 13)),
    ]),
  );
}

// ── Account ──────────────────────────────────────────────
class _AccountCard extends StatelessWidget {
  final Map<String, dynamic> d;
  const _AccountCard({required this.d});

  @override
  Widget build(BuildContext context) {
    final a     = _m(d['account']);
    final today = _m(_m(d['periods'])['today']);
    final demo  = a['demo'] == true;
    final fl    = _n(a['floating']);
    final tNet  = _n(today['net']);
    final upd   = _parse(d['generated']);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [AppColors.primary.withValues(alpha: 0.16), AppColors.cardBackground],
          begin: Alignment.topLeft, end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.primary.withValues(alpha: 0.3)),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          const Text('Balance', style: TextStyle(color: AppColors.textSecondary, fontSize: 13)),
          const Spacer(),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
              color: (demo ? AppColors.success : AppColors.error).withValues(alpha: 0.15),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Text(demo ? 'DEMO ${a['login']}' : 'REAL ${a['login']}',
              style: TextStyle(color: demo ? AppColors.success : AppColors.error,
                fontSize: 11, fontWeight: FontWeight.bold)),
          ),
        ]),
        const SizedBox(height: 4),
        Text(_usd(_n(a['balance'])), style: const TextStyle(
          color: AppColors.textPrimary, fontSize: 30, fontWeight: FontWeight.bold)),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(child: _Kv('Equity', _usd(_n(a['equity'])))),
          Expanded(child: _Kv('Open P/L', _money(fl), color: _pnl(fl))),
          Expanded(child: _Kv('Today', _money(tNet), color: _pnl(tNet))),
        ]),
        if (upd != null) ...[
          const SizedBox(height: 10),
          Text('updated ${_two(upd.hour)}:${_two(upd.minute)}:${_two(upd.second)} · auto 30 s'
              '${a['margin_level'] != null ? '  ·  margin level ${_n(a['margin_level']).toStringAsFixed(0)}%' : ''}',
            style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
        ],
      ]),
    );
  }
}

class _PeriodChips extends StatelessWidget {
  final String selected;
  final ValueChanged<String> onSelect;
  const _PeriodChips({required this.selected, required this.onSelect});

  @override
  Widget build(BuildContext context) => Wrap(spacing: 6, runSpacing: 6, children: [
    for (final e in _periodNames.entries)
      ChoiceChip(
        label: Text(e.value, style: TextStyle(
          fontSize: 12,
          color: e.key == selected ? AppColors.background : AppColors.textPrimary,
        )),
        selected: e.key == selected,
        showCheckmark: false,
        selectedColor: AppColors.primary,
        backgroundColor: AppColors.surface,
        onSelected: (_) => onSelect(e.key),
      ),
  ]);
}

// ── Performance ──────────────────────────────────────────
class _PerformanceCard extends StatelessWidget {
  final Map<String, dynamic> p;
  final String label;
  const _PerformanceCard({required this.p, required this.label});

  @override
  Widget build(BuildContext context) {
    final n = _n(p['trades']);
    if (n == 0) {
      return _Card(
        title: '📊 Performance — $label',
        child: const Text('No closed trade in this period',
          style: TextStyle(color: AppColors.textSecondary)),
      );
    }
    final net  = _n(p['net']);
    final ret  = p['return_pct'] as num?;
    final dep  = _n(p['deposits']);
    final wr   = p['win_rate'] as num?;
    final pf   = p['profit_factor'] as num?;
    final bots = _m(p['by_bot']);
    return _Card(
      title: '📊 Performance — $label',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
          Text(_money(net), style: TextStyle(
            color: _pnl(net), fontSize: 26, fontWeight: FontWeight.bold)),
          const SizedBox(width: 10),
          if (ret != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Text('${ret >= 0 ? '+' : ''}${ret.toStringAsFixed(1)}%',
                style: TextStyle(color: _pnl(ret), fontSize: 14, fontWeight: FontWeight.w600)),
            ),
        ]),
        if (dep != 0)
          Text('deposits in this period: ${_money(dep)} — not counted as profit',
            style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
        const SizedBox(height: 12),
        Wrap(spacing: 8, runSpacing: 8, children: [
          _Tile('Trades', '$n  (${p['wins']}W / ${p['losses']}L)'),
          _Tile('Win rate', wr == null ? '—' : '${wr.toStringAsFixed(1)}%'),
          _Tile('Profit factor', pf == null ? '—' : pf.toStringAsFixed(2),
            color: pf == null ? null : (pf >= 1 ? AppColors.success : AppColors.error)),
          _Tile('Avg / trade', _money(_n(p['expectancy'])), color: _pnl(_n(p['expectancy']))),
          _Tile('Avg win', p['avg_win'] == null ? '—' : _money(_n(p['avg_win'])),
            color: AppColors.success),
          _Tile('Avg loss', p['avg_loss'] == null ? '—' : _money(_n(p['avg_loss'])),
            color: AppColors.error),
          _Tile('Best', _money(_n(p['best'])), color: _pnl(_n(p['best']))),
          _Tile('Worst', _money(_n(p['worst'])), color: _pnl(_n(p['worst']))),
        ]),
        if (bots.isNotEmpty) ...[
          const Divider(color: AppColors.surface, height: 24),
          const Text('By bot', style: TextStyle(color: AppColors.textSecondary, fontSize: 12,
            fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          for (final e in bots.entries) _row(_botNames[e.key] ?? e.key, _m(e.value)),
        ],
      ]),
    );
  }

  Widget _row(String name, Map<String, dynamic> v) {
    final net = _n(v['net']);
    final wr  = v['win_rate'] as num?;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(children: [
        Expanded(child: Text(name,
          style: const TextStyle(color: AppColors.textPrimary, fontSize: 13))),
        SizedBox(width: 56, child: Text('${v['trades']} tr', textAlign: TextAlign.right,
          style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))),
        SizedBox(width: 50, child: Text(wr == null ? '—' : '${wr.toStringAsFixed(0)}%',
          textAlign: TextAlign.right,
          style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))),
        SizedBox(width: 80, child: Text(_money(net), textAlign: TextAlign.right,
          style: TextStyle(color: _pnl(net), fontWeight: FontWeight.bold, fontSize: 12))),
      ]),
    );
  }
}

// ── Balance curve ────────────────────────────────────────
class _BalanceCard extends StatelessWidget {
  final List<Map<String, dynamic>> curve;
  const _BalanceCard({required this.curve});

  @override
  Widget build(BuildContext context) {
    if (curve.length < 2) return const SizedBox.shrink();
    final vals  = curve.map((c) => _n(c['balance']).toDouble()).toList();
    final first = vals.first, last = vals.last;
    final lo    = vals.reduce((a, b) => a < b ? a : b);
    final hi    = vals.reduce((a, b) => a > b ? a : b);
    final pad   = (hi - lo).abs() < 1 ? 1.0 : (hi - lo) * 0.1;
    final color = last >= first ? AppColors.success : AppColors.error;
    return _Card(
      title: '💰 Balance — last 30 days',
      trailing: '${_usd(first)} → ${_usd(last)}',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(
          height: 140,
          child: LineChart(
            LineChartData(
              minY: lo - pad,
              maxY: hi + pad,
              gridData: const FlGridData(show: false),
              titlesData: const FlTitlesData(show: false),
              borderData: FlBorderData(show: false),
              lineTouchData: LineTouchData(
                touchTooltipData: LineTouchTooltipData(
                  getTooltipItems: (spots) => spots.map((s) {
                    final c = curve[s.x.toInt()];
                    return LineTooltipItem('${c['date']}\n${_usd(s.y)}',
                      const TextStyle(color: AppColors.textPrimary, fontSize: 11));
                  }).toList(),
                ),
              ),
              lineBarsData: [
                LineChartBarData(
                  spots: [for (var i = 0; i < vals.length; i++) FlSpot(i.toDouble(), vals[i])],
                  isCurved: false,
                  color: color,
                  barWidth: 2,
                  dotData: const FlDotData(show: false),
                  belowBarData: BarAreaData(show: true, color: color.withValues(alpha: 0.1)),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 6),
        const Text('End-of-day balance; deposits show up as steps.',
          style: TextStyle(color: AppColors.textHint, fontSize: 10)),
      ]),
    );
  }
}

// ── Per symbol ───────────────────────────────────────────
class _SymbolsCard extends StatelessWidget {
  final List<Map<String, dynamic>> rows;
  final String label;
  const _SymbolsCard({required this.rows, required this.label});

  @override
  Widget build(BuildContext context) {
    if (rows.isEmpty) return const SizedBox.shrink();
    final maxAbs = rows.map((r) => _n(r['net']).abs()).fold<num>(0, (a, b) => a > b ? a : b);
    return _Card(
      title: '🎯 Per symbol — $label',
      child: Column(children: [
        for (final r in rows) _row(r, maxAbs == 0 ? 1 : maxAbs),
      ]),
    );
  }

  Widget _row(Map<String, dynamic> r, num maxAbs) {
    final net = _n(r['net']);
    final wr  = r['win_rate'] as num?;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Column(children: [
        Row(children: [
          Expanded(child: Text('${r['symbol']}', style: const TextStyle(
            color: AppColors.textPrimary, fontWeight: FontWeight.w600, fontSize: 13))),
          Text('${r['trades']} tr · ${wr == null ? '—' : '${wr.toStringAsFixed(0)}%'}',
            style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
          SizedBox(width: 84, child: Text(_money(net), textAlign: TextAlign.right,
            style: TextStyle(color: _pnl(net), fontWeight: FontWeight.bold, fontSize: 13))),
        ]),
        const SizedBox(height: 4),
        ClipRRect(
          borderRadius: BorderRadius.circular(3),
          child: LinearProgressIndicator(
            value: (net.abs() / maxAbs).toDouble(),
            minHeight: 4,
            backgroundColor: AppColors.surface,
            valueColor: AlwaysStoppedAnimation(_pnl(net)),
          ),
        ),
      ]),
    );
  }
}

// ── Risk ─────────────────────────────────────────────────
class _RiskCard extends StatelessWidget {
  final Map<String, dynamic> r;
  final Map<String, dynamic> streak;
  const _RiskCard({required this.r, required this.streak});

  @override
  Widget build(BuildContext context) {
    final open    = _n(r['open_positions']);
    final atRisk  = _n(r['at_risk']);
    final pct     = r['at_risk_pct'] as num?;
    final locked  = _n(r['locked']);
    final noStop  = _n(r['no_stop']);
    final byBot   = _m(r['by_bot']);
    final dd      = _m(r['max_drawdown_30d']);
    final riskCol = pct == null ? AppColors.textPrimary
        : pct >= 30 ? AppColors.error : pct >= 15 ? AppColors.warning : AppColors.textPrimary;
    final positions = _l(r['positions'])
      ..sort((a, b) => _n(a['at_sl'] ?? -1e9).compareTo(_n(b['at_sl'] ?? -1e9)));

    return _Card(
      title: '🛡 Risk now',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (noStop > 0)
          _Banner(
            text: '⚠ $noStop open trade${noStop > 1 ? 's' : ''} WITHOUT a stop loss — '
                'the loss is not limited. Check it in MT5.',
            color: AppColors.error,
          ),
        _line('Open positions', '$open'
            '${byBot.isEmpty ? '' : '  (${byBot.entries.map((e) => '${_botNames[e.key] ?? e.key} ${_m(e.value)['open']}').join(' · ')})'}'),
        _line('If every stop is hit',
            open == 0 ? '—' : '-\$${atRisk.toStringAsFixed(2)}'
                '${pct == null ? '' : '  (${pct.toStringAsFixed(1)}% of balance)'}',
            color: riskCol),
        if (locked > 0)
          _line('Profit locked by trailing stops', _money(locked), color: AppColors.success),
        if (r['drawdown_from_peak_pct'] != null)
          _line('Below peak equity',
              '${_n(r['drawdown_from_peak_pct']).toStringAsFixed(1)}%  (peak ${_usd(_n(r['peak_equity']))})'),
        if (r['halt_equity'] != null)
          _line('Scalping halts at',
              '${_usd(_n(r['halt_equity']))}  (-${_n(r['halt_pct']).toStringAsFixed(0)}% from peak)'),
        _line('Worst drawdown, 30 days',
            '-\$${_n(dd['usd']).toStringAsFixed(2)}  (${_n(dd['pct']).toStringAsFixed(1)}%)'),
        if (streak['kind'] != null)
          _line('Current streak',
              '${streak['count']} ${streak['kind'] == 'win' ? 'win' : 'loss'}${_n(streak['count']) > 1 ? (streak['kind'] == 'win' ? 's' : 'es') : ''} in a row',
              color: streak['kind'] == 'win' ? AppColors.success : AppColors.error),
        const Divider(color: AppColors.surface, height: 20),
        _line('Scalping limits',
            'max risk ${r['smc_risk_cap_pct'] == null ? 'default (15%/7%)' : '${_n(r['smc_risk_cap_pct']).toStringAsFixed(0)}%'}'
            ' · daily loss ${_limit(r['smc_daily_limit_pct'])}'),
        _line('Vol. Profile limits',
            'max risk ${_n(r['vp_risk_cap_pct']).toStringAsFixed(0)}%'
            ' · daily loss ${_limit(r['vp_daily_limit_pct'])}'),
        if (positions.isNotEmpty) ...[
          const Divider(color: AppColors.surface, height: 20),
          for (final p in positions) _position(p),
        ],
      ]),
    );
  }

  static String _limit(Object? v) {
    final n = _n(v);
    return n <= 0 ? 'OFF' : '${n.toStringAsFixed(0)}%';
  }

  Widget _line(String k, String v, {Color? color}) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 3),
    child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      SizedBox(width: 150, child: Text(k,
        style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))),
      Expanded(child: Text(v, textAlign: TextAlign.right, style: TextStyle(
        color: color ?? AppColors.textPrimary, fontSize: 12, fontWeight: FontWeight.w600))),
    ]),
  );

  Widget _position(Map<String, dynamic> p) {
    final atSl = p['at_sl'] as num?;
    final pl   = _n(p['profit']);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(children: [
        Expanded(child: Text(
          '${_botNames[p['bot']] ?? p['bot']} · ${p['symbol']} ${(p['side'] as String? ?? '').toUpperCase()} ${p['volume']}',
          style: const TextStyle(color: AppColors.textPrimary, fontSize: 12))),
        Text(atSl == null ? 'NO STOP' : 'stop ${_money(atSl)}',
          style: TextStyle(
            color: atSl == null ? AppColors.error
                : (atSl >= 0 ? AppColors.success : AppColors.textSecondary),
            fontSize: 11, fontWeight: atSl == null ? FontWeight.bold : FontWeight.normal)),
        SizedBox(width: 72, child: Text(_money(pl), textAlign: TextAlign.right,
          style: TextStyle(color: _pnl(pl), fontWeight: FontWeight.bold, fontSize: 12))),
      ]),
    );
  }
}

// ── Economic calendar ────────────────────────────────────
class _NewsCard extends StatelessWidget {
  final Map<String, dynamic>? news;
  const _NewsCard({required this.news});

  @override
  Widget build(BuildContext context) {
    final n = news;
    if (n == null) {
      return const _Card(title: '📅 Economic news', child: LinearProgressIndicator(minHeight: 2));
    }
    final curs     = ((n['currencies'] as List?) ?? const [])
        .map((e) => '$e').where((c) => c != 'XAG' && c != 'XAU').toList();
    final upcoming = _l(n['upcoming']);
    final recent   = _l(n['recent']);
    return _Card(
      title: '📅 Economic news',
      trailing: curs.isEmpty ? null : curs.join(' · '),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (upcoming.isEmpty)
          Text('No high or medium-impact ${curs.join('/')} event left this week.\n'
              '${n['calendar_note'] ?? ''}',
            style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))
        else
          for (final e in upcoming.take(10)) _event(e, upcoming: true),
        if (recent.isNotEmpty) ...[
          const Divider(color: AppColors.surface, height: 20),
          const Text('Released in the last 24 h',
            style: TextStyle(color: AppColors.textSecondary, fontSize: 11,
              fontWeight: FontWeight.w600)),
          for (final e in recent.take(5)) _event(e, upcoming: false),
        ],
        const SizedBox(height: 8),
        const Text('Both bots pause new trades from 15 min before to 18 min after '
            'high-impact news (10 → 5 min for medium); Scalping also cancels its '
            'waiting limit orders, Volume Profile closes its open trades 15 min before '
            '(both switchable in its tab).',
          style: TextStyle(color: AppColors.textHint, fontSize: 10)),
      ]),
    );
  }

  Widget _event(Map<String, dynamic> e, {required bool upcoming}) {
    final t     = _parse(e['time']);
    final high  = e['impact'] == 'High';
    final soon  = upcoming && t != null && t.difference(DateTime.now()).inMinutes <= 60;
    final facts = [
      if (e['actual'] != null) 'actual ${e['actual']}',
      if (e['forecast'] != null) 'forecast ${e['forecast']}',
      if (e['previous'] != null) 'previous ${e['previous']}',
    ].join(' · ');
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 4),
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: soon ? AppColors.warning.withValues(alpha: 0.08) : AppColors.surface,
        borderRadius: BorderRadius.circular(8),
        border: soon ? Border.all(color: AppColors.warning.withValues(alpha: 0.5)) : null,
      ),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
          width: 8, height: 8, margin: const EdgeInsets.only(top: 4, right: 8),
          decoration: BoxDecoration(
            color: high ? AppColors.error : AppColors.warning, shape: BoxShape.circle),
        ),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${e['currency']}  ${e['title']}', style: const TextStyle(
            color: AppColors.textPrimary, fontSize: 13, fontWeight: FontWeight.w600)),
          const SizedBox(height: 2),
          Text(
            '${t == null ? '' : '${_dayTime(t)} · ${_until(t)}'}'
            '  ·  ${high ? 'HIGH' : 'medium'} impact',
            style: TextStyle(color: soon ? AppColors.warning : AppColors.textSecondary,
              fontSize: 11)),
          if (facts.isNotEmpty)
            Text(facts, style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
          Text('moves: ${((e['symbols'] as List?) ?? const []).join(', ')}',
            style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
        ])),
      ]),
    );
  }
}

// ── Headlines ────────────────────────────────────────────
class _HeadlinesCard extends StatelessWidget {
  final Map<String, dynamic>? news;
  const _HeadlinesCard({required this.news});

  @override
  Widget build(BuildContext context) {
    final items = _l(news?['headlines']);
    if (items.isEmpty) return const SizedBox.shrink();
    return _Card(
      title: '📰 Market headlines',
      trailing: 'CoinDesk · Cointelegraph · FXStreet',
      child: Column(children: [
        for (final h in items.take(10))
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Padding(
                padding: EdgeInsets.only(top: 3, right: 8),
                child: Icon(Icons.article_outlined, size: 14, color: AppColors.textSecondary),
              ),
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('${h['title']}', maxLines: 2, overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: AppColors.textPrimary, fontSize: 12)),
                Text('${h['source']} · ${_parse(h['time']) == null ? '' : _ago(_parse(h['time'])!)}',
                  style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
              ])),
            ]),
          ),
      ]),
    );
  }
}

// ── Section Title ────────────────────────────────────────
class _SectionTitle extends StatelessWidget {
  final String title;
  final String subtitle;
  const _SectionTitle({required this.title, required this.subtitle});

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                  color: AppColors.textPrimary,
                  fontSize: 16,
                  fontWeight: FontWeight.bold)),
        ),
        Text(subtitle,
            style: const TextStyle(
                color: AppColors.textSecondary, fontSize: 12)),
      ],
    );
  }
}

// ── Coin Card ────────────────────────────────────────────
class _CoinCard extends StatelessWidget {
  final Map<String, dynamic> coin;
  const _CoinCard({required this.coin});

  @override
  Widget build(BuildContext context) {
    final change = (coin['price_change_percentage_24h'] as num?)?.toDouble() ?? 0;
    final isUp   = change >= 0;
    final color  = isUp ? AppColors.success : AppColors.error;
    final price  = (coin['current_price'] as num).toDouble();

    return GestureDetector(
      onTap: () => Navigator.push(
        context,
        MaterialPageRoute(builder: (_) => CoinDetailScreen(coin: coin)),
      ),
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        decoration: BoxDecoration(
          color: AppColors.cardBackground,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: AppColors.cardBorder, width: 0.5),
        ),
        child: Row(
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(20),
              child: Image.network(
                coin['image'] as String,
                width: 36, height: 36,
                errorBuilder: (_, __, ___) =>
                    const Icon(Icons.currency_bitcoin, color: AppColors.primary),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(coin['name'] as String,
                      style: const TextStyle(
                          color: AppColors.textPrimary,
                          fontWeight: FontWeight.w600)),
                  Text((coin['symbol'] as String).toUpperCase(),
                      style: const TextStyle(
                          color: AppColors.textSecondary, fontSize: 12)),
                ],
              ),
            ),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(
                  '\$${_fmt(price)}',
                  style: const TextStyle(
                      color: AppColors.textPrimary,
                      fontWeight: FontWeight.bold,
                      fontSize: 15),
                ),
                Text(
                  '${isUp ? '+' : ''}${change.toStringAsFixed(2)}%',
                  style: TextStyle(color: color, fontSize: 12),
                ),
              ],
            ),
            const SizedBox(width: 12),
            _Sparkline(
              prices: List<double>.from(
                (coin['sparkline_in_7d']?['price'] as List? ?? [])
                    .map((e) => (e as num).toDouble()),
              ),
              isUp: isUp,
            ),
          ],
        ),
      ),
    );
  }

  String _fmt(double price) {
    if (price >= 1000) {
      return price.toStringAsFixed(0).replaceAllMapped(
          RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},');
    }
    if (price >= 1) return price.toStringAsFixed(2);
    return price.toStringAsFixed(4);
  }
}

// ── Mini Sparkline ───────────────────────────────────────
class _Sparkline extends StatelessWidget {
  final List<double> prices;
  final bool isUp;
  const _Sparkline({required this.prices, required this.isUp});

  @override
  Widget build(BuildContext context) {
    if (prices.isEmpty) return const SizedBox(width: 60);
    return SizedBox(
      width: 60,
      height: 32,
      child: CustomPaint(
        painter: _SparklinePainter(
          prices: prices,
          color: isUp ? AppColors.success : AppColors.error,
        ),
      ),
    );
  }
}

class _SparklinePainter extends CustomPainter {
  final List<double> prices;
  final Color color;
  _SparklinePainter({required this.prices, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    if (prices.length < 2) return;
    final min = prices.reduce((a, b) => a < b ? a : b);
    final max = prices.reduce((a, b) => a > b ? a : b);
    final range = max - min == 0 ? 1.0 : max - min;

    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke;

    final path = Path();
    for (int i = 0; i < prices.length; i++) {
      final x = i / (prices.length - 1) * size.width;
      final y = size.height - ((prices[i] - min) / range) * size.height;
      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(_) => false;
}

// ── Error Card ───────────────────────────────────────────
class _ErrorCard extends StatelessWidget {
  final String message;
  const _ErrorCard({required this.message});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.error.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.error.withValues(alpha: 0.3)),
      ),
      child: const Row(
        children: [
          Icon(Icons.wifi_off, color: AppColors.error),
          SizedBox(width: 12),
          Expanded(
            child: Text('Could not load market data.\nPull down to retry.',
                style: TextStyle(color: AppColors.textSecondary)),
          ),
        ],
      ),
    );
  }
}
