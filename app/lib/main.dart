import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:window_manager/window_manager.dart';

import 'core/theme/midas_theme.dart';
import 'providers/app_providers.dart';
import 'screens/shell.dart';
import 'widgets/widgets.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await windowManager.ensureInitialized();
  const options = WindowOptions(
    size: Size(1220, 780),
    minimumSize: Size(1020, 660),
    center: true,
    title: 'Midas',
    backgroundColor: Color(0xFF0B0B0D),
  );
  await windowManager.waitUntilReadyToShow(options, () async {
    await windowManager.show();
    await windowManager.focus();
    // Keep the window on top while the splash is visible so the user
    // always sees the app launching; released once the shell loads.
    await windowManager.setAlwaysOnTop(true);
  });
  runApp(const ProviderScope(child: MidasApp()));
}

class MidasApp extends StatelessWidget {
  const MidasApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Midas',
      debugShowCheckedModeBanner: false,
      theme: MidasTheme.dark(),
      home: const _SplashGate(),
    );
  }
}

/// Splash artwork while the engine boots; fades into the app shell.
/// The window stays always-on-top during the splash, and a minimum
/// display time guarantees the splash is fully seen even when the
/// engine starts instantly.
class _SplashGate extends ConsumerStatefulWidget {
  const _SplashGate();

  @override
  ConsumerState<_SplashGate> createState() => _SplashGateState();
}

class _SplashGateState extends ConsumerState<_SplashGate>
    with WindowListener {
  static const _minSplash = Duration(milliseconds: 2800);
  bool _minTimeElapsed = false;
  bool _pinned = true;
  bool _closing = false;

  @override
  void initState() {
    super.initState();
    // Intercept the window close button so we can call /shutdown on the
    // engine before the process goes away (no orphan midas-engine.exe).
    windowManager.addListener(this);
    windowManager.setPreventClose(true);
    Future<void>.delayed(_minSplash, () {
      if (mounted) setState(() => _minTimeElapsed = true);
    });
  }

  @override
  void dispose() {
    windowManager.removeListener(this);
    super.dispose();
  }

  @override
  void onWindowClose() async {
    if (_closing) return;
    _closing = true;
    try {
      await ref
          .read(engineProcessProvider)
          .stop()
          .timeout(const Duration(seconds: 6));
    } catch (_) {
      // The engine's own watchdog is the last-resort fallback.
    }
    await windowManager.destroy();
  }

  void _unpin() {
    if (!_pinned) return;
    _pinned = false;
    windowManager.setAlwaysOnTop(false);
  }

  @override
  Widget build(BuildContext context) {
    final port = ref.watch(enginePortProvider);
    final Widget child;
    if (port.hasValue && _minTimeElapsed) {
      _unpin();
      child = const Shell();
    } else if (port.hasError) {
      _unpin();
      child = _SplashScreen(
        key: const ValueKey('error'),
        error: true,
        onRetry: () => ref.invalidate(enginePortProvider),
      );
    } else {
      child = const _SplashScreen(key: ValueKey('loading'));
    }
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 600),
      switchInCurve: Curves.easeOut,
      child: child,
    );
  }
}

class _SplashScreen extends StatelessWidget {
  final bool error;
  final VoidCallback? onRetry;
  const _SplashScreen({super.key, this.error = false, this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(fit: StackFit.expand, children: [
        Image.asset('assets/images/splash.jpg', fit: BoxFit.cover)
            .animate()
            .fadeIn(duration: 900.ms),
        Container(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [Colors.transparent, Color(0xE60B0B0D)],
            ),
          ),
        ),
        Column(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            const MidasWordmark(size: 54),
            const SizedBox(height: 10),
            Text('Everything you touch turns to gold.',
                style: MidasTheme.ui(14, color: MidasColors.textDim))
                .animate()
                .fadeIn(delay: 400.ms),
            const SizedBox(height: 34),
            if (!error)
              const SizedBox(
                width: 26,
                height: 26,
                child: CircularProgressIndicator(
                    strokeWidth: 2.4, color: MidasColors.gold),
              )
            else ...[
              Text('The Midas engine could not start.',
                  style: MidasTheme.ui(14, color: MidasColors.red)),
              const SizedBox(height: 12),
              OutlinedButton(onPressed: onRetry, child: const Text('Retry')),
            ],
            const SizedBox(height: 56),
          ],
        ),
      ]),
    );
  }
}
