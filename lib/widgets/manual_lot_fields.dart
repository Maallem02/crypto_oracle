import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../core/constants/app_colors.dart';
import '../features/bot/providers/vp_provider.dart' show LotRule;

// ── Lot manuel par symbole (2026-10-12) ───────────────────────────────────────
// Demande utilisateur : « le bot choisit le lot si je n'en choisis pas, mais
// respecte le lot que je donne ». Case vide = Auto (le lot vient du risque par
// trade) ; un lot tapé est utilisé TEL QUEL pour ce symbole. Commun aux onglets
// Scalping et Vol. Profile. Un lot refusé par le broker (minimum, maximum, pas)
// est signalé ici et n'est pas appliqué.

String lotText(double v) {
  var s = v.toStringAsFixed(4);
  while (s.contains('.') && (s.endsWith('0') || s.endsWith('.'))) {
    s = s.substring(0, s.length - 1);
  }
  return s;
}

/// null = lot accepté ; sinon la raison (mêmes règles que le backend).
String? manualLotError(double lot, LotRule? rule) {
  if (lot <= 0) return 'the lot must be above 0';
  if (rule == null) return null;                     // limites inconnues : le backend vérifiera
  if (lot < rule.minLot - 1e-9) return 'below the broker minimum of ${lotText(rule.minLot)}';
  if (lot > rule.maxLot + 1e-9) return 'above the broker maximum of ${lotText(rule.maxLot)}';
  final steps = lot / rule.lotStep;
  if ((steps - steps.round()).abs() > 1e-6) return 'not a multiple of the broker step ${lotText(rule.lotStep)}';
  return null;
}

class ManualLotFields extends StatefulWidget {
  final List<String> symbols;
  final Map<String, double> lots;                    // symbole -> lot manuel (absent = Auto)
  final Map<String, LotRule> rules;                  // limites du broker, si connues
  final void Function(String symbol, double? lot) onChanged;   // null = retour en Auto
  final bool enabled;
  final String safetyNote;
  const ManualLotFields({super.key, required this.symbols, required this.lots, required this.rules,
    required this.onChanged, required this.safetyNote, this.enabled = true});

  @override
  State<ManualLotFields> createState() => _ManualLotFieldsState();
}

class _ManualLotFieldsState extends State<ManualLotFields> {
  final _text = <String, TextEditingController>{};
  final _focus = <String, FocusNode>{};
  final _errors = <String, String>{};

  TextEditingController _ctrl(String sym) => _text.putIfAbsent(sym, () {
    final c = TextEditingController(text: _shown(sym));
    final f = FocusNode();
    f.addListener(() { if (!f.hasFocus) _apply(sym); });
    _focus[sym] = f;
    return c;
  });

  String _shown(String sym) => widget.lots[sym] == null ? '' : lotText(widget.lots[sym]!);

  @override
  void didUpdateWidget(covariant ManualLotFields old) {
    super.didUpdateWidget(old);
    _text.forEach((sym, c) {
      if (!(_focus[sym]?.hasFocus ?? false) && !_errors.containsKey(sym)) c.text = _shown(sym);
    });
  }

  @override
  void dispose() {
    for (final c in _text.values) { c.dispose(); }
    for (final f in _focus.values) { f.dispose(); }
    super.dispose();
  }

  void _apply(String sym) {
    final raw = _text[sym]!.text.trim().replaceAll(',', '.');
    if (raw.isEmpty) {
      setState(() => _errors.remove(sym));
      widget.onChanged(sym, null);                   // case vidée : Auto
      return;
    }
    final v = double.tryParse(raw);
    final err = v == null ? 'not a number' : manualLotError(v, widget.rules[sym]);
    if (err != null) {
      setState(() => _errors[sym] = err);            // pas appliqué : l'ancien réglage reste
      return;
    }
    setState(() => _errors.remove(sym));
    widget.onChanged(sym, v);
  }

  void _clear(String sym) {
    _text[sym]?.text = '';
    setState(() => _errors.remove(sym));
    widget.onChanged(sym, null);
  }

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
    const SizedBox(height: 8),
    const Text('Lot per symbol (optional)', style: TextStyle(color: AppColors.textSecondary, fontSize: 13)),
    const SizedBox(height: 4),
    for (final sym in widget.symbols) _row(sym),
    Text('Empty = Auto: the bot picks the lot from your risk per trade. Type a lot to use exactly that '
        'lot on this symbol; its risk then changes with each stop distance. ${widget.safetyNote}',
      style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
  ]);

  Widget _row(String sym) {
    final manual = widget.lots[sym];
    final rule = widget.rules[sym];
    final err = _errors[sym];
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          SizedBox(width: 72, child: Text(sym, style: const TextStyle(
            color: AppColors.textPrimary, fontWeight: FontWeight.w600, fontSize: 12))),
          SizedBox(width: 96, child: TextField(
            key: Key('manual_lot_$sym'),
            controller: _ctrl(sym),
            focusNode: _focus[sym],
            enabled: widget.enabled,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9.,]'))],
            onSubmitted: (_) => _apply(sym),
            style: const TextStyle(color: AppColors.textPrimary, fontSize: 13),
            decoration: const InputDecoration(hintText: 'Auto', suffixText: 'lot', isDense: true,
              border: OutlineInputBorder()),
          )),
          const SizedBox(width: 8),
          Expanded(child: Text(
            manual == null ? 'auto (risk %)' : 'fixed ${lotText(manual)} lot',
            style: TextStyle(color: manual == null ? AppColors.textHint : AppColors.warning, fontSize: 11))),
          if (manual != null)
            IconButton(
              tooltip: 'Back to Auto',
              visualDensity: VisualDensity.compact,
              icon: const Icon(Icons.close, size: 16, color: AppColors.textSecondary),
              onPressed: widget.enabled ? () => _clear(sym) : null,
            ),
        ]),
        if (err != null)
          Text('$sym: $err — not applied', style: const TextStyle(color: AppColors.error, fontSize: 10))
        else if (rule != null)
          Text('broker: min ${lotText(rule.minLot)}, step ${lotText(rule.lotStep)}',
            style: const TextStyle(color: AppColors.textHint, fontSize: 9)),
      ]),
    );
  }
}
