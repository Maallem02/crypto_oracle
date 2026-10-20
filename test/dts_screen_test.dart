import 'package:crypto_oracle/features/bot/providers/dts_provider.dart';
import 'package:crypto_oracle/features/bot/screens/dts_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

// Onglet Daily Trend SPOT (testnet Binance) avec des données au format de
// /trading/dts/status : sans clés (cas du 13/10) puis connecté avec une position.
Map<String, dynamic> _status({bool keys = true, bool withPosition = false}) => {
  'enabled': keys, 'keys': keys, 'mode': 'TESTNET', 'last_scan_age_min': 2.0,
  'last_error': keys ? null : 'no API keys: add BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET to .env',
  'connection_error': null, 'usdt_free': keys ? 10000.0 : null,
  'equity': 203.5, 'realized_usd': 3.5, 'free_cash': withPosition ? 132.0 : 203.5, 'open_risk_usd': 0,
  'settings': {'symbols': ['BTC', 'ETH'], 'risk_pct': 2.0, 'max_crypto_risk_pct': 3.0, 'capital_usd': 200.0},
  'symbols': {
    'BTC': {'enabled': true, 'price': 83525.0, 'atr': 2250.0, 'entry_level': 87398.19, 'exit_level': 80924.0,
            'stop_pct': 5.39, 'next_order_usd': 70.55, 'next_risk_usd': 4.07},
    'ETH': {'enabled': true, 'price': 2513.0, 'atr': 90.0, 'entry_level': 2806.72, 'exit_level': 2405.0,
            'stop_pct': 7.16, 'next_order_usd': 56.1, 'next_risk_usd': 4.07},
  },
  'positions': withPosition ? [
    {'symbol': 'ETH', 'qty': 0.0284, 'entry': 2510.0, 'stop': 2330.0, 'cost_usd': 71.35, 'value_usd': 71.37,
     'pnl_usd': 0.02, 'since': '2026-10-14T00:01:00+00:00'}] : [],
};

Widget _wrap(Map<String, dynamic> status, {Map<String, dynamic>? testResult}) => ProviderScope(
  overrides: [
    dtsProvider.overrideWith((ref) {
      final n = DtsNotifier(autoRefresh: false)..applyStatus(status);
      if (testResult != null) n.applyTestResult(testResult);
      return n;
    }),
    dtsHistoryProvider.overrideWith((ref) => Stream.value([
      {'timestamp': '2026-10-14T00:01:00+00:00', 'symbol': 'ETH', 'event': 'entry', 'qty': 0.0284, 'price': 2510.0,
       'reason': 'close 2810.00 above the 20-day high 2806.72; risk 2.00%'},
    ])),
  ],
  child: const MaterialApp(home: DtsScreen()),
);

void main() {
  testWidgets('without keys: step-by-step setup, and Start stays disabled', (tester) async {
    await tester.pumpWidget(_wrap(_status(keys: false)));
    await tester.pumpAndSettle();
    expect(find.textContaining('BINANCE_TESTNET_API_KEY=your key'), findsOneWidget);
    expect(find.textContaining('never paste them in a chat'), findsOneWidget);
    final start = tester.widget<ElevatedButton>(find.widgetWithText(ElevatedButton, 'Start spot bot (testnet)'));
    expect(start.onPressed, isNull);
  });

  testWidgets('connected: testnet banner, levels, next buy size, open position with its Binance stop',
      (tester) async {
    await tester.pumpWidget(_wrap(_status(withPosition: true)));
    await tester.pumpAndSettle();
    expect(find.textContaining('orders go to Binance\'s TESTNET'), findsOneWidget);
    expect(find.text('SPOT BOT ACTIVE (testnet)'), findsOneWidget);
    expect(find.textContaining('Buys if a daily close ends above 87398'), findsOneWidget);
    expect(find.textContaining('Next buy ≈ \$70.55 of BTC'), findsOneWidget);
    expect(find.textContaining('Sells if a daily close ends below 2405 (10-day low)'), findsOneWidget);   // ETH is open
    await tester.scrollUntilVisible(find.textContaining('stop 2330 at Binance'), 300, scrollable: find.byType(Scrollable).first);
    expect(find.textContaining('stop 2330 at Binance'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('ETH ENTRY'), 300, scrollable: find.byType(Scrollable).first);
    expect(find.text('ETH ENTRY'), findsOneWidget);
  });

  testWidgets('test order: the button is there, and a passed test shows every step and its cost', (tester) async {
    await tester.pumpWidget(_wrap(_status(), testResult: {
      'ok': true, 'symbol': 'BTC', 'cost_usd': 0.0125,
      'steps': [
        {'step': '1. Market buy', 'ok': true, 'detail': 'bought 8e-05 BTC at 83000.00 for \$6.64'},
        {'step': '2. Stop placed at Binance', 'ok': true, 'detail': 'STOP_LOSS sell 0.00007 at 78850.00 (5% below)'},
        {'step': '3. Stop read back from Binance', 'ok': true, 'detail': 'status NEW'},
        {'step': '4. Stop cancelled', 'ok': true, 'detail': ''},
        {'step': '5. Market sell', 'ok': true, 'detail': 'sold 7e-05 BTC at 83000.00 for \$5.81'},
      ],
    }));
    await tester.pumpAndSettle();
    final btn = tester.widget<ElevatedButton>(find.byKey(const Key('dts_test_order')));
    expect(btn.onPressed, isNotNull);
    expect(find.text('✅ Test order passed'), findsOneWidget);
    expect(find.textContaining('2. Stop placed at Binance — STOP_LOSS sell'), findsOneWidget);
    expect(find.textContaining('4. Stop cancelled'), findsOneWidget);
    expect(find.textContaining('Cost of the round trip: \$0.0125'), findsOneWidget);
  });

  testWidgets('test order: a failure is shown with its clean-up, and the button is off while the bot holds BTC',
      (tester) async {
    final st = _status(withPosition: true);
    (st['positions'] as List).add({'symbol': 'BTC', 'qty': 0.0009, 'entry': 83000.0, 'stop': 78500.0,
      'cost_usd': 74.7, 'value_usd': 74.7, 'pnl_usd': 0.0, 'since': '2026-10-16T00:01:00+00:00'});
    await tester.pumpWidget(_wrap(st, testResult: {
      'ok': false, 'symbol': 'BTC', 'cost_usd': null,
      'steps': [
        {'step': '1. Market buy', 'ok': true, 'detail': 'bought 8e-05 BTC'},
        {'step': 'Failed', 'ok': false, 'detail': 'Binance 400 -2010: Filter failure'},
        {'step': 'Clean-up: coins sold back', 'ok': true, 'detail': 'at 83000.00 for \$6.63'},
      ],
    }));
    await tester.pumpAndSettle();
    expect(tester.widget<ElevatedButton>(find.byKey(const Key('dts_test_order'))).onPressed, isNull);
    expect(find.textContaining('Disabled while the bot holds BTC'), findsOneWidget);
    expect(find.text('❌ Test order failed'), findsOneWidget);
    expect(find.textContaining('Clean-up: coins sold back'), findsOneWidget);
  });
}
