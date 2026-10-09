/// Rafraîchissement automatique de l'app (demande du 2026-09-25) : un seul
/// minuteur dans MainShell (app.dart) relit l'état des bots Scalping et
/// Volume Profile, le solde du compte et — si le tableau de bord est affiché —
/// les prix. Les positions ouvertes et les historiques ont déjà leur propre
/// sondage (10-15 s).
const autoRefreshInterval = Duration(seconds: 30);
