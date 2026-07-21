import 'dart:async';
import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/i18n/strings.dart';
import '../../core/theme/midas_theme.dart';
import '../../models/models.dart';
import '../../providers/app_providers.dart';
import '../../widgets/widgets.dart';

class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settings = ref.watch(settingsProvider);
    final deps = ref.watch(depsProvider);
    final notifier = ref.read(settingsProvider.notifier);

    if (settings == null) {
      return const Center(
          child: CircularProgressIndicator(color: MidasColors.gold));
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(36, 30, 36, 36),
      children: [
        Text(tr('nav.settings'), style: MidasTheme.display(34)),
        const SizedBox(height: 20),

        _Section(
          title: tr('settings.deps'),
          subtitle:
              'Portable tools installed inside the Midas folder — no admin rights needed.',
          trailing: deps.anyMissing
              ? ElevatedButton.icon(
                  onPressed: () =>
                      ref.read(depsProvider.notifier).installAll(),
                  icon: const Icon(Icons.bolt_rounded, size: 18),
                  label: Text(tr('settings.deps.install_all')),
                )
              : null,
          child: Column(children: [
            for (final dep in deps.items) _DepRow(dep: dep),
            if (!deps.loaded)
              const Padding(
                padding: EdgeInsets.all(16),
                child: CircularProgressIndicator(
                    color: MidasColors.gold, strokeWidth: 2.4),
              ),
          ]),
        ),

        _Section(
          title: tr('settings.downloads'),
          child: Column(children: [
            _DropdownRow(
              label: 'Quality',
              value: settings.quality,
              options: const {
                'best': 'Best available',
                '2160': '4K (2160p)',
                '1440': '1440p',
                '1080': '1080p',
                '720': '720p',
                'audio': 'Audio only',
              },
              onChanged: (v) => notifier.update((s) => s.quality = v),
            ),
            _DropdownRow(
              label: 'Video format',
              value: settings.videoFormat,
              options: const {'mp4': 'MP4', 'mkv': 'MKV', 'webm': 'WebM'},
              onChanged: (v) => notifier.update((s) => s.videoFormat = v),
            ),
            _DropdownRow(
              label: 'Audio format',
              value: settings.audioFormat,
              options: const {
                'mp3': 'MP3',
                'm4a': 'M4A',
                'flac': 'FLAC',
                'opus': 'Opus',
              },
              onChanged: (v) => notifier.update((s) => s.audioFormat = v),
            ),
            _DropdownRow(
              label: 'Audio bitrate',
              value: '${settings.audioBitrate}',
              options: const {
                '128': '128 kbps',
                '192': '192 kbps',
                '256': '256 kbps',
                '320': '320 kbps',
              },
              onChanged: (v) =>
                  notifier.update((s) => s.audioBitrate = int.parse(v)),
            ),
            _StepperRow(
              label: 'Max concurrent downloads',
              value: settings.maxConcurrent,
              min: 1,
              max: 8,
              onChanged: (v) =>
                  notifier.update((s) => s.maxConcurrent = v),
            ),
            _StepperRow(
              label: 'Retries per download',
              value: settings.retries,
              min: 0,
              max: 10,
              onChanged: (v) => notifier.update((s) => s.retries = v),
            ),
            _DropdownRow(
              label: 'Speed limit',
              value: '${settings.speedLimitKbps ?? 0}',
              options: const {
                '0': 'Unlimited',
                '1024': '1 MB/s',
                '2048': '2 MB/s',
                '5120': '5 MB/s',
                '10240': '10 MB/s',
              },
              onChanged: (v) => notifier.update(
                  (s) => s.speedLimitKbps = v == '0' ? null : int.parse(v)),
            ),
          ]),
        ),

        _Section(
          title: tr('settings.output'),
          child: Column(children: [
            _SettingRow(
              label: 'Output folder',
              child: OutlinedButton.icon(
                icon: const Icon(Icons.folder_rounded, size: 17),
                label: Text(
                    settings.outputDir.isEmpty
                        ? 'Choose…'
                        : settings.outputDir,
                    overflow: TextOverflow.ellipsis),
                onPressed: () async {
                  final dir =
                      await FilePicker.platform.getDirectoryPath();
                  if (dir != null) {
                    notifier.update((s) => s.outputDir = dir);
                  }
                },
              ),
            ),
            _SwitchRow(
              label: 'Per-platform subfolders',
              sub: 'e.g. Downloads/Midas/Youtube, /Spotify, …',
              value: settings.perPlatformSubfolders,
              onChanged: (v) =>
                  notifier.update((s) => s.perPlatformSubfolders = v),
            ),
            // Interactive, option-based filename builder — replaces the old
            // free-text template field. Composes filenameTemplate for the
            // engine from selectable, reorderable components.
            _FilenameBuilder(
              template: settings.filenameTemplate,
              componentsJson: settings.filenameComponents,
              onChanged: (template, componentsJson) =>
                  notifier.update((s) => s
                    ..filenameTemplate = template
                    ..filenameComponents = componentsJson),
            ),
          ]),
        ),

        _Section(
          title: tr('settings.advanced'),
          child: Column(children: [
            _SwitchRow(
                label: 'Embed thumbnails into media files',
                value: settings.embedThumbnail,
                onChanged: (v) =>
                    notifier.update((s) => s.embedThumbnail = v)),
            _SwitchRow(
                label: 'Also save thumbnail as a separate image',
                value: settings.saveThumbnailFile,
                onChanged: (v) =>
                    notifier.update((s) => s.saveThumbnailFile = v)),
            _SwitchRow(
                label: 'Embed chapters (YouTube: auto from description too)',
                value: settings.embedChapters,
                onChanged: (v) =>
                    notifier.update((s) => s.embedChapters = v)),
            _SwitchRow(
                label: 'Embed metadata (title, artist, date…)',
                value: settings.embedMetadata,
                onChanged: (v) =>
                    notifier.update((s) => s.embedMetadata = v)),
            _SwitchRow(
                label: 'Embed subtitles when available',
                value: settings.embedSubtitles,
                onChanged: (v) =>
                    notifier.update((s) => s.embedSubtitles = v)),
            _SwitchRow(
                label: 'Watch clipboard for links',
                sub: 'Auto-fills the link box when you copy a supported URL',
                value: settings.clipboardWatch,
                onChanged: (v) =>
                    notifier.update((s) => s.clipboardWatch = v)),
            _DropdownRow(
              label: 'Cookies from browser',
              sub: 'For age/region-restricted content',
              value: settings.cookiesFromBrowser.isEmpty
                  ? 'off'
                  : settings.cookiesFromBrowser,
              options: const {
                'off': 'Off',
                'chrome': 'Chrome',
                'edge': 'Edge',
                'firefox': 'Firefox',
                'brave': 'Brave',
              },
              onChanged: (v) => notifier.update(
                  (s) => s.cookiesFromBrowser = v == 'off' ? '' : v),
            ),
          ]),
        ),
        _Section(
          title: 'Developers',
          child: Column(children: [
            _SwitchRow(
                label: 'Show logs',
                sub: 'Live engine & downloader log panel at the bottom of Home',
                value: settings.showLogs,
                onChanged: (v) => notifier.update((s) => s.showLogs = v)),
          ]),
        ),
      ],
    );
  }
}

