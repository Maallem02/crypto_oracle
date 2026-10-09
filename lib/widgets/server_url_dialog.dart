import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import '../core/config/api_config.dart';
import '../core/constants/api_constants.dart';
import '../core/network/dio_client.dart';

/// Lets the user point the app at a different backend without a rebuild.
///
/// Deliberately reachable from the LOGIN screen: when the address is wrong
/// every request fails, including login, so a setting hidden behind
/// authentication would be unreachable exactly when it is needed.
class ServerUrlDialog extends StatefulWidget {
  const ServerUrlDialog({super.key});

  static Future<void> show(BuildContext context) =>
      showDialog(context: context, builder: (_) => const ServerUrlDialog());

  @override
  State<ServerUrlDialog> createState() => _ServerUrlDialogState();
}

class _ServerUrlDialogState extends State<ServerUrlDialog> {
  late final TextEditingController _ctrl =
      TextEditingController(text: ApiConfig.baseUrl);
  String? _result;
  bool _testing = false;
  bool _ok = false;

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  /// Probe /health before saving, so a typo is caught here rather than
  /// surfacing later as an opaque DioException on the login button.
  Future<void> _test() async {
    setState(() {
      _testing = true;
      _result = null;
      _ok = false;
    });
    final url = _ctrl.text.trim();
    try {
      final dio = Dio(BaseOptions(
        connectTimeout: const Duration(seconds: 6),
        receiveTimeout: const Duration(seconds: 6),
      ));
      final normalised = _preview(url);
      final r = await dio.get('$normalised/health');
      final ok = r.statusCode == 200 && (r.data is Map) && r.data['status'] == 'ok';
      setState(() {
        _ok = ok;
        _result = ok
            ? 'Connected — instance "${r.data['instance'] ?? '?'}"'
            : 'Reachable but unexpected reply: ${r.data}';
      });
    } catch (e) {
      setState(() {
        _ok = false;
        _result = 'Cannot reach it. Check the backend is running and that '
            'this device is on the same network.';
      });
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  // Mirrors ApiConfig._normalise so the preview matches what gets saved.
  String _preview(String raw) {
    var s = raw.trim();
    if (s.isEmpty) return ApiConstants.baseUrl;
    if (!s.startsWith('http://') && !s.startsWith('https://')) {
      final local = RegExp(r'^(\d{1,3}\.){3}\d{1,3}(:\d+)?$').hasMatch(s) ||
          s.startsWith('localhost') ||
          s.endsWith('.local');
      s = (local ? 'http://' : 'https://') + s;
    }
    while (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    return s;
  }

  Future<void> _save() async {
    await ApiConfig.setBaseUrl(_ctrl.text);
    DioClient.reset();
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Server address'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _ctrl,
              autocorrect: false,
              enableSuggestions: false,
              keyboardType: TextInputType.url,
              // keeps the "Will be saved as" preview in step with typing
              onChanged: (_) => setState(() => _result = null),
              decoration: const InputDecoration(
                labelText: 'Backend URL',
                hintText: '192.168.0.129:8000',
                helperText: 'LAN address, Tailscale name, or a tunnel URL',
              ),
            ),
            const SizedBox(height: 12),
            Text('Will be saved as:  ${_preview(_ctrl.text)}',
                style: Theme.of(context).textTheme.bodySmall),
            if (_result != null) ...[
              const SizedBox(height: 12),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(_ok ? Icons.check_circle : Icons.error_outline,
                      size: 18, color: _ok ? Colors.green : Colors.redAccent),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(_result!,
                        style: TextStyle(
                            color: _ok ? Colors.green : Colors.redAccent,
                            fontSize: 12)),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () async {
            await ApiConfig.reset();
            DioClient.reset();
            if (mounted) Navigator.of(context).pop();
          },
          child: const Text('Default'),
        ),
        TextButton(
          onPressed: _testing ? null : _test,
          child: _testing
              ? const SizedBox(
                  width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
              : const Text('Test'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(onPressed: _save, child: const Text('Save')),
      ],
    );
  }
}
