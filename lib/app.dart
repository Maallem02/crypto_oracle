import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'core/config/refresh_config.dart';
import 'core/constants/app_colors.dart';
import 'features/auth/providers/auth_provider.dart';
import 'features/bot/providers/bot_provider.dart';
import 'features/bot/providers/scalp_provider.dart';
import 'features/bot/providers/vp_provider.dart';
import 'features/bot/providers/dt_provider.dart';
import 'features/bot/providers/dts_provider.dart';
import 'features/dashboard/providers/market_provider.dart';
import 'features/auth/screens/login_screen.dart';
import 'features/auth/screens/register_screen.dart';
import 'features/dashboard/screens/dashboard_screen.dart';
import 'features/dashboard/screens/smc_screen.dart';
import 'features/bot/screens/bot_screen.dart';

final routerProvider = Provider<GoRouter>((ref) {
  final authState = ref.watch(authProvider);
  return GoRouter(
    initialLocation: '/login',
    redirect: (context, state) {
      final isAuth    = authState.isAuthenticated;
      final isLoading = authState.isLoading;
      final onAuth    = state.matchedLocation == '/login' ||
                        state.matchedLocation == '/register';
      if (isLoading) return null;
      if (isAuth && onAuth) return '/dashboard';
      if (!isAuth && !onAuth) return '/login';
      return null;
    },
    routes: [
      GoRoute(path: '/login',    builder: (_, __) => const LoginScreen()),
      GoRoute(path: '/register', builder: (_, __) => const RegisterScreen()),
      GoRoute(path: '/dashboard', builder: (_, __) => const MainShell()),
    ],
  );
});

class CryptoOracleApp extends ConsumerWidget {
  const CryptoOracleApp({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(routerProvider);
    return MaterialApp.router(
      title: 'CryptoOracle',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark().copyWith(scaffoldBackgroundColor: AppColors.background),
      routerConfig: router,
    );
  }
}

class MainShell extends ConsumerStatefulWidget {
  const MainShell({super.key});
  @override
  ConsumerState<MainShell> createState() => _MainShellState();
}

class _MainShellState extends ConsumerState<MainShell> {
  int _index = 0;
  final _screens = const [DashboardScreen(), SmcScreen(), BotScreen()];
  Timer? _autoRefresh;

  @override
  void initState() {
    super.initState();
    _autoRefresh = Timer.periodic(autoRefreshInterval, (_) => _refreshAll());
  }

  @override
  void dispose() {
    _autoRefresh?.cancel();
    super.dispose();
  }

  // Lecture seule : rien ici ne démarre, n'arrête ni ne modifie un bot. Les
  // saisies de réglages non enregistrées sont conservées par les providers.
  void _refreshAll() {
    if (!mounted) return;
    ref.read(scalpProvider.notifier).refreshStatus();
    ref.read(vpProvider.notifier).refresh();
    ref.read(dtProvider.notifier).refresh();
    ref.read(dtsProvider.notifier).refresh();
    ref.read(botProvider.notifier).refreshAccount();
    if (_index == 0) ref.invalidate(marketsProvider);   // prix CoinGecko : écran visible seulement
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: _screens[_index],
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _index,
        onTap: (i) => setState(() => _index = i),
        backgroundColor: AppColors.cardBackground,
        selectedItemColor: AppColors.primary,
        unselectedItemColor: AppColors.textSecondary,
        items: const [
          BottomNavigationBarItem(icon: Icon(Icons.dashboard), label: 'Dashboard'),
          BottomNavigationBarItem(icon: Icon(Icons.analytics), label: 'SMC'),
          BottomNavigationBarItem(icon: Icon(Icons.smart_toy), label: 'Bot'),
        ],
      ),
    );
  }
}