class _DepRow extends ConsumerWidget {
  final DepInfo dep;
  const _DepRow({required this.dep});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final deps = ref.watch(depsProvider);
    final installing = deps.progress.containsKey(dep.name);
    final percent = deps.progress[dep.name];
    final error = deps.errors[dep.name];

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(children: [
        Icon(
          dep.installed
              ? Icons.check_circle_rounded
              : Icons.cancel_rounded,
          size: 20,
          color: dep.installed ? MidasColors.gold : MidasColors.red,
        ),
        const SizedBox(width: 12),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(dep.label, style: MidasTheme.ui(14, weight: 700)),
            Text(
              installing
                  ? (percent != null
                      ? 'Installing… ${percent.toStringAsFixed(0)}%'
                      : 'Installing…')
                  : dep.installed
                      ? 'Installed${dep.version != null ? ' • ${dep.version}' : ''}'
                          '${dep.updateAvailable ? ' • update available: ${dep.latest}' : ''}'
                      : 'Not installed',
              style: MidasTheme.ui(12,
                  color: dep.updateAvailable
                      ? MidasColors.gold
                      : MidasColors.textDim),
            ),
            if (!installing && error != null)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(error,
                    style: MidasTheme.ui(11.5, color: MidasColors.red)),
              ),
            if (installing) ...[
              const SizedBox(height: 6),
              SizedBox(
                  width: 260,
                  child: GoldProgressBar(
                      percent: percent ?? 0, active: true)),
            ],
          ]),
        ),
        if (!installing)
          if (!dep.installed)
            OutlinedButton(
                onPressed: () =>
                    ref.read(depsProvider.notifier).install(dep.name),
                child: const Text('Install'))
          else if (dep.updateAvailable)
            OutlinedButton(
                onPressed: () =>
                    ref.read(depsProvider.notifier).install(dep.name),
                child: const Text('Update')),
      ]),
    );
  }
}

