class PredictionModel {
  final String   symbol;
  final String   assetType;      // 'crypto' or 'forex'
  final String   timeframe;      // '5m', '15m', '30m', '1h', '4h'
  final double   currentPrice;
  final double   predictedPrice; // predicted price at end of timeframe
  final double   changePercent;  // % change predicted
  final String   direction;      // 'up' or 'down'
  final double   confidence;     // 0.0 - 1.0
  final DateTime generatedAt;

  const PredictionModel({
    required this.symbol,
    required this.assetType,
    required this.timeframe,
    required this.currentPrice,
    required this.predictedPrice,
    required this.changePercent,
    required this.direction,
    required this.confidence,
    required this.generatedAt,
  });

  factory PredictionModel.fromJson(Map<String, dynamic> json) {
    return PredictionModel(
      symbol:         json['symbol']          as String,
      assetType:      json['asset_type']      as String,
      timeframe:      json['timeframe']       as String,
      currentPrice:   (json['current_price']  as num).toDouble(),
      predictedPrice: (json['predicted_price'] as num).toDouble(),
      changePercent:  (json['change_percent'] as num).toDouble(),
      direction:      json['direction']       as String,
      confidence:     (json['confidence']     as num).toDouble(),
      generatedAt:    DateTime.parse(json['generated_at'] as String),
    );
  }

  Map<String, dynamic> toJson() => {
    'symbol':          symbol,
    'asset_type':      assetType,
    'timeframe':       timeframe,
    'current_price':   currentPrice,
    'predicted_price': predictedPrice,
    'change_percent':  changePercent,
    'direction':       direction,
    'confidence':      confidence,
    'generated_at':    generatedAt.toIso8601String(),
  };

  bool get isBullish => direction == 'up';
  bool get isForex   => assetType == 'forex';
}