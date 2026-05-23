import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/smc_repository.dart';

final smcRepositoryProvider = Provider((ref) => SmcRepository());

final selectedSymbolProvider = StateProvider<String>((ref) => 'BTC');
final selectedTimeframeProvider = StateProvider<String>((ref) => '15m');

final smcAnalysisProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  final symbol    = ref.watch(selectedSymbolProvider);
  final timeframe = ref.watch(selectedTimeframeProvider);
  return ref.watch(smcRepositoryProvider).getAnalysis(symbol, timeframe);
});