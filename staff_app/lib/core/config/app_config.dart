import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show kIsWeb;

/// Build-time configuration. Nothing secret lives in the app — only the API address.
///
///   flutter run --dart-define=API_BASE_URL=https://api.example.com
class AppConfig {
  const AppConfig({required this.apiBaseUrl});

  final String apiBaseUrl;

  static AppConfig fromEnvironment() {
    const configured = String.fromEnvironment('API_BASE_URL');
    if (configured.isNotEmpty) return const AppConfig(apiBaseUrl: configured);
    // Development defaults: the Android emulator reaches the host machine via 10.0.2.2.
    final host = !kIsWeb && Platform.isAndroid ? '10.0.2.2' : '127.0.0.1';
    return AppConfig(apiBaseUrl: 'http://$host:8000');
  }
}