class _Section extends StatelessWidget {
  final String title;
  final String? subtitle;
  final Widget child;
  final Widget? trailing;
  const _Section(
      {required this.title, required this.child, this.subtitle, this.trailing});

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 18),
      child: Padding(
        padding: const EdgeInsets.all(22),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: MidasTheme.display(23)),
                    if (subtitle != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 3),
                        child: Text(subtitle!,
                            style: MidasTheme.ui(12.5,
                                color: MidasColors.textDim)),
                      ),
                  ]),
            ),
            if (trailing != null) trailing!,
          ]),
          const SizedBox(height: 10),
          child,
        ]),
      ),
    );
  }
}

class _SettingRow extends StatelessWidget {
  final String label;
  final String? sub;
  final Widget child;
  const _SettingRow({required this.label, required this.child, this.sub});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(children: [
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(label, style: MidasTheme.ui(14)),
            if (sub != null)
              Text(sub!,
                  style: MidasTheme.ui(11.5, color: MidasColors.textDim)),
          ]),
        ),
        const SizedBox(width: 16),
        Flexible(child: child),
      ]),
    );
  }
}

class _SwitchRow extends StatelessWidget {
  final String label;
  final String? sub;
  final bool value;
  final ValueChanged<bool> onChanged;
  const _SwitchRow(
      {required this.label,
      required this.value,
      required this.onChanged,
      this.sub});

  @override
  Widget build(BuildContext context) {
    return _SettingRow(
      label: label,
      sub: sub,
      child: Switch(value: value, onChanged: onChanged),
    );
  }
}

class _DropdownRow extends StatelessWidget {
  final String label;
  final String? sub;
  final String value;
  final Map<String, String> options;
  final ValueChanged<String> onChanged;
  const _DropdownRow(
      {required this.label,
      required this.value,
      required this.options,
      required this.onChanged,
      this.sub});

  @override
  Widget build(BuildContext context) {
    return _SettingRow(
      label: label,
      sub: sub,
      child: DropdownButton<String>(
        value: options.containsKey(value) ? value : options.keys.first,
        dropdownColor: MidasColors.raised,
        style: MidasTheme.ui(13.5),
        underline: const SizedBox(),
        borderRadius: BorderRadius.circular(10),
        items: [
          for (final entry in options.entries)
            DropdownMenuItem(value: entry.key, child: Text(entry.value)),
        ],
        onChanged: (v) {
          if (v != null) onChanged(v);
        },
      ),
    );
  }
}

class _StepperRow extends StatelessWidget {
  final String label;
  final int value;
  final int min;
  final int max;
  final ValueChanged<int> onChanged;
  const _StepperRow(
      {required this.label,
      required this.value,
      required this.min,
      required this.max,
      required this.onChanged});

  @override
  Widget build(BuildContext context) {
    return _SettingRow(
      label: label,
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        IconButton(
            onPressed:
                value > min ? () => onChanged(value - 1) : null,
            icon: const Icon(Icons.remove_rounded, size: 18)),
        SizedBox(
            width: 34,
            child: Center(
                child:
                    Text('$value', style: MidasTheme.ui(14, weight: 700)))),
        IconButton(
            onPressed:
                value < max ? () => onChanged(value + 1) : null,
            icon: const Icon(Icons.add_rounded, size: 18)),
      ]),
    );
  }
}

/// One building block of the filename: a yt-dlp token or a piece of text.
class _TplPart {
  static int _nextKey = 0;
  final int key = _nextKey++;
  String kind; // yt-dlp field name, or 'text' for a literal
  String text; // the literal value when kind == 'text'
  bool enabled;

  _TplPart(this.kind, {this.text = '', this.enabled = true});

  Map<String, dynamic> toJson() =>
      {'kind': kind, 'text': text, 'enabled': enabled};

  static _TplPart fromJson(Map<String, dynamic> j) => _TplPart(
        (j['kind'] ?? 'text') as String,
        text: (j['text'] ?? '') as String,
        enabled: (j['enabled'] ?? true) as bool,
      );
}

