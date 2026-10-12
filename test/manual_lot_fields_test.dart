import 'package:crypto_oracle/features/bot/providers/vp_provider.dart' show LotRule;
import 'package:crypto_oracle/widgets/manual_lot_fields.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

// Lot manuel par symbole : case vide = Auto, lot tapé = ce lot exactement.
// Limites du broker au format de lot_rules / available_symbols (Exness démo).
const _rules = {'BTC': LotRule(0.01, 0.01, 200), 'ETH': LotRule(0.1, 0.01, 2000)};

void main() {
  test('broker limits: same rules as the backend', () {
    expect(manualLotError(0.05, _rules['BTC']), isNull);
    expect(manualLotError(0.005, _rules['BTC']), contains('below the broker minimum of 0.01'));
    expect(manualLotError(0.015, _rules['BTC']), contains('not a multiple of the broker step 0.01'));
    expect(manualLotError(300, _rules['BTC']), contains('above the broker maximum of 200'));
    expect(manualLotError(0.05, _rules['ETH']), contains('below the broker minimum of 0.1'));
    expect(manualLotError(0.25, null), isNull);       // limites inconnues : le backend tranche
  });

  Future<Map<String, double?>> pump(WidgetTester tester, Map<String, double> lots, List<String> calls) async {
    final changes = <String, double?>{};
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(child: ManualLotFields(
      symbols: const ['BTC', 'ETH'], lots: lots, rules: _rules, safetyNote: 'Safety kept.',
      onChanged: (s, v) { changes[s] = v; calls.add('$s=$v'); },
    )))));
    return changes;
  }

  testWidgets('empty boxes show Auto; a typed valid lot is applied as is', (tester) async {
    final calls = <String>[];
    final changes = await pump(tester, const {}, calls);
    expect(find.text('auto (risk %)'), findsNWidgets(2));
    await tester.enterText(find.byKey(const Key('manual_lot_BTC')), '0.05');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump();
    expect(changes['BTC'], 0.05);
  });

  testWidgets('a lot the broker would refuse is NOT applied and the reason is shown', (tester) async {
    final calls = <String>[];
    await pump(tester, const {}, calls);
    await tester.enterText(find.byKey(const Key('manual_lot_ETH')), '0.05');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump();
    expect(calls, isEmpty);
    expect(find.textContaining('ETH: below the broker minimum of 0.1 — not applied'), findsOneWidget);
  });

  testWidgets('a fixed lot is labelled, and the cross / an emptied box goes back to Auto', (tester) async {
    final calls = <String>[];
    final changes = await pump(tester, const {'BTC': 0.02}, calls);
    expect(find.text('fixed 0.02 lot'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.close));
    await tester.pump();
    expect(changes.containsKey('BTC') && changes['BTC'] == null, isTrue);
    await tester.enterText(find.byKey(const Key('manual_lot_ETH')), '');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump();
    expect(changes.containsKey('ETH') && changes['ETH'] == null, isTrue);
  });
}
