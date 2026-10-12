import 'package:crypto_oracle/widgets/expectancy_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

// Données au format de /dashboard/expectancy (valeurs réelles du 10/10/2026
// pour le Scalping, données inventées pour les autres cas).
final _data = {
  'bots': {
    'smc': {
      'bot': 'smc', 'trades': 1366, 'trades_with_r': 1239, 'r_from_journal': 115, 'sample_target': 100,
      'expectancy_r': -0.096, 'range_low_r': -0.145, 'range_high_r': -0.047, 'expectancy_usd': -0.36,
      'win_rate': 40.0, 'breakeven_win_rate': 45.4, 'avg_win_r': 0.98, 'avg_loss_r': 0.82,
      'total_usd': -487.83, 'spread_est_usd': 492.0, 'commission_swap_usd': 0.0,
      'risk_pct_median': 7.7, 'risk_pct_p90': 15.2, 'verdict': 'losing',
    },
    'vp': {
      'bot': 'vp', 'trades': 47, 'trades_with_r': 47, 'sample_target': 100,
      'expectancy_r': -0.225, 'range_low_r': -0.517, 'range_high_r': 0.099, 'expectancy_usd': -1.42,
      'win_rate': 27.7, 'breakeven_win_rate': 35.4, 'avg_win_r': 1.87, 'avg_loss_r': 1.03,
      'total_usd': -66.65, 'spread_est_usd': 24.16, 'commission_swap_usd': 0.0,
      'risk_pct_median': 5.5, 'risk_pct_p90': 12.0, 'verdict': 'not_proven',
    },
    'dt': {'bot': 'dt', 'trades': 0, 'trades_with_r': 0, 'sample_target': 100, 'verdict': 'no_trades',
           'tested': 'Exness daily 2018-2026: +1.66R per trade over 91 trades.'},
  },
};

Widget _wrap(String bot) => ProviderScope(
  overrides: [expectancyProvider.overrideWith((ref) => Stream.value(_data))],
  child: MaterialApp(home: Scaffold(body: SingleChildScrollView(child: ExpectancyCard(bot: bot)))),
);

void main() {
  testWidgets('a losing bot over a large sample says so, with the numbers', (tester) async {
    await tester.pumpWidget(_wrap('smc'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Losing system'), findsOneWidget);
    expect(find.text('-0.10R'), findsOneWidget);
    expect(find.textContaining('needs 45% to break even'), findsOneWidget);
    expect(find.textContaining('large enough sample'), findsOneWidget);
    expect(find.textContaining('guideline 0.25–2%'), findsOneWidget);
    expect(find.textContaining('1239 of 1366 trades (115 limit-order stops from the bot'), findsOneWidget);
  });

  testWidgets('a small sample is "not proven", with its likely range', (tester) async {
    await tester.pumpWidget(_wrap('vp'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Not proven yet'), findsOneWidget);
    expect(find.textContaining('Likely between -0.52R and +0.10R'), findsOneWidget);
    expect(find.textContaining('47 of 100'), findsOneWidget);
  });

  testWidgets('a bot without trades shows its tested reference', (tester) async {
    await tester.pumpWidget(_wrap('dt'));
    await tester.pumpAndSettle();
    expect(find.text('No trades yet'), findsOneWidget);
    expect(find.textContaining('+1.66R per trade'), findsOneWidget);
  });
}
