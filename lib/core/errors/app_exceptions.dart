// Base exception class for the whole app
class AppException implements Exception {
  final String message;
  final int? statusCode;

  const AppException(this.message, {this.statusCode});

  @override
  String toString() => message;
}

// Network is down or timeout
class NetworkException extends AppException {
  const NetworkException()
      : super('No internet connection. Please check your network.');
}

// Server returned an error (4xx, 5xx)
class ServerException extends AppException {
  const ServerException(super.message, {super.statusCode});
}

// Wrong credentials, token expired, etc.
class AuthException extends AppException {
  const AuthException(super.message);
}

// Data parsing failed
class ParseException extends AppException {
  const ParseException() : super('Failed to parse server response.');
}