import 'package:dio/dio.dart';

import '../models/models.dart';

class ApiClient {
  final Dio _dio;
  final int port;

  ApiClient(this.port)
      : _dio = Dio(BaseOptions(
          baseUrl: 'http://127.0.0.1:$port',
          connectTimeout: const Duration(seconds: 5),
          receiveTimeout: const Duration(minutes: 3),
        ));

  Future<AnalysisResult> analyze(String url) async {
    final res = await _dio.post('/analyze', data: {'url': url});
    return AnalysisResult.fromJson(Map<String, dynamic>.from(res.data));
  }

  Future<(List<DownloadItem>, List<DownloadItem>)> downloads() async {
    final res = await _dio.get('/downloads');
    final data = Map<String, dynamic>.from(res.data);
    List<DownloadItem> parse(String key) =>
        List<Map<String, dynamic>>.from((data[key] as List)
                .map((e) => Map<String, dynamic>.from(e)))
            .map(DownloadItem.fromJson)
            .toList();
    return (parse('active'), parse('history'));
  }

  /// Returns null on success, or the engine's friendly error message.
  ///
  /// [overrides] is the per-download quality/format override (TASK 6),
  /// [items] the --playlist-items selection (TASK 7), [selectedIndices]
  /// the 0-based Spotify track picker choice (BUG 5) and [section] the
  /// {start_sec, end_sec} clip range (TASK 9) — all optional, none of them
  /// touches the persisted settings.
  Future<String?> createDownload(String url, String mode,
      AnalysisResult preview,
      {Map<String, dynamic>? overrides,
      String? items,
      List<int> selectedIndices = const [],
      Map<String, int>? section}) async {
    final res = await _dio.post('/downloads', data: {
      'url': url,
      'mode': mode,
      'preview': preview.toJson(),
      if (overrides != null && overrides.isNotEmpty) 'overrides': overrides,
      if (items != null && items.isNotEmpty) 'items': items,
      if (selectedIndices.isNotEmpty) 'selected_indices': selectedIndices,
      if (section != null) 'section': section,
    });
    final data = res.data;
    if (data is Map && data['ok'] == true) return null;
    final message = data is Map ? data['message'] : null;
    return (message is String && message.trim().isNotEmpty)
        ? message
        : 'Could not start this download. Please retry.';
  }

  // Fire-and-forget actions: a transient connection error must never become
  // an unhandled Future error in the UI (the WS stream re-syncs state).
  Future<void> cancel(String id) async {
    try {
      await _dio.delete('/downloads/$id');
    } catch (_) {}
  }

  Future<void> retry(String id) async {
    try {
      await _dio.post('/downloads/$id/retry');
    } catch (_) {}
  }

  Future<void> pause(String id) async {
    try {
      await _dio.post('/downloads/$id/pause');
    } catch (_) {}
  }

  Future<void> resume(String id) async {
    try {
      await _dio.post('/downloads/$id/resume');
    } catch (_) {}
  }

  Future<void> pauseAll() async {
    try {
      await _dio.post('/downloads/pause-all');
    } catch (_) {}
  }

  Future<void> resumeAll() async {
    try {
      await _dio.post('/downloads/resume-all');
    } catch (_) {}
  }

  Future<void> openFolder(String id) async {
    try {
      await _dio.post('/downloads/$id/open-folder');
    } catch (_) {}
  }

  Future<void> clearHistory() async {
    try {
      await _dio.post('/history/clear');
    } catch (_) {}
  }

  /// Removes a single finished entry from the download history.
  Future<void> deleteHistoryItem(String id) async {
    try {
      await _dio.delete('/history/$id');
    } catch (_) {}
  }

  // ------------------------------------------------------------- studio

  /// Cover image URL for an editable item; [cacheBust] forces a re-fetch
  /// after the cover was replaced (the URL itself never changes otherwise).
  String studioCoverUrl(String id, {int cacheBust = 0}) =>
      'http://127.0.0.1:$port/studio/$id/cover?v=$cacheBust';

  Future<List<StudioItem>> studioItems() async {
    final res = await _dio.get('/studio/items');
    final data = res.data;
    if (data is! Map || data['ok'] != true || data['items'] is! List) {
      return const [];
    }
    return (data['items'] as List)
        .whereType<Map>()
        .map((e) => StudioItem.fromJson(Map<String, dynamic>.from(e)))
        .toList();
  }

  /// Registers a local media file as an editable Studio item (BUG 1).
  /// Returns null on success, or the engine's friendly error message.
  Future<String?> studioImport(String path) =>
      _studioRequest('POST', '/studio/import', {'file_path': path});

