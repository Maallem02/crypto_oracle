// Represents one SMC Order Block zone
class OrderBlock {
  final double top;
  final double bottom;
  final String type; // 'bullish' or 'bearish'

  const OrderBlock({
    required this.top,
    required this.bottom,
    required this.type,
  });

  factory OrderBlock.fromJson(Map<String, dynamic> json) => OrderBlock(
    top:    (json['top']    as num).toDouble(),
    bottom: (json['bottom'] as num).toDouble(),
    type:   json['type']    as String,
  );

  bool get isBullish => type == 'bullish';
}

// Represents one Fair Value Gap zone
class FairValueGap {
  final double top;
  final double bottom;
  final String type; // 'bullish' or 'bearish'

  const FairValueGap({
    required this.top,
    required this.bottom,
    required this.type,
  });

  factory FairValueGap.fromJson(Map<String, dynamic> json) => FairValueGap(
    top:    (json['top']    as num).toDouble(),
    bottom: (json['bottom'] as num).toDouble(),
    type:   json['type']    as String,
  );
}

// Market structure analysis
class MarketStructure {
  final String trend;         // 'bullish' or 'bearish'
  final bool   bosDetected;   // Break of Structure
  final bool   chochDetected; // Change of Character
  final double keyHigh;
  final double keyLow;

  const MarketStructure({
    required this.trend,
    required this.bosDetected,
    required this.chochDetected,
    required this.keyHigh,
    required this.keyLow,
  });

  factory MarketStructure.fromJson(Map<String, dynamic> json) => MarketStructure(
    trend:         json['trend']          as String,
    bosDetected:   json['bos_detected']   as bool,
    chochDetected: json['choch_detected'] as bool,
    keyHigh:       (json['key_high']      as num).toDouble(),
    keyLow:        (json['key_low']       as num).toDouble(),
  );
}

// The complete trading signal combining SMC + LSTM
class SignalModel {
  final String          symbol;
  final String          assetType;       // 'crypto' or 'forex'
  final String          timeframe;       // '5m','15m','30m','1h','4h'
  final String          action;          // 'BUY', 'SELL', 'WAIT'
  final double          confidence;      // 0.0 - 100.0
  final double          currentPrice;
  final double          targetPrice;     // LSTM predicted price
  final double          stopLoss;        // Below/above nearest OB
  final double          takeProfit;      // Nearest FVG or structure level
  final List<OrderBlock>    orderBlocks;
  final List<FairValueGap>  fairValueGaps;
  final MarketStructure     structure;
  final String          reasoning;       // Human readable explanation
  final DateTime        generatedAt;

  const SignalModel({
    required this.symbol,
    required this.assetType,
    required this.timeframe,
    required this.action,
    required this.confidence,
    required this.currentPrice,
    required this.targetPrice,
    required this.stopLoss,
    required this.takeProfit,
    required this.orderBlocks,
    required this.fairValueGaps,
    required this.structure,
    required this.reasoning,
    required this.generatedAt,
  });

  factory SignalModel.fromJson(Map<String, dynamic> json) => SignalModel(
    symbol:       json['symbol']       as String,
    assetType:    json['asset_type']   as String,
    timeframe:    json['timeframe']    as String,
    action:       json['action']       as String,
    confidence:   (json['confidence']  as num).toDouble(),
    currentPrice: (json['current_price'] as num).toDouble(),
    targetPrice:  (json['target_price']  as num).toDouble(),
    stopLoss:     (json['stop_loss']     as num).toDouble(),
    takeProfit:   (json['take_profit']   as num).toDouble(),
    orderBlocks:  (json['order_blocks']  as List)
                    .map((e) => OrderBlock.fromJson(e))
                    .toList(),
    fairValueGaps: (json['fair_value_gaps'] as List)
                    .map((e) => FairValueGap.fromJson(e))
                    .toList(),
    structure:    MarketStructure.fromJson(json['structure']),
    reasoning:    json['reasoning']    as String,
    generatedAt:  DateTime.parse(json['generated_at'] as String),
  );

  bool get isBuy     => action == 'BUY';
  bool get isSell    => action == 'SELL';
  bool get isWait    => action == 'WAIT';
  bool get isForex   => assetType == 'forex';

  // Risk/Reward ratio
  double get riskRewardRatio {
    final risk   = (currentPrice - stopLoss).abs();
    final reward = (takeProfit - currentPrice).abs();
    if (risk == 0) return 0;
    return reward / risk;
  }
}