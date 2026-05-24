import 'package:dio/dio.dart';
import '../constants/api_constants.dart';
import '../errors/app_exceptions.dart';
import '../storage/secure_storage.dart';

class DioClient {
  DioClient._();

  // Single instance used across the whole app
  static final Dio _dio = _createDio();

  static Dio get instance => _dio;

  static Dio _createDio() {
    final dio = Dio(
      BaseOptions(
        baseUrl: ApiConstants.baseUrl,
        connectTimeout: const Duration(seconds: 10),
        receiveTimeout: const Duration(seconds: 10),
        headers: _ngrokHeaders,
      ),
    );

    // Add interceptor (auto-attach JWT to every request)
    dio.interceptors.add(_AuthInterceptor());

    return dio;
  }

  // Shared headers for all Dio instances — required to bypass ngrok interstitial on Safari/iPhone
  static const Map<String, String> _ngrokHeaders = {
    'Content-Type': 'application/json',
    'ngrok-skip-browser-warning': 'true',
  };

  /// Factory: creates a plain Dio pointing to [baseUrl] with ngrok headers.
  /// Use this in repositories instead of creating a bare Dio().
  static Dio create({
    String? baseUrl,
    Duration timeout = const Duration(seconds: 15),
  }) {
    return Dio(BaseOptions(
      baseUrl: baseUrl ?? ApiConstants.baseUrl,
      connectTimeout: timeout,
      receiveTimeout: timeout,
      headers: _ngrokHeaders,
    ));
  }
}

// Interceptor: runs automatically before every request
class _AuthInterceptor extends Interceptor {
  @override
  Future<void> onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    // Get token from secure storage
    final token = await SecureStorage.getToken();

    // If token exists, attach it to the request header
    if (token != null) {
      options.headers['Authorization'] = 'Bearer $token';
    }

    handler.next(options); // continue with the request
  }

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    // Convert Dio errors into our clean AppException classes
    AppException exception;

    switch (err.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.receiveTimeout:
      case DioExceptionType.sendTimeout:
        exception = const NetworkException();
        break;
      case DioExceptionType.connectionError:
        exception = const NetworkException();
        break;
      default:
        final statusCode = err.response?.statusCode;
        final message    = err.response?.data?['detail'] ?? 'Something went wrong';

        if (statusCode == 401) {
          exception = const AuthException('Session expired. Please login again.');
        } else {
          exception = ServerException(message, statusCode: statusCode);
        }
    }

    handler.reject(DioException(
      requestOptions: err.requestOptions,
      error: exception,
    ));
  }
}