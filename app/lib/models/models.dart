/// Data models mirroring the engine's JSON payloads.
library;

/// One row of a flat-playlist analysis (TASK 7's checkbox picker).
class PlaylistEntry {
  final int index; // 1-based, matches yt-dlp --playlist-items
  final String? id;
  final String title;
  final String artist; // Spotify track picker (BUG 5); '' elsewhere
  final int? duration;
  final String? url;
  /// This track's OWN cover art, never the playlist's icon (BUG 7).
  final String? thumbnail;

  const PlaylistEntry({
    required this.index,
    this.id,
    this.title = '',
    this.artist = '',
    this.duration,
    this.url,
    this.thumbnail,
  });

  factory PlaylistEntry.fromJson(Map<String, dynamic> json) => PlaylistEntry(
        index: (json['index'] as num?)?.toInt() ?? 0,
        id: json['id'] as String?,
        title: (json['title'] ?? '') as String,
        artist: (json['artist'] ?? '') as String,
        duration: (json['duration'] as num?)?.toInt(),
        url: json['url'] as String?,
        thumbnail: json['thumbnail'] as String?,
      );
}

class AnalysisResult {
  final String? error; // unsupported | invalid | unreachable
  final String? message;
  final String platform;
  final String kind; // single | playlist | both
  final String contentType;
  final String title;
  final String author;
  final String? thumbnail;
  final int? duration;
  final int count;
  final List<PlaylistEntry> entries;
  final String url;

  const AnalysisResult({
    this.error,
    this.message,
    this.platform = '',
    this.kind = 'single',
    this.contentType = 'single',
    this.title = '',
    this.author = '',
    this.thumbnail,
    this.duration,
    this.count = 1,
    this.entries = const [],
    this.url = '',
  });

  bool get isError => error != null;
  bool get isPlaylist => kind == 'playlist' || kind == 'both';

  factory AnalysisResult.fromJson(Map<String, dynamic> json) => AnalysisResult(
        error: json['error'] as String?,
        message: json['message'] as String?,
        platform: (json['platform'] ?? '') as String,
        kind: (json['kind'] ?? 'single') as String,
        contentType: (json['content_type'] ?? 'single') as String,
        title: (json['title'] ?? '') as String,
        author: (json['author'] ?? '') as String,
        thumbnail: json['thumbnail'] as String?,
        duration: (json['duration'] as num?)?.toInt(),
        count: (json['count'] as num?)?.toInt() ?? 1,
        entries: json['entries'] is List
            ? (json['entries'] as List)
                .whereType<Map>()
                .map((e) =>
                    PlaylistEntry.fromJson(Map<String, dynamic>.from(e)))
                .toList()
            : const [],
        url: (json['url'] ?? '') as String,
      );

  Map<String, dynamic> toJson() => {
        'platform': platform,
        'kind': kind,
        'content_type': contentType,
        'title': title,
        'author': author,
        'thumbnail': thumbnail,
        'duration': duration,
        'count': count,
        'url': url,
      };
}

class DownloadItem {
  final String id;
  final String url;
  final String platform;
  final String title;
  final String? thumbnail;
  final String kind;
  final String status; // queued|starting|downloading|processing|paused|completed|error|cancelled
  final double percent;
  final double? speed; // bytes/s
  final int? eta; // seconds
  final int downloaded;
  final int total;
  final int? itemIndex;
  final int? itemCount;
  final String? filePath;
  final String? error;
  final String createdAt;
  final String? completedAt;

  const DownloadItem({
    required this.id,
    required this.url,
    required this.platform,
    required this.title,
    this.thumbnail,
    this.kind = 'single',
    this.status = 'queued',
    this.percent = 0,
    this.speed,
    this.eta,
    this.downloaded = 0,
    this.total = 0,
    this.itemIndex,
    this.itemCount,
    this.filePath,
    this.error,
    this.createdAt = '',
    this.completedAt,
  });

  bool get isLive =>
      status == 'queued' || status == 'starting' ||
      status == 'downloading' || status == 'processing';

  bool get isPaused => status == 'paused';

