import 'package:flutter/material.dart';

/// The Midas palette: matte black canvas, luminous gold, restrained red.
class MidasColors {
  static const bg = Color(0xFF0B0B0D);
  static const surface = Color(0xFF141416);
  static const raised = Color(0xFF1C1C1F);
  static const border = Color(0xFF2A2A2E);
  static const gold = Color(0xFFD4AF37);
  static const goldBright = Color(0xFFF5D061);
  static const goldDeep = Color(0xFF9C7E1F);
  static const red = Color(0xFFC22740); // errors / destructive only
  static const text = Color(0xFFF2EFE6);
  static const textDim = Color(0xFF9C988D);

  static const goldGradient = LinearGradient(
    colors: [goldDeep, goldBright, gold],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );
}

class MidasTheme {
  /// Wordmark & display face. Open-license Bruney stand-in is Cormorant.
  /// If you personally own Bruney, add it in pubspec.yaml and set this
  /// to 'Bruney' - nothing else needs to change.
  static const String displayFamily = 'Cormorant';
  static const String uiFamily = 'Manrope';

  static TextStyle display(double size,
          {Color color = MidasColors.text, double weight = 600}) =>
      TextStyle(
        fontFamily: displayFamily,
        fontSize: size,
        color: color,
        fontVariations: [FontVariation('wght', weight)],
        letterSpacing: size * 0.04,
        height: 1.1,
      );

  static TextStyle ui(double size,
          {Color color = MidasColors.text, double weight = 400,
          double? letterSpacing}) =>
      TextStyle(
        fontFamily: uiFamily,
        fontSize: size,
        color: color,
        fontVariations: [FontVariation('wght', weight)],
        letterSpacing: letterSpacing,
        height: 1.45,
      );

  static ThemeData dark() {
    final base = ThemeData.dark(useMaterial3: true);
    return base.copyWith(
      scaffoldBackgroundColor: MidasColors.bg,
      colorScheme: const ColorScheme.dark(
        primary: MidasColors.gold,
        secondary: MidasColors.goldBright,
        surface: MidasColors.surface,
        error: MidasColors.red,
        onPrimary: Color(0xFF141414),
        onSurface: MidasColors.text,
      ),
      textTheme: base.textTheme.apply(
        fontFamily: uiFamily,
        bodyColor: MidasColors.text,
        displayColor: MidasColors.text,
      ),
      cardTheme: CardThemeData(
        color: MidasColors.surface,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: const BorderSide(color: MidasColors.border),
        ),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: MidasColors.raised,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: const BorderSide(color: MidasColors.border),
        ),
        titleTextStyle: display(26),
        contentTextStyle: ui(15, color: MidasColors.textDim),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: MidasColors.raised,
        contentTextStyle: ui(14),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(8),
          side: const BorderSide(color: MidasColors.border),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: MidasColors.surface,
        hintStyle: ui(15, color: MidasColors.textDim),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: MidasColors.border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: MidasColors.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: MidasColors.gold, width: 1.4),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: MidasColors.gold,
          foregroundColor: const Color(0xFF141414),
          textStyle: ui(15, weight: 700),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(10)),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: MidasColors.gold,
          side: const BorderSide(color: MidasColors.goldDeep),
          textStyle: ui(14, weight: 600),
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(10)),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: MidasColors.textDim,
          textStyle: ui(14, weight: 600),
        ),
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) =>
            states.contains(WidgetState.selected)
                ? const Color(0xFF141414)
                : MidasColors.textDim),
        trackColor: WidgetStateProperty.resolveWith((states) =>
            states.contains(WidgetState.selected)
                ? MidasColors.gold
                : MidasColors.raised),
      ),
      dividerTheme: const DividerThemeData(color: MidasColors.border),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: MidasColors.raised,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: MidasColors.border),
        ),
        textStyle: ui(12),
      ),
    );
  }
}