/// Interactive, option-based filename builder (Settings -> Output).
///
/// No fixed presets: the user freely composes the template from selectable,
/// reorderable components - yt-dlp tokens and custom text pieces - toggles
/// them on or off, edits text, drags rows (or uses the arrows) to reorder,
/// and watches a live preview. The resulting yt-dlp template is written to
/// [EngineSettings.filenameTemplate]; the builder state itself is persisted
/// as JSON in [EngineSettings.filenameComponents].
class _FilenameBuilder extends StatefulWidget {
  final String template;
  final String componentsJson;
  final void Function(String template, String componentsJson) onChanged;

  const _FilenameBuilder({
    required this.template,
    required this.componentsJson,
    required this.onChanged,
  });

  @override
  State<_FilenameBuilder> createState() => _FilenameBuilderState();
}

class _FilenameBuilderState extends State<_FilenameBuilder> {
  /// Tokens the app supports (yt-dlp output-template fields).
  static const Map<String, String> _tokenLabels = {
    'title': 'Title',
    'uploader': 'Artist / Uploader',
    'id': 'Download ID',
    'extractor': 'Site',
    'playlist_index': 'Playlist index',
    'upload_date': 'Upload date',
    'resolution': 'Resolution',
  };

  /// Sample values used by the live preview.
  static const Map<String, String> _samples = {
    'title': 'Golden Hour',
    'uploader': 'JVKE',
    'id': 'dQw4w9WgXcQ',
    'extractor': 'youtube',
    'playlist_index': '07',
    'upload_date': '20260721',
    'resolution': '1920x1080',
  };

  late List<_TplPart> _parts;
  Timer? _debounce;

