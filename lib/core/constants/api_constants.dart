class ApiConstants {
  ApiConstants._();

  // ─── Your FastAPI Backend ───────────────────────────────────────────────
  static const String baseUrl = 'http://10.0.2.2:8000';

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