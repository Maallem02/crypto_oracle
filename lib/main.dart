import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'app.dart';
import 'core/config/api_config.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Load the saved backend URL before the first request is made.
  await ApiConfig.load();
  runApp(const ProviderScope(child: CryptoOracleApp()));
}