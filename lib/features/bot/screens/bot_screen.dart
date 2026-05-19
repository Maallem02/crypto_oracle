import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/constants/app_colors.dart';
import '../providers/bot_provider.dart';

class BotScreen extends ConsumerWidget {
  const BotScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final botState = ref.watch(botProvider);

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.background,
        elevation: 0,
        title: const Text('🤖 Trading Bot',
          style: TextStyle(color: AppColors.primary, fontWeight: FontWeight.bold)),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            // Bot Status Card
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(24),
              decoration: BoxDecoration(
                color: AppColors.cardBackground,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(
                  color: botState.isRunning
                    ? AppColors.success.withValues(alpha: 0.4)
                    : AppColors.error.withValues(alpha: 0.4)),
              ),
              child: Column(children: [
                Icon(
                  botState.isRunning ? Icons.smart_toy : Icons.smart_toy_outlined,
                  color: botState.isRunning ? AppColors.success : AppColors.textSecondary,
                  size: 48,
                ),
                const SizedBox(height: 12),
                Text(
                  botState.isRunning ? 'BOT ACTIVE' : 'BOT STOPPED',
                  style: TextStyle(
                    color: botState.isRunning ? AppColors.success : AppColors.textSecondary,
                    fontSize: 20, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 16),
                Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                  ElevatedButton.icon(
                    onPressed: botState.isRunning
                      ? () => ref.read(botProvider.notifier).stop()
                      : () => ref.read(botProvider.notifier).start(),
                    icon: Icon(botState.isRunning ? Icons.stop : Icons.play_arrow),
                    label: Text(botState.isRunning ? 'Stop Bot' : 'Start Bot'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: botState.isRunning ? AppColors.error : AppColors.success,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 12),
                    ),
                  ),
                ]),
              ]),
            ),
            const SizedBox(height: 16),

            // Account Info
            if (botState.account != null) ...[
              _InfoCard(data: {
                '💰 Balance':  '\$${botState.account!['balance']?.toStringAsFixed(2)}',
                '📊 Equity':   '\$${botState.account!['equity']?.toStringAsFixed(2)}',
                '🔧 Leverage': '1:${botState.account!['leverage']}',
                '💱 Currency': botState.account!['currency'],
              }),
              const SizedBox(height: 16),
            ],

            // Settings
            _SettingsCard(ref: ref, botState: botState),
            const SizedBox(height: 16),

            // Scan Button
            SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                onPressed: botState.isRunning
                  ? () => ref.read(botProvider.notifier).scan()
                  : null,
                icon: const Icon(Icons.radar),
                label: const Text('Scan & Trade Now'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: AppColors.primary,
                  foregroundColor: AppColors.background,
                  padding: const EdgeInsets.symmetric(vertical: 14),
                ),
              ),
            ),
            const SizedBox(height: 16),

            // Open Trades
            _OpenTradesCard(ref: ref),
          ],
        ),
      ),
    );
  }
}

class _InfoCard extends StatelessWidget {
  final Map<String, String?> data;
  const _InfoCard({required this.data});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.cardBackground,
        borderRadius: BorderRadius.circular(12)),
      child: Column(
        children: data.entries.map((e) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Row(children: [
            Text(e.key, style: const TextStyle(color: AppColors.textSecondary, fontSize: 13)),
            const Spacer(),
            Text(e.value ?? '-', style: const TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.w600, fontSize: 13)),
          ]),
        )).toList(),
      ),
    );
  }
}

class _SettingsCard extends StatelessWidget {
  final WidgetRef ref;
  final BotState botState;
  const _SettingsCard({required this.ref, required this.botState});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.cardBackground,
        borderRadius: BorderRadius.circular(12)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('⚙️ Settings', style: TextStyle(
            color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15)),
          const SizedBox(height: 12),
          Row(children: [
            const Text('Risk %', style: TextStyle(color: AppColors.textSecondary)),
            const Spacer(),
            Text('${botState.riskPercent.toStringAsFixed(1)}%',
              style: const TextStyle(color: AppColors.primary, fontWeight: FontWeight.bold)),
          ]),
          Slider(
            value: botState.riskPercent,
            min: 0.5, max: 3.0, divisions: 5,
            activeColor: AppColors.primary,
            onChanged: (v) => ref.read(botProvider.notifier).setRisk(v),
          ),
          Row(children: [
            const Text('Min Confidence', style: TextStyle(color: AppColors.textSecondary)),
            const Spacer(),
            Text('${(botState.minConfidence * 100).toStringAsFixed(0)}%',
              style: const TextStyle(color: AppColors.warning, fontWeight: FontWeight.bold)),
          ]),
          Slider(
            value: botState.minConfidence,
            min: 0.6, max: 0.95, divisions: 7,
            activeColor: AppColors.warning,
            onChanged: (v) => ref.read(botProvider.notifier).setMinConfidence(v),
          ),
        ],
      ),
    );
  }
}

class _OpenTradesCard extends ConsumerWidget {
  const _OpenTradesCard({required WidgetRef ref});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final trades = ref.watch(openTradesProvider);
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.cardBackground,
        borderRadius: BorderRadius.circular(12)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('📋 Open Trades', style: TextStyle(
            color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 15)),
          const SizedBox(height: 8),
          trades.when(
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (e, _) => Text('Error: $e', style: const TextStyle(color: AppColors.error)),
            data: (list) => list.isEmpty
              ? const Text('No open trades', style: TextStyle(color: AppColors.textSecondary))
              : Column(children: list.map((t) {
                  final isProfit = (t['profit'] as num) >= 0;
                  return Padding(
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: Row(children: [
                      Text(t['symbol'], style: const TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.bold)),
                      const SizedBox(width: 8),
                      Text(t['type'].toString().toUpperCase(),
                        style: TextStyle(
                          color: t['type'] == 'buy' ? AppColors.success : AppColors.error,
                          fontSize: 12)),
                      const Spacer(),
                      Text('${isProfit ? '+' : ''}\$${(t['profit'] as num).toStringAsFixed(2)}',
                        style: TextStyle(
                          color: isProfit ? AppColors.success : AppColors.error,
                          fontWeight: FontWeight.bold)),
                    ]),
                  );
                }).toList()),
          ),
        ],
      ),
    );
  }
}