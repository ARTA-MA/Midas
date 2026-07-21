import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

/// Owns the midas-engine.exe child process.
///
/// Release layout:  <exe dir>/engine/midas-engine.exe
/// Dev mode:        run_dev.bat starts the engine on port 8765 with
///                  --no-watchdog, and we just attach to it.
class EngineProcess {
  Process? _process;
  int? port;

  static const int devPort = 8765;

  Future<int> start() async {
    if (kDebugMode) {
      port = devPort;
      return devPort;
    }
    final exeDir = File(Platform.resolvedExecutable).parent.path;
    final enginePath = '$exeDir${Platform.pathSeparator}engine'
        '${Platform.pathSeparator}midas-engine.exe';

    final process = await Process.start(enginePath, const []);
    _process = process;

    final completer = Completer<int>();
    process.stdout
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .listen((line) {
      if (line.startsWith('MIDAS_ENGINE_PORT=') && !completer.isCompleted) {
        final parsed = int.tryParse(line.split('=').last.trim());
        if (parsed != null) completer.complete(parsed);
      }
    }, onError: (_) {});
    process.stderr.drain<void>();
    process.exitCode.then((_) {
      if (!completer.isCompleted) {
        completer.completeError(StateError('Engine exited during startup'));
      }
    });

    try {
      port = await completer.future.timeout(const Duration(seconds: 30));
    } catch (_) {
      // Startup failed or timed out: don't leak the half-started engine.
      try {
        process.kill(ProcessSignal.sigkill);
      } catch (_) {}
      _process = null;
      rethrow;
    }
    return port!;
  }

  /// Graceful stop; the engine's own watchdog is the fallback.
  ///
  /// Awaits until the engine is actually gone (or force-killed), so callers
  /// can safely destroy the window afterwards without leaking the child.
  Future<void> stop() async {
    final p = _process;
    if (p == null) return;
    try {
      final client = HttpClient();
      final req = await client
          .postUrl(Uri.parse('http://127.0.0.1:$port/shutdown'))
          .timeout(const Duration(seconds: 2));
      await req.close().timeout(const Duration(seconds: 2));
      client.close(force: true);
    } catch (_) {}
    try {
      await p.exitCode.timeout(const Duration(seconds: 3));
    } catch (_) {
      try {
        p.kill(ProcessSignal.sigkill);
      } catch (_) {}
    }
    _process = null;
  }
}
