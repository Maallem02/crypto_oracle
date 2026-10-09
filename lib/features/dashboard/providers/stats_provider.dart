import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/dio_client.dart';

// ── Tableau de bord (2026-09-26) ─────────────────────────────────────────────
// Données calculées par le backend depuis l'historique MT5 du compte connecté
// (GET /dashboard/summary et /dashboard/news). Lecture seule.

/// Compte, performance par période / bot / symbole, courbe du solde, risque.
/// Relu toutes les 30 s tant que le tableau de bord est affiché.
final dashboardSummaryProvider =
    StreamProvider.autoDispose<Map<String, dynamic>>((ref) async* {
  final dio = DioClient.create();
  Map<String, dynamic>? last;
  while (true) {
    try {
      final r = await dio.get('/dashboard/summary');
      last = (r.data as Map).cast<String, dynamic>();
      yield last;
    } catch (e) {
      // on garde les derniers chiffres affichés plutôt qu'un écran d'erreur
      yield {...?last, 'error': e.toString()};
    }
    await Future.delayed(const Duration(seconds: 30));
  }
});

/// Calendrier économique + titres. Le backend met en cache (30 min / 15 min),
/// relu toutes les 5 minutes.
final dashboardNewsProvider =
    StreamProvider.autoDispose<Map<String, dynamic>>((ref) async* {
  final dio = DioClient.create();
  Map<String, dynamic>? last;
  while (true) {
    try {
      final r = await dio.get('/dashboard/news');
      last = (r.data as Map).cast<String, dynamic>();
      yield last;
    } catch (e) {
      yield {...?last, 'error': e.toString()};
    }
    await Future.delayed(const Duration(minutes: 5));
  }
});