  factory DownloadItem.fromJson(Map<String, dynamic> json) => DownloadItem(
        id: json['id'] as String,
        url: (json['url'] ?? '') as String,
        platform: (json['platform'] ?? '') as String,
        title: (json['title'] ?? '') as String,
        thumbnail: json['thumbnail'] as String?,
        kind: (json['kind'] ?? 'single') as String,
        status: (json['status'] ?? 'queued') as String,
        percent: (json['percent'] as num?)?.toDouble() ?? 0,
        speed: (json['speed'] as num?)?.toDouble(),
        eta: (json['eta'] as num?)?.toInt(),
        downloaded: (json['downloaded'] as num?)?.toInt() ?? 0,
        total: (json['total'] as num?)?.toInt() ?? 0,
        itemIndex: (json['item_index'] as num?)?.toInt(),
        itemCount: (json['item_count'] as num?)?.toInt(),
        filePath: json['file_path'] as String?,
        error: json['error'] as String?,
        createdAt: (json['created_at'] ?? '') as String,
        completedAt: json['completed_at'] as String?,
      );
}

/// An embedded subtitle stream, as reported by the Studio (TASK 3).
class SubtitleTrack {
  final int index; // ffmpeg stream index
  final String? language;
  final String? codec;

  const SubtitleTrack({required this.index, this.language, this.codec});

  factory SubtitleTrack.fromJson(Map<String, dynamic> json) => SubtitleTrack(
        index: (json['index'] as num?)?.toInt() ?? 0,
        language: json['language'] as String?,
        codec: json['codec'] as String?,
      );
}

/// A completed download that can be edited in the Studio (TASK 3).
class StudioItem {
  final String id;
  final String title;
  final String platform;
  final String? thumbnail;
  final String filePath;
  final String fileName;
  final String container;
  final double? duration; // seconds
  final int? width;
  final int? height;
  final String? videoCodec;
  final String? audioCodec;
  final int? audioBitrateKbps;
  final bool isAudio;
  final bool hasCover;
  final int coverVersion; // file mtime; busts cover image caches after edits
  final List<SubtitleTrack> subtitles;

  const StudioItem({
    required this.id,
    required this.title,
    required this.platform,
    this.thumbnail,
    this.filePath = '',
    this.fileName = '',
    this.container = '',
    this.duration,
    this.width,
    this.height,
    this.videoCodec,
    this.audioCodec,
    this.audioBitrateKbps,
    this.isAudio = false,
    this.hasCover = false,
    this.coverVersion = 0,
    this.subtitles = const [],
  });

  factory StudioItem.fromJson(Map<String, dynamic> json) => StudioItem(
        id: json['id'] as String,
        title: (json['title'] ?? '') as String,
        platform: (json['platform'] ?? 'unknown') as String,
        thumbnail: json['thumbnail'] as String?,
        filePath: (json['file_path'] ?? '') as String,
        fileName: (json['file_name'] ?? '') as String,
        container: (json['container'] ?? '') as String,
        duration: (json['duration'] as num?)?.toDouble(),
        width: (json['width'] as num?)?.toInt(),
        height: (json['height'] as num?)?.toInt(),
        videoCodec: json['video_codec'] as String?,
        audioCodec: json['audio_codec'] as String?,
        audioBitrateKbps: (json['audio_bitrate_kbps'] as num?)?.toInt(),
        isAudio: (json['is_audio'] ?? false) as bool,
        hasCover: (json['has_cover'] ?? false) as bool,
        coverVersion: (json['cover_version'] as num?)?.toInt() ?? 0,
        subtitles: json['subtitles'] is List
            ? (json['subtitles'] as List)
                .whereType<Map>()
                .map((e) =>
                    SubtitleTrack.fromJson(Map<String, dynamic>.from(e)))
                .toList()
            : const [],
      );
}

class DepInfo {
  final String name;
  final String label;
  final bool installed;
  final String? version;
  final bool updateAvailable;
  final String? latest;

  const DepInfo({
    required this.name,
    required this.label,
    required this.installed,
    this.version,
    this.updateAvailable = false,
    this.latest,
  });

  factory DepInfo.fromJson(String name, Map<String, dynamic> json) => DepInfo(
        name: name,
        label: (json['label'] ?? name) as String,
        installed: (json['installed'] ?? false) as bool,
        version: json['version'] as String?,
        updateAvailable: (json['update_available'] ?? false) as bool,
        latest: json['latest'] as String?,
      );
}

