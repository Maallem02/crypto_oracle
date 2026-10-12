import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/constants/app_colors.dart';
import '../core/network/dio_client.dart';

// ── Espérance d'un bot (2026-10-10) ───────────────────────────────────────────
// Demande utilisateur après la vidéo "4 concepts mathématiques" : le seul
// chiffre qui dit si un bot gagne de l'argent est son ESPÉRANCE (résultat moyen
// par trade, frais compris), jugée sur un GRAND échantillon. Calculée par le
// backend sur les trades réels du compte (/dashboard/expectancy).

final expectancyProvider = StreamProvider.autoDispose<Map<String, dynamic>>((ref) async* {
  final dio = DioClient.create();
  while (true) {
    try {
      final r = await dio.get('/dashboard/expectancy');
      yield (r.data as Map).cast<String, dynamic>();
    } catch (_) {
      yield const {};
    }
    await Future.delayed(const Duration(seconds: 60));
  }
});

String _r(num? v) => v == null ? '--' : '${v >= 0 ? '+' : ''}${v.toStringAsFixed(2)}R';
String _usd(num? v) => v == null ? '--' : '${v >= 0 ? '+' : '-'}\$${v.abs().toStringAsFixed(2)}';

class ExpectancyCard extends ConsumerWidget {
  final String bot;                       // smc | vp | dt
  const ExpectancyCard({super.key, required this.bot});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(expectancyProvider);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(color: AppColors.cardBackground, borderRadius: BorderRadius.circular(12)),
      child: async.when(
        loading: () => const Text('📐 Expectancy — loading…',
          style: TextStyle(color: AppColors.textSecondary, fontSize: 12)),
        error: (_, __) => const Text('📐 Expectancy unavailable',
          style: TextStyle(color: AppColors.error, fontSize: 12)),
        data: (all) {
          final d = ((all['bots'] as Map?)?[bot] as Map?)?.cast<String, dynamic>();
          if (d == null) {
            return const Text('📐 Expectancy unavailable (backend not reachable)',
              style: TextStyle(color: AppColors.textSecondary, fontSize: 12));
          }
          return _body(d);
        },
      ),
    );
  }

  Widget _body(Map<String, dynamic> d) {
    final n = (d['trades_with_r'] as num?)?.toInt() ?? 0;
    final target = (d['sample_target'] as num?)?.toInt() ?? 100;
    final exp = d['expectancy_r'] as num?;
    final verdict = d['verdict'] as String? ?? 'no_trades';
    final (vText, vColor) = switch (verdict) {
      'winning'   => ('Winning system — the whole likely range is above zero', AppColors.success),
      'losing'    => ('Losing system — the whole likely range is below zero', AppColors.error),
      'not_proven'=> ('Not proven yet — it could still be either', AppColors.warning),
      'too_early' => ('Too early to judge — fewer than 30 trades', AppColors.warning),
      _           => ('No trades yet', AppColors.textSecondary),
    };
    final wr = d['win_rate'] as num?;
    final be = d['breakeven_win_rate'] as num?;
    final sampleLabel = n >= target ? 'large enough sample'
        : (n >= 30 ? 'early indication only' : 'too few trades to judge');

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Text('📐 Expectancy — does this bot make money per trade?', style: TextStyle(
        color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15)),
      const SizedBox(height: 10),
      Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(color: vColor.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(6),
          border: Border.all(color: vColor.withValues(alpha: 0.5))),
        child: Text(vText, style: TextStyle(color: vColor, fontWeight: FontWeight.bold, fontSize: 12)),
      ),
      if (n > 0) ...[
        const SizedBox(height: 10),
        Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
          Text(_r(exp), style: TextStyle(fontSize: 26, fontWeight: FontWeight.bold,
            color: (exp ?? 0) >= 0 ? AppColors.success : AppColors.error)),
          const SizedBox(width: 8),
          Padding(padding: const EdgeInsets.only(bottom: 4), child: Text(
            'per trade  (${_usd(d['expectancy_usd'] as num?)})',
            style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))),
        ]),
        if (d['range_low_r'] != null)
          Text('Likely between ${_r(d['range_low_r'] as num?)} and ${_r(d['range_high_r'] as num?)} per trade',
            style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
        const SizedBox(height: 10),
        _line('Win rate', wr == null ? '--' : '${wr.toStringAsFixed(0)}%  (needs '
            '${be == null ? '--' : '${be.toStringAsFixed(0)}%'} to break even)',
          color: (wr != null && be != null) ? (wr >= be ? AppColors.success : AppColors.error) : null),
        _line('Average win / loss', '${_r(d['avg_win_r'] as num?)} / -${(d['avg_loss_r'] as num? ?? 0).toStringAsFixed(2)}R'),
        _line('Result', '${_usd(d['total_usd'] as num?)} over ${d['trades']} trades'),
        // 2026-10-12 : le broker efface le stop des ordres limite exécutés -> stop du journal du bot
        if (((d['r_from_journal'] as num?) ?? 0) > 0 || n < ((d['trades'] as num?) ?? 0))
          _line('Measured in R', '$n of ${d['trades']} trades'
              '${((d['r_from_journal'] as num?) ?? 0) > 0 ? ' (${d['r_from_journal']} limit-order stops from the bot\'s journal)' : ''}'),
        _line('Costs', 'spread ≈ \$${(d['spread_est_usd'] as num? ?? 0).toStringAsFixed(2)} (estimated) · '
            'commission & swap ${_usd(d['commission_swap_usd'] as num?)}'),
        if (d['risk_pct_median'] != null)
          _line('Risk per trade', 'median ${d['risk_pct_median']}%, 9 in 10 under ${d['risk_pct_p90']}%  '
              '(guideline 0.25–2%)',
            color: ((d['risk_pct_median'] as num?) ?? 0) > 2 ? AppColors.warning : null),
      ],
      const SizedBox(height: 10),
      Row(children: [
        Text('Sample: $n of $target trades', style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
        const Spacer(),
        Text(sampleLabel, style: TextStyle(fontSize: 11,
          color: n >= target ? AppColors.success : AppColors.warning)),
      ]),
      const SizedBox(height: 4),
      ClipRRect(
        borderRadius: BorderRadius.circular(4),
        child: LinearProgressIndicator(value: (n / target).clamp(0.0, 1.0), minHeight: 6,
          backgroundColor: AppColors.surface,
          color: n >= target ? AppColors.success : AppColors.warning),
      ),
      if (d['tested'] != null) ...[
        const SizedBox(height: 8),
        Text('Tested on history: ${d['tested']}', style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
      ],
      const SizedBox(height: 4),
      const Text('1R = what a trade risked at its initial stop. Break-even win rate = 1 ÷ (1 + average win ÷ average '
          'loss). One trade means nothing: judge a bot over 100+ trades.',
        style: TextStyle(color: AppColors.textHint, fontSize: 10)),
    ]);
  }

  Widget _line(String label, String value, {Color? color}) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 2),
    child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      SizedBox(width: 130, child: Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 12))),
      Expanded(child: Text(value, style: TextStyle(color: color ?? AppColors.textPrimary, fontSize: 12))),
    ]),
  );
}