  @override
  void initState() {
    super.initState();
    _parts = _load();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  List<_TplPart> _load() {
    // Preferred source: the saved builder state (keeps switched-off parts).
    if (widget.componentsJson.isNotEmpty) {
      try {
        final decoded = jsonDecode(widget.componentsJson);
        if (decoded is List && decoded.isNotEmpty) {
          return [
            for (final e in decoded)
              if (e is Map<String, dynamic>) _TplPart.fromJson(e)
          ];
        }
      } catch (_) {
        // Corrupt blob -> fall back to parsing the template string.
      }
    }
    return _parseTemplate(widget.template);
  }

  /// Best-effort parse of an existing yt-dlp template into components, so
  /// the user's current template carries over into the builder unchanged.
  static List<_TplPart> _parseTemplate(String template) {
    var body = template.trim();
    if (body.endsWith('.%(ext)s')) {
      body = body.substring(0, body.length - '.%(ext)s'.length);
    }
    final parts = <_TplPart>[];
    final re = RegExp(r'%\((\w+)\)s');
    var last = 0;
    for (final m in re.allMatches(body)) {
      if (m.start > last) {
        parts.add(_TplPart('text', text: body.substring(last, m.start)));
      }
      parts.add(_TplPart(m.group(1)!));
      last = m.end;
    }
    if (last < body.length) {
      parts.add(_TplPart('text', text: body.substring(last)));
    }
    if (parts.isEmpty) parts.add(_TplPart('title'));
    return parts;
  }

  String _template() {
    final body = [
      for (final p in _parts)
        if (p.enabled) p.kind == 'text' ? p.text : '%(${p.kind})s'
    ].join();
    // A filename can never be empty - fall back to the title.
    return '${body.trim().isEmpty ? '%(title)s' : body}.%(ext)s';
  }

  String _preview() {
    final body = [
      for (final p in _parts)
        if (p.enabled)
          p.kind == 'text' ? p.text : (_samples[p.kind] ?? p.kind)
    ].join();
    return '${body.trim().isEmpty ? _samples['title']! : body}.mp3';
  }

  /// Persist immediately (structure changes: toggle, add, remove, reorder).
  void _commit() {
    setState(() {});
    _debounce?.cancel();
    _push();
  }

  /// Persist after a short pause (used while typing custom text).
  void _commitDebounced() {
    setState(() {});
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 450), _push);
  }

  void _push() => widget.onChanged(
      _template(), jsonEncode([for (final p in _parts) p.toJson()]));

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Filename builder', style: MidasTheme.ui(13.5)),
        const SizedBox(height: 2),
        Text(
            'Compose the filename from components: switch them on or off, '
            'drag to reorder, edit text pieces, and add more below.',
            style: MidasTheme.ui(11.5, color: MidasColors.textDim)),
        const SizedBox(height: 10),
        Container(
          decoration: BoxDecoration(
            border: Border.all(color: MidasColors.border),
            borderRadius: BorderRadius.circular(10),
          ),
          clipBehavior: Clip.antiAlias,
          child: ReorderableListView.builder(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            buildDefaultDragHandles: false,
            itemCount: _parts.length,
            onReorder: (oldIndex, newIndex) {
              if (newIndex > oldIndex) newIndex -= 1;
              final part = _parts.removeAt(oldIndex);
              _parts.insert(newIndex, part);
              _commit();
            },
            itemBuilder: (_, i) => _partRow(i),
          ),
        ),
        const SizedBox(height: 8),
        _addComponentButton(),
        const SizedBox(height: 12),
        Text('Preview',
            style: MidasTheme.ui(11.5, color: MidasColors.textDim)),
        const SizedBox(height: 4),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: MidasColors.raised,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: MidasColors.border),
          ),
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(_preview(),
                    style: MidasTheme.ui(12.5,
                        color: MidasColors.goldBright, weight: 600)),
                const SizedBox(height: 2),
                Text(_template(),
                    style: MidasTheme.ui(10.5, color: MidasColors.textDim)),
              ]),
        ),
      ]),
    );
  }

  Widget _partRow(int i) {
    final p = _parts[i];
    final isText = p.kind == 'text';
    return Container(
      key: ValueKey(p.key),
      color: MidasColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      child: Row(children: [
        ReorderableDragStartListener(
          index: i,
          child: const MouseRegion(
            cursor: SystemMouseCursors.grab,
            child: Icon(Icons.drag_indicator_rounded,
                size: 18, color: MidasColors.textDim),
          ),
        ),
        Switch(
          // No explicit colors: the app-wide switchTheme styles this
          // exactly like every other toggle in Settings.
          value: p.enabled,
          onChanged: (v) {
            p.enabled = v;
            _commit();
          },
        ),
        const SizedBox(width: 4),
        Expanded(
          child: isText
              ? TextFormField(
                  key: ValueKey('text-${p.key}'),
                  initialValue: p.text,
                  style: MidasTheme.ui(12.5),
                  decoration: const InputDecoration(
                      isDense: true,
                      hintText: 'Custom text, e.g. " - " or "["'),
                  onChanged: (v) {
                    p.text = v;
                    _commitDebounced();
                  },
                )
              : Row(children: [
                  Flexible(
                    child: Text(_tokenLabels[p.kind] ?? p.kind,
                        overflow: TextOverflow.ellipsis,
                        style: MidasTheme.ui(12.5, weight: 600)),
                  ),
                  const SizedBox(width: 8),
                  Text('%(${p.kind})s',
                      style:
                          MidasTheme.ui(10.5, color: MidasColors.textDim)),
                ]),
        ),
        IconButton(
          tooltip: 'Move up',
          onPressed: i == 0
              ? null
              : () {
                  final part = _parts.removeAt(i);
                  _parts.insert(i - 1, part);
                  _commit();
                },
          icon: const Icon(Icons.arrow_upward_rounded, size: 16),
          color: MidasColors.textDim,
        ),
        IconButton(
          tooltip: 'Move down',
          onPressed: i == _parts.length - 1
              ? null
              : () {
                  final part = _parts.removeAt(i);
                  _parts.insert(i + 1, part);
                  _commit();
                },
          icon: const Icon(Icons.arrow_downward_rounded, size: 16),
          color: MidasColors.textDim,
        ),
        IconButton(
          tooltip: 'Remove',
          onPressed: _parts.length <= 1
              ? null
              : () {
                  _parts.removeAt(i);
                  _commit();
                },
          icon: const Icon(Icons.close_rounded, size: 16),
          color: MidasColors.textDim,
        ),
      ]),
    );
  }

  Widget _addComponentButton() {
    return PopupMenuButton<String>(
      tooltip: 'Add a component',
      color: MidasColors.raised,
      onSelected: (kind) {
        _parts.add(
            kind == 'text' ? _TplPart('text', text: ' - ') : _TplPart(kind));
        _commit();
      },
      itemBuilder: (_) => [
        for (final entry in _tokenLabels.entries)
          PopupMenuItem<String>(
            value: entry.key,
            child: Text('${entry.value}   %(${entry.key})s',
                style: MidasTheme.ui(12.5)),
          ),
        const PopupMenuDivider(),
        PopupMenuItem<String>(
          value: 'text',
          child: Text('Custom text', style: MidasTheme.ui(12.5)),
        ),
      ],
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: MidasColors.goldDeep),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.add_rounded, size: 16, color: MidasColors.gold),
          const SizedBox(width: 6),
          Text('Add component',
              style:
                  MidasTheme.ui(11.5, color: MidasColors.gold, weight: 600)),
        ]),
      ),
    );
  }
}
