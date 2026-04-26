import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/app_colors.dart';
import '../../../widgets/price_chart.dart';
import '../../../widgets/loading_indicator.dart';
import '../providers/smc_provider.dart';
import 'smc_screen.dart';

class CoinDetailScreen extends ConsumerWidget {
  final Map<String, dynamic> coin;
  const CoinDetailScreen({super.key, required this.coin});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final prices = List<double>.from(
      (coin['sparkline_in_7d']?['price'] as List? ?? [])
          .map((e) => (e as num).toDouble()),
    );
    final change = (coin['price_change_percentage_24h'] as num?)?.toDouble() ?? 0;
    final isUp   = change >= 0;
    final color  = isUp ? AppColors.success : AppColors.error;
    final symbol = (coin['symbol'] as String).toUpperCase();

    // Map CoinGecko symbol → backend symbol
    const symbolMap = {
      'BTC': 'BTC', 'ETH': 'ETH', 'SOL': 'SOL', 'BNB': 'BNB', 'XRP': 'XRP',
    };
    final backendSymbol = symbolMap[symbol] ?? symbol;

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.background,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back, color: AppColors.textPrimary),
          onPressed: () => Navigator.pop(context),
        ),
        title: Row(children: [
          Image.network(coin['image'] as String, width: 28, height: 28,
            errorBuilder: (_, __, ___) =>
              const Icon(Icons.currency_bitcoin, color: AppColors.primary)),
          const SizedBox(width: 8),
          Text(coin['name'] as String,
            style: const TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.bold)),
        ]),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Price header
            Text('\$${_fmtPrice((coin['current_price'] as num).toDouble())}',
              style: const TextStyle(
                color: AppColors.textPrimary, fontSize: 36, fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Row(children: [
              Icon(isUp ? Icons.trending_up : Icons.trending_down, color: color, size: 18),
              const SizedBox(width: 4),
              Text('${isUp ? '+' : ''}${change.toStringAsFixed(2)}% (24h)',
                style: TextStyle(color: color, fontSize: 14)),
            ]),
            const SizedBox(height: 24),

            // Chart
            Container(
              height: 200,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppColors.cardBackground,
                borderRadius: BorderRadius.circular(16),
              ),
              child: PriceChart(prices: prices, isPositive: isUp),
            ),
            const SizedBox(height: 8),
            const Text('7-day price chart',
              style: TextStyle(color: AppColors.textHint, fontSize: 11)),
            const SizedBox(height: 24),

            // Market stats
            _SectionTitle('📊 Market Stats'),
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: AppColors.cardBackground, borderRadius: BorderRadius.circular(12)),
              child: Column(children: [
                _StatRow('Market Cap',   '\$${_fmtLarge((coin['market_cap'] as num).toDouble())}'),
                _StatRow('24h Volume',   '\$${_fmtLarge((coin['total_volume'] as num).toDouble())}'),
                _StatRow('Symbol',       symbol),
              ]),
            ),
            const SizedBox(height: 24),

            // SMC Quick Analysis
            _SectionTitle('🔍 SMC Quick Analysis'),
            _SmcQuickCard(symbol: backendSymbol),
            const SizedBox(height: 32),
          ],
        ),
      ),
    );
  }

  String _fmtPrice(double p) {
    if (p >= 1000) return p.toStringAsFixed(0).replaceAllMapped(
      RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},');
    if (p >= 1) return p.toStringAsFixed(2);
    return p.toStringAsFixed(4);
  }

  String _fmtLarge(double v) {
    if (v >= 1e12) return '${(v / 1e12).toStringAsFixed(2)}T';
    if (v >= 1e9)  return '${(v / 1e9).toStringAsFixed(2)}B';
    if (v >= 1e6)  return '${(v / 1e6).toStringAsFixed(2)}M';
    return v.toStringAsFixed(0);
  }

  Widget _SectionTitle(String t) => Padding(
    padding: const EdgeInsets.only(bottom: 10),
    child: Text(t, style: const TextStyle(
      color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15)));

  Widget _StatRow(String label, String value) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 5),
    child: Row(children: [
      Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 13)),
      const Spacer(),
      Text(value, style: const TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.w600, fontSize: 13)),
    ]));
}

class _SmcQuickCard extends ConsumerWidget {
  final String symbol;
  const _SmcQuickCard({required this.symbol});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Override provider for this symbol
    final analysisAsync = ref.watch(
      FutureProvider.autoDispose<Map<String, dynamic>>((ref) =>
        ref.watch(smcRepositoryProvider).getAnalysis(symbol, '1h')
      )
    );

    return analysisAsync.when(
      loading: () => const Center(child: LoadingIndicator()),
      error: (e, _) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: AppColors.cardBackground, borderRadius: BorderRadius.circular(12)),
        child: Text('SMC unavailable', style: const TextStyle(color: AppColors.textSecondary))),
      data: (data) {
        final bias  = data['bias'] as String;
        final conf  = ((data['confidence'] as num) * 100).toStringAsFixed(0);
        final ote   = data['ote'] as Map<String, dynamic>?;
        final color = bias == 'buy' ? AppColors.success
                    : bias == 'sell' ? AppColors.error : AppColors.warning;
        return Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: AppColors.cardBackground,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: color.withValues(alpha: 0.3)),
          ),
          child: Column(children: [
            Row(children: [
              Text(bias.toUpperCase(),
                style: TextStyle(color: color, fontSize: 20, fontWeight: FontWeight.bold)),
              const Spacer(),
              Text('$conf% confidence',
                style: TextStyle(color: color, fontSize: 13)),
            ]),
            if (ote != null) ...[
              const Divider(color: AppColors.surface, height: 20),
              Row(children: [
                _chip('Entry \$${(ote['entry_price'] as num).toStringAsFixed(2)}', AppColors.primary),
                const SizedBox(width: 8),
                _chip('SL \$${(ote['sl'] as num).toStringAsFixed(2)}', AppColors.error),
                const SizedBox(width: 8),
                _chip('R:R ${ote['rr_ratio']}', AppColors.warning),
              ]),
            ],
          ]),
        );
      },
    );
  }

  Widget _chip(String label, Color color) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
    decoration: BoxDecoration(
      color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(6)),
    child: Text(label, style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.w600)));
}