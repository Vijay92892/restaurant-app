import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import 'app.dart';
import 'core/config/app_config.dart';
import 'core/network/api_client.dart';
import 'core/storage/token_storage.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (kReleaseMode) _keepGoingAfterErrors();
  final config = AppConfig.fromEnvironment();
  final storage = SecureTokenStorage();
  final api = ApiClient(baseUrl: config.apiBaseUrl, storage: storage);
  runApp(StaffApp(api: api, rememberedCode: await storage.readRestaurantCode()));
}

/// A kitchen screen must survive one bad frame or one failed background task. Release builds
/// log only the error's type (messages could contain order data) and show a small notice in
/// place of a widget that failed to build, instead of a grey box.
void _keepGoingAfterErrors() {
  PlatformDispatcher.instance.onError = (error, stack) {
    debugPrint('Unhandled error: ${error.runtimeType}');
    return true;
  };
  ErrorWidget.builder = (details) => const Material(
    child: Center(
      child: Padding(
        padding: EdgeInsets.all(12),
        child: Text('This part of the screen could not be shown. Reopen the screen to try again.'),
      ),
    ),
  );
}
