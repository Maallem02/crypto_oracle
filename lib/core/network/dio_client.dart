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
        headers: {'Content-Type': 'application/json'},
      ),
    );

    // Add interceptor (auto-attach JWT to every request)
    dio.interceptors.add(_AuthInterceptor());

    return dio;
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