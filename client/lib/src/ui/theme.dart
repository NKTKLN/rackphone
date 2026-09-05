import 'package:flutter/material.dart';

/// Keeps the chrome quiet so live rack state remains the focus.
ThemeData rackphoneTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: const Color(0xFF7C6BA8),
    brightness: Brightness.dark,
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: scheme.surface,
    appBarTheme: AppBarTheme(
      backgroundColor: scheme.surface,
      surfaceTintColor: scheme.surfaceTint.withValues(alpha: 0.10),
    ),
    cardTheme: CardThemeData(
      surfaceTintColor: scheme.surfaceTint.withValues(alpha: 0.08),
    ),
  );
}