  /// Shared handling for studio edits: null on success, friendly message
  /// otherwise. Long jobs stream progress over the WebSocket bus.
  Future<String?> _studioRequest(String method, String path,
      [Map<String, dynamic>? body]) async {
    try {
      final res = await _dio.request(path,
          data: body,
          options: Options(
              method: method,
              receiveTimeout: const Duration(minutes: 60)));
      final data = res.data;
      if (data is Map && data['ok'] == true) return null;
      final message = data is Map ? data['message'] : null;
      return (message is String && message.trim().isNotEmpty)
          ? message
          : 'This edit could not be completed.';
    } catch (_) {
      return 'This edit could not be completed.';
    }
  }

  Future<String?> studioSetCover(String id,
          {String? imageBase64, Map<String, dynamic>? transform}) =>
      _studioRequest('POST', '/studio/$id/cover', {
        'image_base64': imageBase64,
        'transform': transform,
      });

  Future<String?> studioConvert(String id, String target,
          {int? bitrateKbps, bool keepOriginal = true}) =>
      _studioRequest('POST', '/studio/$id/convert', {
        'target': target,
        'bitrate_kbps': bitrateKbps,
        'keep_original': keepOriginal,
      });

  /// Returns {'content': srtText, 'file_path': savedPath} or throws-free
  /// {'message': friendlyError} when extraction failed.
  Future<Map<String, dynamic>> studioExtractSubtitle(
      String id, int streamIndex) async {
    try {
      final res = await _dio.post('/studio/$id/subtitles/extract',
          data: {'stream_index': streamIndex});
      final data = res.data;
      if (data is Map && data['ok'] == true) {
        return Map<String, dynamic>.from(data);
      }
      return {
        'message': (data is Map ? data['message'] : null) ??
            'Could not extract this subtitle track.'
      };
    } catch (_) {
      return {'message': 'Could not extract this subtitle track.'};
    }
  }

  Future<String?> studioSaveSubtitle(String id, String content,
          {int? replaceIndex, String? language}) =>
      _studioRequest('PUT', '/studio/$id/subtitles', {
        'content': content,
        'replace_index': replaceIndex,
        'language': language,
      });

  Future<String?> studioDeleteSubtitle(String id, int streamIndex) =>
      _studioRequest('DELETE', '/studio/$id/subtitles/$streamIndex');

  Future<String?> studioBurnSubtitle(String id,
          {int? streamIndex,
          String? content,
          String position = 'bottom',
          int fontSize = 24}) =>
      _studioRequest('POST', '/studio/$id/subtitles/burn', {
        'stream_index': streamIndex,
        'content': content,
        'position': position,
        'font_size': fontSize,
      });

  Future<String?> studioTrim(String id, List<Map<String, double>> segments,
          {String mode = 'keep',
          bool keepOriginal = true,
          bool precise = false}) =>
      _studioRequest('POST', '/studio/$id/trim', {
        'segments': segments,
        'mode': mode,
        'keep_original': keepOriginal,
        'precise': precise,
      });

  /// Poster-frame URL for the crop preview; [atSec] scrubs through the
  /// video, [cacheBust] forces a re-fetch after the file itself changed.
  String studioFrameUrl(String id, {double atSec = 0, int cacheBust = 0}) =>
      'http://127.0.0.1:$port/studio/$id/frame'
      '?t=${atSec.toStringAsFixed(2)}&v=$cacheBust';

  /// Crop the picture to a region given as fractions (0..1) of the frame.
  Future<String?> studioCrop(String id,
          {required double left,
          required double top,
          required double right,
          required double bottom,
          bool keepOriginal = true}) =>
      _studioRequest('POST', '/studio/$id/crop', {
        'left': left,
        'top': top,
        'right': right,
        'bottom': bottom,
        'keep_original': keepOriginal,
      });

  // -------------------------------------------------------------- misc

  Future<List<Map<String, dynamic>>> logs() async {
    final res = await _dio.get('/logs');
    return (res.data as List)
        .map((e) => Map<String, dynamic>.from(e as Map))
        .toList();
  }

  Future<EngineSettings> getSettings() async {
    final res = await _dio.get('/settings');
    return EngineSettings.fromJson(Map<String, dynamic>.from(res.data));
  }

  Future<EngineSettings> putSettings(EngineSettings settings) async {
    final res = await _dio.put('/settings', data: settings.toJson());
    return EngineSettings.fromJson(Map<String, dynamic>.from(res.data));
  }

  Future<List<DepInfo>> deps() async {
    final res = await _dio.get('/deps');
    final data = Map<String, dynamic>.from(res.data);
    return data.entries
        .map((e) => DepInfo.fromJson(e.key, Map<String, dynamic>.from(e.value)))
        .toList();
  }

  Future<void> installDep(String name) =>
      _dio.post('/deps/$name/install',
          options: Options(receiveTimeout: const Duration(minutes: 15)));
}