class EngineSettings {
  String videoFormat;
  String audioFormat;
  int audioBitrate;
  String quality;
  String outputDir;
  bool perPlatformSubfolders;
  String filenameTemplate;

  /// UI state of the interactive filename builder (JSON blob). The engine
  /// ignores it; [filenameTemplate] stays the single source of truth for
  /// yt-dlp and is regenerated by the builder on every change.
  String filenameComponents;
  int maxConcurrent;
  int? speedLimitKbps;
  int retries;
  bool embedThumbnail;
  bool saveThumbnailFile;
  bool embedChapters;
  bool embedMetadata;
  bool embedSubtitles;
  bool clipboardWatch;
  String cookiesFromBrowser;
  String language;
  bool showLogs;

  EngineSettings({
    this.videoFormat = 'mp4',
    this.audioFormat = 'mp3',
    this.audioBitrate = 192,
    this.quality = 'best',
    this.outputDir = '',
    this.perPlatformSubfolders = true,
    this.filenameTemplate = '%(title)s [%(id)s].%(ext)s',
    this.filenameComponents = '',
    this.maxConcurrent = 3,
    this.speedLimitKbps,
    this.retries = 3,
    this.embedThumbnail = true,
    this.saveThumbnailFile = true,
    this.embedChapters = true,
    this.embedMetadata = true,
    this.embedSubtitles = false,
    this.clipboardWatch = true,
    this.cookiesFromBrowser = '',
    this.language = 'en',
    this.showLogs = false,
  });

  factory EngineSettings.fromJson(Map<String, dynamic> json) => EngineSettings(
        videoFormat: (json['video_format'] ?? 'mp4') as String,
        audioFormat: (json['audio_format'] ?? 'mp3') as String,
        audioBitrate: (json['audio_bitrate'] as num?)?.toInt() ?? 192,
        quality: (json['quality'] ?? 'best') as String,
        outputDir: (json['output_dir'] ?? '') as String,
        perPlatformSubfolders: (json['per_platform_subfolders'] ?? true) as bool,
        filenameTemplate:
            (json['filename_template'] ?? '%(title)s [%(id)s].%(ext)s') as String,
        filenameComponents: (json['filename_components'] ?? '') as String,
        maxConcurrent: (json['max_concurrent'] as num?)?.toInt() ?? 3,
        speedLimitKbps: (json['speed_limit_kbps'] as num?)?.toInt(),
        retries: (json['retries'] as num?)?.toInt() ?? 3,
        embedThumbnail: (json['embed_thumbnail'] ?? true) as bool,
        saveThumbnailFile: (json['save_thumbnail_file'] ?? true) as bool,
        embedChapters: (json['embed_chapters'] ?? true) as bool,
        embedMetadata: (json['embed_metadata'] ?? true) as bool,
        embedSubtitles: (json['embed_subtitles'] ?? false) as bool,
        clipboardWatch: (json['clipboard_watch'] ?? true) as bool,
        cookiesFromBrowser: (json['cookies_from_browser'] ?? '') as String,
        language: (json['language'] ?? 'en') as String,
        showLogs: (json['show_logs'] ?? false) as bool,
      );

  Map<String, dynamic> toJson() => {
        'video_format': videoFormat,
        'audio_format': audioFormat,
        'audio_bitrate': audioBitrate,
        'quality': quality,
        'output_dir': outputDir,
        'per_platform_subfolders': perPlatformSubfolders,
        'filename_template': filenameTemplate,
        'filename_components': filenameComponents,
        'max_concurrent': maxConcurrent,
        'speed_limit_kbps': speedLimitKbps,
        'retries': retries,
        'embed_thumbnail': embedThumbnail,
        'save_thumbnail_file': saveThumbnailFile,
        'embed_chapters': embedChapters,
        'embed_metadata': embedMetadata,
        'embed_subtitles': embedSubtitles,
        'clipboard_watch': clipboardWatch,
        'cookies_from_browser': cookiesFromBrowser,
        'language': language,
        'show_logs': showLogs,
      };
}
