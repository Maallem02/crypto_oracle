class ApiConstants {
  ApiConstants._();

  // ───  FastAPI Backend ───────────────────────────────────────────────
  // DEFAULT ONLY. The live value is ApiConfig.baseUrl, which is loaded from
  // device storage at startup and editable from the login screen — so a
  // changed address no longer needs a code edit and a rebuild.
  //
  // Default is this PC's TAILSCALE address — permanent, works from anywhere
  // (home wifi, mobile data), and private: the tailnet is not the public
  // internet, which matters because /trading/* has no authentication.
  //
  // Replaces the Cloudflare quick tunnel, whose URL was regenerated on every
  // restart (the recurring DioException) and which published the trading API
  // openly.
  //
  // Tailscale device: DESKTOP-49MO79I
  //   100.89.32.70                          <- used here, no DNS dependency
  //   desktop-49mo79i                       (MagicDNS short name)
  //   desktop-49mo79i.tailfced0b.ts.net     (MagicDNS FQDN)
  // The IP is fixed for the lifetime of the device in the tailnet. All three
  // were verified reachable. Fall back to http://192.168.0.129:8000 for plain
  // home-wifi use with Tailscale off.
  static const String baseUrl   = 'http://100.89.32.70:8000';

  // ─── Multi-account support ───────────────────────────────────────────────
  // Account 1 tracks ApiConfig.baseUrl (see accountsResolved in ApiConfig
  // consumers). Extra accounts are still declared here.
  static const List<Map<String, String>> accounts = [
    {'name': 'Compte 1', 'url': baseUrl},
    // Compte 2 disabled — re-enable with a second backend when needed
  ];

  // Auth
  static const String register = '/auth/register';
  static const String login    = '/auth/login';
  static const String me       = '/auth/me';

  // Signals (SMC + LSTM combined)
  static const String signal   = '/signals';      // /signals/BTCUSDT?timeframe=15m

  // Prediction (LSTM only)
  static const String predict  = '/predict';      // /predict/BTCUSDT?timeframe=1h

  // Candles
  static const String candles  = '/candles';      // /candles/BTCUSDT?timeframe=15m

  // ─── CoinGecko (live crypto prices for dashboard) ───────────────────────
  static const String coinGeckoBase = 'https://api.coingecko.com/api/v3';
  static const String markets       = '/coins/markets';

  // ─── Twelve Data (Forex + Metals live prices) ───────────────────────────
  static const String twelveDataBase  = 'https://api.twelvedata.com';
  static const String twelveDataPrice = '/price';
  static const String twelveDataKey   = 'YOUR_TWELVE_DATA_KEY'; // free at twelvedata.com

  // ─── Supported Assets ───────────────────────────────────────────────────
  static const List<Map<String, String>> cryptoAssets = [
    {'symbol': 'BTCUSDT',  'name': 'Bitcoin',  'display': 'BTC'},
    {'symbol': 'ETHUSDT',  'name': 'Ethereum', 'display': 'ETH'},
    {'symbol': 'BNBUSDT',  'name': 'BNB',      'display': 'BNB'},
    {'symbol': 'SOLUSDT',  'name': 'Solana',   'display': 'SOL'},
    {'symbol': 'XRPUSDT',  'name': 'XRP',      'display': 'XRP'},
  ];

  static const List<Map<String, String>> forexAssets = [
    {'symbol': 'XAUUSD', 'name': 'Gold',          'display': 'XAU/USD'},
    {'symbol': 'XAGUSD', 'name': 'Silver',         'display': 'XAG/USD'},
    {'symbol': 'GBPJPY', 'name': 'Pound Yen',      'display': 'GBP/JPY'},
  ];

  // ─── Timeframes ──────────────────────────────────────────────────────────
  static const List<String> timeframes = ['5m', '15m', '30m', '1h', '4h'];
}