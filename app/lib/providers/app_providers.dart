import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/engine_process.dart';
import '../services/ws_client.dart';

final engineProcessProvider = Provider<EngineProcess>((ref) {
  final engine = EngineProcess();
  ref.onDispose(engine.stop);
  return engine;
});

/// Starts the engine and resolves once /health responds.
final enginePortProvider = FutureProvider<int>((ref) async {
  final engine = ref.watch(engineProcessProvider);
  final port = await engine.start();
  final dio = Dio();
  for (var attempt = 0; attempt < 120; attempt++) {
    try {
      final res = await dio.get('http://127.0.0.1:$port/health');
      if (res.data['ok'] == true) return port;
    } catch (_) {}
    await Future<void>.delayed(const Duration(milliseconds: 400));
  }
  throw StateError('The Midas engine did not start.');
});

final apiProvider = Provider<ApiClient?>((ref) {
  final port = ref.watch(enginePortProvider).valueOrNull;
  return port == null ? null : ApiClient(port);
});

final wsProvider = Provider<WsClient?>((ref) {
  final port = ref.watch(enginePortProvider).valueOrNull;
  if (port == null) return null;
  final ws = WsClient(port);
  ref.onDispose(ws.dispose);
  return ws;
});

// ---------------------------------------------------------------- downloads

class DownloadsState {
  final List<DownloadItem> active;
  final List<DownloadItem> history;
  final bool loaded;
  const DownloadsState(
      {this.active = const [], this.history = const [], this.loaded = false});

  DownloadsState copyWith(
          {List<DownloadItem>? active,
          List<DownloadItem>? history,
          bool? loaded}) =>
      DownloadsState(
          active: active ?? this.active,
          history: history ?? this.history,
          loaded: loaded ?? this.loaded);
}

class DownloadsNotifier extends StateNotifier<DownloadsState> {
  final Ref _ref;
  StreamSubscription? _sub;

  DownloadsNotifier(this._ref) : super(const DownloadsState()) {
    _ref.listen<WsClient?>(wsProvider, (_, ws) => _attach(ws),
        fireImmediately: true);
    _ref.listen<ApiClient?>(apiProvider, (_, api) {
      if (api != null) refresh();
    }, fireImmediately: true);
  }

  void _attach(WsClient? ws) {
    _sub?.cancel();
    if (ws == null) return;
    _sub = ws.events.listen((event) {
      final type = event['type'];
      if (type == 'download.progress' || type == 'download.state') {
        _upsert(DownloadItem.fromJson(
            Map<String, dynamic>.from(event['item'])));
        if (type == 'download.state') refresh(historyOnly: true);
      } else if (type == 'queue.changed') {
        refresh();
      }
    });
  }

  void _upsert(DownloadItem item) {
    final list = [...state.active];
    final index = list.indexWhere((i) => i.id == item.id);
    if (index >= 0) {
      list[index] = item;
    } else {
      list.insert(0, item);
    }
    state = state.copyWith(active: list);
  }

  Future<void> refresh({bool historyOnly = false}) async {
    final api = _ref.read(apiProvider);
    if (api == null) return;
    try {
      final (active, history) = await api.downloads();
      state = DownloadsState(
          active: historyOnly ? state.active : active,
          history: history,
          loaded: true);
    } catch (_) {}
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}

final downloadsProvider =
    StateNotifierProvider<DownloadsNotifier, DownloadsState>(
        DownloadsNotifier.new);

// ----------------------------------------------------------------- settings

class SettingsNotifier extends StateNotifier<EngineSettings?> {
  final Ref _ref;
  SettingsNotifier(this._ref) : super(null) {
    _ref.listen<ApiClient?>(apiProvider, (_, api) async {
      if (api != null && state == null) {
        try {
          state = await api.getSettings();
        } catch (_) {}
      }
    }, fireImmediately: true);
  }

  // PUTs are chained so rapid toggles can never race each other and land
  // on the engine out of order (last write must win).
  Future<void> _putChain = Future.value();

