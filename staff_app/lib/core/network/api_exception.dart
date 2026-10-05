import 'package:dio/dio.dart';

/// An error from the API's uniform envelope `{"error": {"code", "message", ...}}`,
/// or a network failure (statusCode 0).
class ApiException implements Exception {
  const ApiException({
    required this.statusCode,
    required this.code,
    required this.message,
    this.details = const {},
  });

  final int statusCode;
  final String code;
  final String message;
  final Map<String, Object?> details;

  bool get isNetwork => statusCode == 0;
  bool get isUnauthorized => statusCode == 401;
  bool get isConflict => statusCode == 409;

  factory ApiException.fromDio(DioException error) {
    final response = error.response;
    if (response == null) {
      return const ApiException(
        statusCode: 0,
        code: 'network',
        message: 'No connection to the server. Check the Wi-Fi and try again.',
      );
    }
    final data = response.data;
    if (data is Map && data['error'] is Map) {
      final body = Map<String, Object?>.from(data['error'] as Map);
      return ApiException(
        statusCode: response.statusCode ?? 0,
        code: body['code'] as String? ?? 'error',
        message: body['message'] as String? ?? 'Something went wrong.',
        details: body,
      );
    }
    return ApiException(
      statusCode: response.statusCode ?? 0,
      code: 'error',
      message: 'Something went wrong (${response.statusCode}).',
    );
  }

  @override
  String toString() => 'ApiException($statusCode, $code, $message)';
}
