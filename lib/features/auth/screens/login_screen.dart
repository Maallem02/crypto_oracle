import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../providers/auth_provider.dart';
import '../../../core/constants/app_colors.dart';
import '../../../widgets/loading_indicator.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _formKey      = GlobalKey<FormState>();
  final _emailCtrl    = TextEditingController();
  final _passwordCtrl = TextEditingController();
  bool  _obscurePass  = true;

  @override
  void dispose() {
    _emailCtrl.dispose();
    _passwordCtrl.dispose();
    super.dispose();
  }

  Future<void> _login() async {
    if (!_formKey.currentState!.validate()) return;

    final success = await ref.read(authProvider.notifier).login(
      email:    _emailCtrl.text.trim(),
      password: _passwordCtrl.text,
    );

    if (success && mounted) {
      context.go('/dashboard'); // navigate to dashboard
    }
  }

  @override
  Widget build(BuildContext context) {
    final authState = ref.watch(authProvider);

    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Form(
              key: _formKey,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Logo + Title
                  const SizedBox(height: 40),
                  const Icon(Icons.candlestick_chart,
                    color: AppColors.primary, size: 64),
                  const SizedBox(height: 16),
                  const Text('CryptoOracle',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color:      AppColors.textPrimary,
                      fontSize:   28,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const Text('AI-Powered Trading Signals',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color:    AppColors.textSecondary,
                      fontSize: 14,
                    ),
                  ),
                  const SizedBox(height: 48),

                  // Email field
                  _buildTextField(
                    controller: _emailCtrl,
                    label:      'Email',
                    icon:       Icons.email_outlined,
                    keyboardType: TextInputType.emailAddress,
                    validator: (v) {
                      if (v == null || v.isEmpty) return 'Enter your email';
                      if (!v.contains('@'))       return 'Invalid email';
                      return null;
                    },
                  ),
                  const SizedBox(height: 16),

                  // Password field
                  _buildTextField(
                    controller:  _passwordCtrl,
                    label:       'Password',
                    icon:        Icons.lock_outline,
                    obscureText: _obscurePass,
                    suffixIcon: IconButton(
                      icon: Icon(
                        _obscurePass
                          ? Icons.visibility_outlined
                          : Icons.visibility_off_outlined,
                        color: AppColors.textSecondary,
                      ),
                      onPressed: () =>
                        setState(() => _obscurePass = !_obscurePass),
                    ),
                    validator: (v) {
                      if (v == null || v.isEmpty)  return 'Enter your password';
                      if (v.length < 6)            return 'Min 6 characters';
                      return null;
                    },
                  ),
                  const SizedBox(height: 8),

                  // Error message
                  if (authState.error != null)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: Text(
                        authState.error!,
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: AppColors.error),
                      ),
                    ),
                  const SizedBox(height: 24),

                  // Login button
                  SizedBox(
                    height: 52,
                    child: ElevatedButton(
                      onPressed: authState.isLoading ? null : _login,
                      style: ElevatedButton.styleFrom(
                        backgroundColor: AppColors.primary,
                        foregroundColor: AppColors.background,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      child: authState.isLoading
                        ? const LoadingIndicator(color: AppColors.background)
                        : const Text('Login',
                            style: TextStyle(
                              fontSize: 16, fontWeight: FontWeight.bold)),
                    ),
                  ),
                  const SizedBox(height: 16),

                  // Register link
                  TextButton(
                    onPressed: () => context.push('/register'),
                    child: const Text.rich(
                      TextSpan(children: [
                        TextSpan(
                          text:  "Don't have an account? ",
                          style: TextStyle(color: AppColors.textSecondary),
                        ),
                        TextSpan(
                          text:  'Register',
                          style: TextStyle(
                            color:      AppColors.primary,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ]),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  // Reusable text field builder
  Widget _buildTextField({
    required TextEditingController controller,
    required String                label,
    required IconData              icon,
    bool                           obscureText  = false,
    TextInputType                  keyboardType = TextInputType.text,
    Widget?                        suffixIcon,
    String? Function(String?)?     validator,
  }) {
    return TextFormField(
      controller:   controller,
      obscureText:  obscureText,
      keyboardType: keyboardType,
      style: const TextStyle(color: AppColors.textPrimary),
      decoration: InputDecoration(
        labelText:     label,
        labelStyle:    const TextStyle(color: AppColors.textSecondary),
        prefixIcon:    Icon(icon, color: AppColors.textSecondary),
        suffixIcon:    suffixIcon,
        filled:        true,
        fillColor:     AppColors.cardBackground,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide:   BorderSide.none,
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide:   const BorderSide(color: AppColors.primary),
        ),
        errorStyle: const TextStyle(color: AppColors.error),
      ),
      validator: validator,
    );
  }
}