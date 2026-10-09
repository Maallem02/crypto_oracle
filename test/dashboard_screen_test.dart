// Draws the dashboard with REAL backend responses (test/fixtures, account
// number replaced) on a phone-size and a desktop-size screen, through every
// period. Any exception or layout overflow fails the test.
import 'dart:convert';
import 'dart:io';

import 'package:crypto_oracle/features/dashboard/providers/market_provider.dart';
import 'package:crypto_oracle/features/dashboard/providers/stats_provider.dart';
import 'package:crypto_oracle/features/dashboard/screens/dashboard_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> _load(String name) =>
    (jsonDecode(File('test/fixtures/$name').readAsStringSync()) as Map)
        .cast<String, dynamic>();

/// The saved calendar is from a Saturday (empty) — add events so the
/// "upcoming", "soon" and "released" rows are drawn too.
Map<String, dynamic> _newsWithEvents() {
  final n = _load('dashboard_news.json');
  final now = DateTime.now().toUtc();
  const all = ['BTC', 'ETH', 'USDJPY', 'XAGUSD', 'XAUUSD'];
  n['upcoming'] = [
    {'title': 'Core PCE Price Index m/m', 'currency': 'USD', 'impact': 'High',
     'time': now.add(const Duration(minutes: 35)).toIso8601String(),
     'forecast': '0.2%', 'previous': '0.3%', 'actual': null, 'symbols': all},
    {'title': 'BOJ Gov Ueda Speaks', 'currency': 'JPY', 'impact': 'Medium',
     'time': now.add(const Duration(hours: 26)).toIso8601String(),
     'forecast': null, 'previous': null, 'actual': null, 'symbols': ['USDJPY']},
  ];
  n['recent'] = [
    {'title': 'Unemployment Claims', 'currency': 'USD', 'impact': 'High',
     'time': now.subtract(const Duration(hours: 3)).toIso8601String(),
     'forecast': '224K', 'previous': '231K', 'actual': '219K', 'symbols': all},
  ];
  return n;
}

Future<void> _pump(WidgetTester tester, Size size, Map<String, dynamic> summary,
    Map<String, dynamic> news) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(ProviderScope(
    overrides: [
      dashboardSummaryProvider.overrideWith((ref) => Stream.value(summary)),
      dashboardNewsProvider.overrideWith((ref) => Stream.value(news)),
      marketsProvider.overrideWith((ref) async => <Map<String, dynamic>>[]),
    ],
    child: const MaterialApp(home: DashboardScreen()),
  ));
  await tester.pump();
  await tester.pump(const Duration(seconds: 1));
}

Future<void> _allPeriods(WidgetTester tester) async {
  for (final p in ['Today', 'This week', 'This month', '30 days', 'All time']) {
    await tester.tap(find.widgetWithText(ChoiceChip, p));
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.textContaining('Performance — $p'), findsOneWidget, reason: p);
  }
}

void main() {
  final summary = _load('dashboard_summary.json');

  for (final size in const [Size(390, 844), Size(1280, 900)]) {
    testWidgets('dashboard draws with real data at ${size.width.toInt()} px', (tester) async {
      await _pump(tester, size, summary, _newsWithEvents());
      expect(find.text('Balance'), findsOneWidget);
      expect(find.textContaining('Risk now'), findsOneWidget);
      expect(find.textContaining('Economic news'), findsOneWidget);
      expect(find.textContaining('Core PCE'), findsOneWidget);
      expect(find.textContaining('in 3'), findsWidgets);            // "in 34/35 min"
      expect(find.textContaining('Market headlines'), findsOneWidget);
      await _allPeriods(tester);
    });
  }

  testWidgets('open trade without a stop is flagged in red', (tester) async {
    await _pump(tester, const Size(390, 844), summary, _load('dashboard_news.json'));
    final noStop = ((summary['risk'] as Map)['no_stop'] as num?) ?? 0;
    expect(find.textContaining('WITHOUT a stop loss'), noStop > 0 ? findsOneWidget : findsNothing);
  });

  testWidgets('empty calendar shows the Sunday note, backend down keeps last data', (tester) async {
    final down = {...summary, 'error': 'connection refused'};
    await _pump(tester, const Size(390, 844), down, _load('dashboard_news.json'));
    expect(find.textContaining('last known figures'), findsOneWidget);
    expect(find.textContaining('left this week'), findsOneWidget);
  });
}
