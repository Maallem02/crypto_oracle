import 'package:crypto_oracle/features/bot/providers/dt_provider.dart';
import 'package:crypto_oracle/features/bot/screens/dt_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

// Données au format de /trading/dt/audit : le cas réel du 11/10/2026 (aucun
// trade, 3 clôtures contrôlées) et des cas inventés pour les alertes.
final _none = {
  'verdict': 'no_trades', 'since': '2026-10-08T00:00:00+00:00', 'checked_closes': 3,
  'summary': {'live_trades': 0, 'skipped_by_design': 0, 'warnings': 0, 'problems': 0},
  'range': {'n': 0, 'status': 'no_trades'}, 'events': [], 'trades': [],
};

final _warn = {
  'verdict': 'problems', 'since': '2026-10-08T00:00:00+00:00', 'checked_closes': 40,
  'summary': {'live_trades': 2, 'skipped_by_design': 1, 'warnings': 1, 'problems': 1,
              'avg_gap_r': -0.04, 'avg_entry_slip_r': -0.02},
  'range': {'n': 1, 'sum_r': -1.02, 'expected_p5': -1.0, 'expected_p95': 9.8, 'max_dd_r': -1.02,
            'dd_p5': -1.0, 'losing_streak': 1, 'streak_p95': 1, 'status': 'below'},
  'events': [
    {'time': '2026-11-03T00:00:00+00:00', 'symbol': 'ETH', 'status': 'problem',
     'text': '02/11/2026 breakout not bought and no journal record: the backend was probably not running'},
    {'time': '2026-10-20T00:01:00+00:00', 'symbol': 'BTC', 'status': 'info',
     'text': '19/10/2026 breakout not bought: crypto risk cap full'},
  ],
  'trades': [
    {'symbol': 'BTC', 'signal_day': '14/10/2026', 'status': 'closed', 'r': -1.02, 'test_r': -1.0,
     'late_min': 185.0, 'entry_slip_r': -0.02, 'stop_dev_pct': 0.0, 'risk_pct': 1.98, 'check': 'warn',
     'notes': ['entered 3.1 h after the daily close (the test buys at the open)']},
  ],
};

Widget _wrap(Map<String, dynamic>? data) => ProviderScope(
  overrides: [dtAuditProvider.overrideWith((ref) => Stream.value(data))],
  child: const MaterialApp(home: Scaffold(body: SingleChildScrollView(child: DtAuditCard()))),
);

void main() {
  testWidgets('no trade yet: says how many closes were checked and that nothing was missed', (tester) async {
    await tester.pumpWidget(_wrap(_none));
    await tester.pumpAndSettle();
    expect(find.textContaining('No trade yet — 3 daily closes checked'), findsOneWidget);
    expect(find.textContaining('nothing missed'), findsOneWidget);
  });

  testWidgets('problems and warnings are listed with their reasons, and a result below range is flagged',
      (tester) async {
    await tester.pumpWidget(_wrap(_warn));
    await tester.pumpAndSettle();
    expect(find.text('1 problem — see below.'), findsOneWidget);
    expect(find.textContaining('no journal record'), findsOneWidget);
    expect(find.textContaining('crypto risk cap full'), findsOneWidget);
    expect(find.textContaining('3.1 h after the daily close'), findsOneWidget);
    expect(find.textContaining('result -1.02R vs test -1.00R'), findsOneWidget);
    expect(find.textContaining('Below what the test expects'), findsOneWidget);
  });

  testWidgets('backend unreachable is said plainly', (tester) async {
    await tester.pumpWidget(_wrap(null));
    await tester.pumpAndSettle();
    expect(find.textContaining('backend unreachable'), findsOneWidget);
  });
}
