import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/app_colors.dart';
import '../../../features/auth/providers/auth_provider.dart';
import '../providers/market_provider.dart';
import '../../../models/prediction_model.dart';
import 'coin_detail_screen.dart';
import '../../../widgets/shimmer_card.dart';

class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final markets    = ref.watch(marketsProvider);
    final predictions = ref.watch(predictionsProvider);

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.background,
        elevation: 0,
        title: const Text(
          '🔮 CryptoOracle',
          style: TextStyle(
            color: AppColors.primary,
            fontWeight: FontWeight.bold,
            fontSize: 20,
          ),
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
        onRefresh: () => ref.refresh(marketsProvider.future),
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // ── Welcome ──────────────────────────────────────
              Text(
                'Welcome back 👋',
                style: TextStyle(
                  color: AppColors.textSecondary,
                  fontSize: 14,
                ),
              ),
              const SizedBox(height: 4),
              const Text(
                'Live Market Overview',
                style: TextStyle(
                  color: AppColors.textPrimary,
                  fontSize: 22,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 20),

              // ── Live Prices ──────────────────────────────────
              const _SectionTitle(title: '📈 Live Prices', subtitle: 'via CoinGecko'),
              const SizedBox(height: 12),
              markets.when(
                loading: () => const ShimmerList(count: 5),
                error:   (e, _) => _ErrorCard(message: e.toString()),
                data:    (data) => Column(
                  children: data.map((coin) => _CoinCard(coin: coin)).toList(),
                ),
              ),

              const SizedBox(height: 28),

              // ── LSTM Predictions ─────────────────────────────
              const _SectionTitle(
                title: '🤖 LSTM Predictions',
                subtitle: 'H+1 / H+4 forecasts',
              ),
              const SizedBox(height: 12),
              ...predictions.map((p) => _PredictionCard(prediction: p)),
            ],
          ),
        ),
      ),
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
        Text(title,
            style: const TextStyle(
                color: AppColors.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.bold)),
        const Spacer(),
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
        // Icon
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
        // Name + symbol
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
        // Price + change
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
        // Sparkline mini chart
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
    if (price >= 1000) return price.toStringAsFixed(0).replaceAllMapped(
        RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},');
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
      if (i == 0) path.moveTo(x, y);
      else path.lineTo(x, y);
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(_) => false;
}

// ── Prediction Card ──────────────────────────────────────
class _PredictionCard extends StatelessWidget {
  final PredictionModel prediction;
  const _PredictionCard({required this.prediction});

  @override
  Widget build(BuildContext context) {
    final isUp  = prediction.isBullish;
    final color = isUp ? AppColors.success : AppColors.error;
    final icon  = isUp ? Icons.trending_up : Icons.trending_down;

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.cardBackground,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: color.withValues(alpha: 0.3), width: 0.8),
      ),
      child: Column(
        children: [
          Row(
            children: [
              Icon(icon, color: color, size: 20),
              const SizedBox(width: 8),
              Text(prediction.symbol.replaceAll('USDT', ''),
                  style: const TextStyle(
                      color: AppColors.textPrimary,
                      fontWeight: FontWeight.bold,
                      fontSize: 15)),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: AppColors.primary.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(prediction.timeframe,
                    style: const TextStyle(
                        color: AppColors.primary, fontSize: 11)),
              ),
              const Spacer(),
              Text(
                '${(prediction.confidence * 100).toStringAsFixed(0)}% conf.',
                style: TextStyle(color: color, fontSize: 12),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              _PredStat(label: 'Current',
                  value: '\$${prediction.currentPrice.toStringAsFixed(2)}',
                  color: AppColors.textPrimary),
              const Icon(Icons.arrow_forward,
                  color: AppColors.textSecondary, size: 16),
              _PredStat(label: 'Predicted',
                  value: '\$${prediction.predictedPrice.toStringAsFixed(2)}',
                  color: color),
              const Spacer(),
              _PredStat(
                  label: 'Change',
                  value:
                      '${isUp ? '+' : ''}${prediction.changePercent.toStringAsFixed(2)}%',
                  color: color),
            ],
          ),
        ],
      ),
    );
  }
}

class _PredStat extends StatelessWidget {
  final String label, value;
  final Color color;
  const _PredStat({required this.label, required this.value, required this.color});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: const TextStyle(
                color: AppColors.textSecondary, fontSize: 11)),
        Text(value,
            style: TextStyle(
                color: color, fontWeight: FontWeight.w600, fontSize: 13)),
      ],
    );
  }
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
      child: Row(
        children: [
          const Icon(Icons.wifi_off, color: AppColors.error),
          const SizedBox(width: 12),
          const Expanded(
            child: Text('Could not load market data.\nPull down to retry.',
                style: TextStyle(color: AppColors.textSecondary)),
          ),
        ],
      ),
    );
  }
}