  Future<void> update(void Function(EngineSettings s) mutate) {
    final current = state;
    final api = _ref.read(apiProvider);
    if (current == null || api == null) return Future.value();
    mutate(current);
    final snapshot = EngineSettings.fromJson(current.toJson());
    state = snapshot; // clone -> notify
    _putChain = _putChain.then((_) async {
      try {
        await api.putSettings(snapshot);
      } catch (_) {}
    });
    return _putChain;
  }
}

final settingsProvider =
    StateNotifierProvider<SettingsNotifier, EngineSettings?>(
        SettingsNotifier.new);

// --------------------------------------------------------------------- deps

class DepsState {
  final List<DepInfo> items;
  final Map<String, double?> progress; // name -> percent while installing
  final Map<String, String> errors; // name -> last install error message
  final bool loaded;
  const DepsState(
      {this.items = const [],
      this.progress = const {},
      this.errors = const {},
      this.loaded = false});

  bool get anyMissing => items.any((d) => !d.installed);

  DepsState copyWith(
          {List<DepInfo>? items,
          Map<String, double?>? progress,
          Map<String, String>? errors,
          bool? loaded}) =>
      DepsState(
          items: items ?? this.items,
          progress: progress ?? this.progress,
          errors: errors ?? this.errors,
          loaded: loaded ?? this.loaded);
}

class DepsNotifier extends StateNotifier<DepsState> {
  final Ref _ref;
  StreamSubscription? _sub;

  DepsNotifier(this._ref) : super(const DepsState()) {
    _ref.listen<ApiClient?>(apiProvider, (_, api) {
      if (api != null) refresh();
    }, fireImmediately: true);
    _ref.listen<WsClient?>(wsProvider, (_, ws) {
      _sub?.cancel();
      _sub = ws?.events.listen((event) {
        if (event['type'] == 'deps.progress') {
          state = state.copyWith(progress: {
            ...state.progress,
            event['name'] as String: (event['percent'] as num?)?.toDouble(),
          });
        } else if (event['type'] == 'deps.state') {
          final s = event['state'];
          final name = event['name'] as String;
          if (s == 'downloading') {
            final errors = {...state.errors}..remove(name);
            state = state.copyWith(errors: errors);
          } else if (s == 'installed' || s == 'error') {
            final progress = {...state.progress}..remove(name);
            final errors = {...state.errors};
            if (s == 'error') {
              errors[name] =
                  (event['message'] ?? 'Install failed.') as String;
            } else {
              errors.remove(name);
            }
            state = state.copyWith(progress: progress, errors: errors);
            refresh();
          }
        }
      });
    }, fireImmediately: true);
  }

  Future<void> refresh() async {
    final api = _ref.read(apiProvider);
    if (api == null) return;
    try {
      state = state.copyWith(items: await api.deps(), loaded: true);
    } catch (_) {}
  }

  Future<void> install(String name) async {
    final api = _ref.read(apiProvider);
    if (api == null) return;
    final errors = {...state.errors}..remove(name);
    state = state.copyWith(
        progress: {...state.progress, name: null}, errors: errors);
    try {
      await api.installDep(name);
    } catch (_) {}
    await refresh();
  }

  Future<void> installAll() async {
    for (final dep in state.items.where((d) =>
        !d.installed && d.name != 'ffprobe')) {
      await install(dep.name);
    }
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}

final depsProvider =
    StateNotifierProvider<DepsNotifier, DepsState>(DepsNotifier.new);

// ---------------------------------------------------------------- dev logs

/// Live application log lines shown in the developer panel on Home.
class LogsNotifier extends StateNotifier<List<Map<String, dynamic>>> {
  final Ref _ref;
  StreamSubscription? _sub;

  LogsNotifier(this._ref) : super(const []) {
    _ref.listen<WsClient?>(wsProvider, (_, ws) {
      _sub?.cancel();
      if (ws == null) return;
      _load();
      _sub = ws.events.listen((event) {
        if (event['type'] == 'log.line') {
          final next = [...state, event];
          state = next.length > 400 ? next.sublist(next.length - 400) : next;
        }
      });
    }, fireImmediately: true);
  }

