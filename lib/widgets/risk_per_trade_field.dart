import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../core/constants/app_colors.dart';

// ── Risque par trade (2026-10-10) ─────────────────────────────────────────────
// Réglage commun aux onglets Scalping et Vol. Profile : curseur 0.25-5 % ou
// valeur tapée (0.1-5 %). Le backend calcule le lot depuis la distance au stop ;
// si même le lot minimum risque plus, le trade est sauté, jamais forcé.

String riskPctText(double v) => '${v.toStringAsFixed(v == v.roundToDouble() ? 0 : 2)}%';

class RiskPerTradeField extends StatefulWidget {
  final double value;
  final bool enabled;
  /// Applique une valeur (bornée par le notifier) et retourne celle retenue.
  final double Function(double) onChanged;
  final String? note;
  final Key? inputKey;
  const RiskPerTradeField({super.key, required this.value, required this.onChanged,
    this.enabled = true, this.note, this.inputKey});

  @override
  State<RiskPerTradeField> createState() => _RiskPerTradeFieldState();
}

class _RiskPerTradeFieldState extends State<RiskPerTradeField> {
  final _text = TextEditingController();
  final _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _text.text = widget.value.toString();
    _focus.addListener(() { if (!_focus.hasFocus) _apply(); });
  }

  @override
  void didUpdateWidget(covariant RiskPerTradeField old) {
    super.didUpdateWidget(old);
    if (!_focus.hasFocus) _text.text = widget.value.toString();
  }

  @override
  void dispose() { _text.dispose(); _focus.dispose(); super.dispose(); }

  void _apply() {
    final v = double.tryParse(_text.text.replaceAll(',', '.'));
    _text.text = (v == null ? widget.value : widget.onChanged(v)).toString();
  }

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
    Row(children: [
      const Text('Risk per trade', style: TextStyle(color: AppColors.textSecondary, fontSize: 13)),
      const Spacer(),
      Text('${riskPctText(widget.value)} of the balance',
        style: const TextStyle(color: AppColors.warning, fontWeight: FontWeight.bold, fontSize: 13)),
    ]),
    Row(children: [
      Expanded(child: Slider(
        value: widget.value.clamp(0.25, 5.0).toDouble(), min: 0.25, max: 5.0, divisions: 19,
        activeColor: AppColors.warning, inactiveColor: AppColors.surface,
        label: riskPctText(widget.value),
        onChanged: widget.enabled ? (v) { widget.onChanged(v); } : null,
      )),
      SizedBox(width: 72, child: TextField(
        key: widget.inputKey,
        controller: _text,
        focusNode: _focus,
        enabled: widget.enabled,
        keyboardType: const TextInputType.numberWithOptions(decimal: true),
        inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9.,]'))],
        onSubmitted: (_) => _apply(),
        style: const TextStyle(color: AppColors.textPrimary, fontSize: 13),
        decoration: const InputDecoration(suffixText: '%', isDense: true, border: OutlineInputBorder()),
      )),
    ]),
    Text(widget.note ??
        'Each trade is sized so that hitting its stop loses this share of the balance (0.1% to 5%, '
        'type any value). If even the smallest lot would risk more, the trade is skipped, never forced. '
        'The video\'s guideline: 0.25% to 2%.',
      style: const TextStyle(color: AppColors.textHint, fontSize: 10)),
  ]);
}
