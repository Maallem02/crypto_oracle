import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/app_colors.dart';
import '../providers/smc_provider.dart';
import '../../../widgets/shimmer_card.dart';

class SmcScreen extends ConsumerWidget {
  const SmcScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final analysis  = ref.watch(smcAnalysisProvider);
    final symbol    = ref.watch(selectedSymbolProvider);
    final timeframe = ref.watch(selectedTimeframeProvider);

    const symbols    = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'XAUUSD', 'XAGUSD', 'GBPJPY'];
    const timeframes = ['5m', '15m', '30m', '1h', '4h'];

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.background,
        elevation: 0,
        title: const Text('SMC Analysis', style: TextStyle(color: AppColors.primary, fontWeight: FontWeight.bold)),
      ),
      body: Column(
        children: [
          // Symbol selector
          SizedBox(
            height: 44,
            child: ListView.builder(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              itemCount: symbols.length,
              itemBuilder: (_, i) {
                final s      = symbols[i];
                final active = s == symbol;
                return GestureDetector(
                  onTap: () => ref.read(selectedSymbolProvider.notifier).state = s,
                  child: Container(
                    margin: const EdgeInsets.only(right: 8),
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                    decoration: BoxDecoration(
                      color: active ? AppColors.primary : AppColors.cardBackground,
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Text(s,
                      style: TextStyle(
                        color: active ? AppColors.background : AppColors.textSecondary,
                        fontWeight: FontWeight.bold, fontSize: 12,
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
          const SizedBox(height: 8),
          // Timeframe selector
          SizedBox(
            height: 36,
            child: ListView.builder(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              itemCount: timeframes.length,
              itemBuilder: (_, i) {
                final tf     = timeframes[i];
                final active = tf == timeframe;
                return GestureDetector(
                  onTap: () => ref.read(selectedTimeframeProvider.notifier).state = tf,
                  child: Container(
                    margin: const EdgeInsets.only(right: 8),
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                    decoration: BoxDecoration(
                      color: active ? AppColors.warning : AppColors.surface,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(tf,
                      style: TextStyle(
                        color: active ? AppColors.background : AppColors.textSecondary,
                        fontWeight: FontWeight.bold, fontSize: 12,
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
          const SizedBox(height: 12),
          // Analysis result
          Expanded(
            child: analysis.when(
              loading: () => const Padding(
  padding: EdgeInsets.all(16),
  child: ShimmerList(count: 4, itemHeight: 80),
),
              error: (e, _) => Center(
                child: Text('Error: $e', style: const TextStyle(color: AppColors.error)),
              ),
              data: (data) => _SmcResult(data: data),
            ),
          ),
        ],
      ),
    );
  }
}

class _SmcResult extends StatelessWidget {
  final Map<String, dynamic> data;
  const _SmcResult({required this.data});

  @override
  Widget build(BuildContext context) {
    final bias       = data['bias'] as String;
    final confidence = ((data['confidence'] as num) * 100).toStringAsFixed(0);
    final structure  = data['structure'] as Map<String, dynamic>;
    final ote        = data['ote'] as Map<String, dynamic>?;
    final obs        = data['order_blocks'] as List;
    final fvgs       = data['fvg'] as List;
    final liquidity  = data['liquidity'] as Map<String, dynamic>;

    final biasColor = bias == 'buy' ? AppColors.success
                    : bias == 'sell' ? AppColors.error
                    : AppColors.warning;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Bias card
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              color: AppColors.cardBackground,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: biasColor.withValues(alpha: 0.4)),
            ),
            child: Column(children: [
              Text(bias.toUpperCase(),
                style: TextStyle(color: biasColor, fontSize: 32, fontWeight: FontWeight.bold)),
              Text('Confidence: $confidence%',
                style: const TextStyle(color: AppColors.textSecondary)),
              const SizedBox(height: 8),
              Text('Trend: ${structure['trend']}  •  CHoCH: ${structure['last_choch'] ?? 'none'}',
                style: const TextStyle(color: AppColors.textSecondary, fontSize: 12)),
            ]),
          ),
          const SizedBox(height: 16),

          // OTE card
          if (ote != null) ...[
            _SectionTitle('🎯 OTE — Optimal Trade Entry'),
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: AppColors.cardBackground,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(children: [
                _Row('Entry',    '\$${(ote['entry_price'] as num).toStringAsFixed(2)}', AppColors.primary),
                _Row('SL',       '\$${(ote['sl'] as num).toStringAsFixed(2)}',          AppColors.error),
                _Row('TP1',      '\$${(ote['tp1'] as num).toStringAsFixed(2)}',         AppColors.success),
                _Row('TP2',      '\$${(ote['tp2'] as num).toStringAsFixed(2)}',         AppColors.success),
                _Row('R:R',      '${ote['rr_ratio']}',                                  AppColors.warning),
                _Row('OB Aligned', ote['ob_aligned'] == true ? '✅' : '❌',            AppColors.textPrimary),
                _Row('In Zone',    ote['in_zone'] == true ? '✅ Price in OTE' : '❌ Wait for retracement', AppColors.textPrimary),
              ]),
            ),
            const SizedBox(height: 16),
          ],

          // Order Blocks
          _SectionTitle('📦 Order Blocks (${obs.length})'),
          ...obs.map((ob) {
            final isBull = ob['type'] == 'bullish';
            return Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              decoration: BoxDecoration(
                color: AppColors.cardBackground,
                borderRadius: BorderRadius.circular(10),
                border: Border.all(
                  color: (isBull ? AppColors.success : AppColors.error).withValues(alpha: 0.3)),
              ),
              child: Row(children: [
                Icon(isBull ? Icons.arrow_upward : Icons.arrow_downward,
                  color: isBull ? AppColors.success : AppColors.error, size: 16),
                const SizedBox(width: 8),
                Text(ob['type'].toString().toUpperCase(),
                  style: TextStyle(
                    color: isBull ? AppColors.success : AppColors.error,
                    fontWeight: FontWeight.bold, fontSize: 12)),
                const Spacer(),
                Text('${ob['low']} — ${ob['high']}',
                  style: const TextStyle(color: AppColors.textSecondary, fontSize: 12)),
                const SizedBox(width: 8),
                Text('str: ${ob['strength']}',
                  style: const TextStyle(color: AppColors.textHint, fontSize: 11)),
              ]),
            );
          }),
          const SizedBox(height: 16),

          // FVG
          _SectionTitle('🔲 Fair Value Gaps (${fvgs.length})'),
          ...fvgs.map((fvg) {
            final isBull = fvg['type'] == 'bullish';
            return Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              decoration: BoxDecoration(
                color: AppColors.cardBackground,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Row(children: [
                Icon(isBull ? Icons.arrow_upward : Icons.arrow_downward,
                  color: isBull ? AppColors.success : AppColors.error, size: 16),
                const SizedBox(width: 8),
                Text(fvg['type'].toString().toUpperCase(),
                  style: TextStyle(
                    color: isBull ? AppColors.success : AppColors.error,
                    fontWeight: FontWeight.bold, fontSize: 12)),
                const Spacer(),
                Text('${fvg['bottom']} — ${fvg['top']}',
                  style: const TextStyle(color: AppColors.textSecondary, fontSize: 12)),
              ]),
            );
          }),
          const SizedBox(height: 16),

          // Liquidity
          _SectionTitle('💧 Liquidity Zones'),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: AppColors.cardBackground, borderRadius: BorderRadius.circular(12)),
            child: Column(children: [
              _Row('Buy-side (above)',  (liquidity['buy_side']  as List).join(' / '), AppColors.success),
              _Row('Sell-side (below)', (liquidity['sell_side'] as List).join(' / '), AppColors.error),
            ]),
          ),
          const SizedBox(height: 32),
        ],
      ),
    );
  }

  Widget _SectionTitle(String t) => Padding(
    padding: const EdgeInsets.only(bottom: 8),
    child: Text(t, style: const TextStyle(
      color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15)),
  );

  Widget _Row(String label, String value, Color color) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Row(children: [
      Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 13)),
      const Spacer(),
      Text(value, style: TextStyle(color: color, fontWeight: FontWeight.w600, fontSize: 13)),
    ]),
  );
}