  Future<void> _load() async {
    final api = _ref.read(apiProvider);
    if (api == null) return;
    try {
      state = await api.logs();
    } catch (_) {
      // Engine not ready yet; the WS listener streams new lines anyway.
    }
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}

final logsProvider =
    StateNotifierProvider<LogsNotifier, List<Map<String, dynamic>>>(
        LogsNotifier.new);

// ------------------------------------------------------------------ studio

/// Editable completed downloads plus live progress of studio jobs (TASK 3).
class StudioState {
  final List<StudioItem> items;
  final bool loaded;
  final String? selectedId;
  final Map<String, double?> progress; // itemId -> percent while working
  final Map<String, String> runningOp; // itemId -> current operation name
  const StudioState(
      {this.items = const [],
      this.loaded = false,
      this.selectedId,
      this.progress = const {},
      this.runningOp = const {}});

  StudioItem? get selected {
    for (final item in items) {
      if (item.id == selectedId) return item;
    }
    return null;
  }

  bool isBusy(String id) => runningOp.containsKey(id);

  StudioState copyWith(
          {List<StudioItem>? items,
          bool? loaded,
          String? selectedId,
          bool clearSelection = false,
          Map<String, double?>? progress,
          Map<String, String>? runningOp}) =>
      StudioState(
          items: items ?? this.items,
          loaded: loaded ?? this.loaded,
          selectedId:
              clearSelection ? null : (selectedId ?? this.selectedId),
          progress: progress ?? this.progress,
          runningOp: runningOp ?? this.runningOp);
}

class StudioNotifier extends StateNotifier<StudioState> {
  final Ref _ref;
  StreamSubscription? _sub;

  StudioNotifier(this._ref) : super(const StudioState()) {
    _ref.listen<ApiClient?>(apiProvider, (_, api) {
      if (api != null) refresh();
    }, fireImmediately: true);
    _ref.listen<WsClient?>(wsProvider, (_, ws) => _attach(ws),
        fireImmediately: true);
  }

  void _attach(WsClient? ws) {
    _sub?.cancel();
    if (ws == null) return;
    _sub = ws.events.listen((event) {
      final type = event['type'];
      if (type == 'studio.progress') {
        final id = event['item_id'] as String?;
        if (id == null) return;
        state = state.copyWith(progress: {
          ...state.progress,
          id: (event['percent'] as num?)?.toDouble(),
        });
      } else if (type == 'studio.state') {
        final id = event['item_id'] as String?;
        if (id == null) return;
        final s = event['state'];
        if (s == 'running') {
          state = state.copyWith(runningOp: {
            ...state.runningOp,
            id: (event['op'] ?? '') as String,
          });
        } else {
          // done | error -> the job is over either way.
          final runningOp = {...state.runningOp}..remove(id);
          final progress = {...state.progress}..remove(id);
          state =
              state.copyWith(runningOp: runningOp, progress: progress);
          if (s == 'done') refresh();
        }
      } else if (type == 'download.state') {
        // A download just finished -> a new editable file (BUG 2).
        final item = event['item'];
        if (item is Map && item['status'] == 'completed') refresh();
      } else if (type == 'queue.changed') {
        // A download finished (or a convert/trim created a new row).
        refresh();
      }
    });
  }

  Future<void> refresh() async {
    final api = _ref.read(apiProvider);
    if (api == null) return;
    try {
      final items = await api.studioItems();
      final stillThere = items.any((i) => i.id == state.selectedId);
      state = state.copyWith(
          items: items,
          loaded: true,
          clearSelection: !stillThere && state.selectedId != null);
    } catch (_) {
      state = state.copyWith(loaded: true);
    }
  }

  void select(String? id) => state = state.copyWith(
      selectedId: id, clearSelection: id == null);

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}

final studioProvider = StateNotifierProvider<StudioNotifier, StudioState>(
    StudioNotifier.new);
