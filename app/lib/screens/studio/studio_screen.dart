import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/i18n/strings.dart';
import '../../core/theme/midas_theme.dart';
import '../../core/utils/format.dart';
import '../../models/models.dart';
import '../../providers/app_providers.dart';
import '../../widgets/widgets.dart';

/// Studio (TASK 3): edit cover art, convert formats, manage subtitles and
/// trim completed downloads. All media work happens in the Python engine;
/// this screen only collects parameters and follows `studio.*` events.
class StudioScreen extends ConsumerWidget {
  const StudioScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(studioProvider);

    // The header (with Import/Refresh) must stay visible even before the
    // first download so a local file can always be imported.
    final empty = state.loaded && state.items.isEmpty;
    final selected = state.selected;
    return Padding(
      padding: const EdgeInsets.fromLTRB(36, 30, 36, 24),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(tr('nav.studio'), style: MidasTheme.display(34)),
          const Spacer(),
          IconButton(
            tooltip: tr('studio.import'),
            onPressed: () => _importLocalFile(context, ref),
            icon: const Icon(Icons.file_open_rounded,
                size: 20, color: MidasColors.textDim),
          ),
          IconButton(
            tooltip: tr('studio.refresh'),
            onPressed: () => ref.read(studioProvider.notifier).refresh(),
            icon: const Icon(Icons.refresh_rounded,
                size: 20, color: MidasColors.textDim),
          ),
        ]),
        const SizedBox(height: 18),
        Expanded(
          child: empty
              ? MidasEmptyState(
                  image: 'assets/images/empty_state.jpg',
                  title: tr('studio.empty.title'),
                  body: tr('studio.empty.body'),
                )
              : Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(width: 300, child: _ItemList(state: state)),
            const SizedBox(width: 18),
            Expanded(
              child: selected == null
                  ? MidasEmptyState(
                      image: 'assets/images/empty_state.jpg',
                      title: tr('studio.select.title'),
                      body: tr('studio.select.body'),
                    )
                  : _EditorPane(key: ValueKey(selected.id), item: selected),
            ),
          ]),
        ),
      ]),
    );
  }
}

/// "Import local file" (BUG 1): register any media file on disk as an
/// editable Studio item via POST /studio/import.
Future<void> _importLocalFile(BuildContext context, WidgetRef ref) async {
  final api = ref.read(apiProvider);
  if (api == null) return;
  final picked = await FilePicker.platform.pickFiles(
    type: FileType.custom,
    allowedExtensions: const [
      'mp4', 'mkv', 'webm', 'mov', 'mp3', 'm4a', 'flac', 'opus', 'ogg',
      'wav',
    ],
  );
  final path = picked?.files.single.path;
  if (path == null) return;
  final error = await api.studioImport(path);
  if (error == null) {
    await ref.read(studioProvider.notifier).refresh();
  }
  if (error != null) _showStudioResult(context, error);
}

void _showStudioResult(BuildContext context, String? error) {
  if (!context.mounted) return;
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(
    content: Row(children: [
      Icon(
          error == null
              ? Icons.auto_awesome_rounded
              : Icons.error_outline_rounded,
          color: error == null ? MidasColors.gold : MidasColors.red,
          size: 18),
      const SizedBox(width: 10),
      Expanded(
          child:
              Text(error ?? tr('studio.done'), style: MidasTheme.ui(14))),
    ]),
  ));
}

/// "7:05" / "1:02:33" for a number of seconds.
String _clockString(int totalSeconds) {
  final h = totalSeconds ~/ 3600;
  final m = (totalSeconds % 3600) ~/ 60;
  final s = totalSeconds % 60;
  String two(int v) => v.toString().padLeft(2, '0');
  return h > 0 ? '$h:${two(m)}:${two(s)}' : '$m:${two(s)}';
}

/// Parses "mm:ss", "hh:mm:ss" or plain seconds; null when not a time.
int? _parseClock(String text) {
  final parts = text.trim().split(':');
  if (parts.isEmpty || parts.length > 3) return null;
  var total = 0;
  for (final part in parts) {
    final value = int.tryParse(part.trim());
    if (value == null || value < 0) return null;
    total = total * 60 + value;
  }
  return total;
}

class _ItemList extends ConsumerWidget {
  final StudioState state;
  const _ItemList({required this.state});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final api = ref.watch(apiProvider);
    return ListView.builder(
      itemCount: state.items.length,
      itemBuilder: (context, i) {
        final item = state.items[i];
        final selected = item.id == state.selectedId;
        // Prefer the live embedded cover; its mtime-based version busts
        // the image cache right after every cover edit.
        final thumb = api != null && item.hasCover
            ? api.studioCoverUrl(item.id, cacheBust: item.coverVersion)
            : item.thumbnail;
        return HoverScale(
          scale: 1.005,
          child: Card(
            margin: const EdgeInsets.only(bottom: 8),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
              side: BorderSide(
                  color:
                      selected ? MidasColors.gold : MidasColors.border),
            ),
            child: InkWell(
              borderRadius: BorderRadius.circular(12),
              onTap: () =>
                  ref.read(studioProvider.notifier).select(item.id),
              child: Padding(
                padding: const EdgeInsets.all(10),
                child: Row(children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(6),
                    child: SizedBox(
                      width: 64,
                      height: 36,
                      child: thumb != null
                          ? Image.network(thumb,
                              fit: BoxFit.cover,
                              gaplessPlayback: true,
                              errorBuilder: (_, __, ___) => Container(
                                  color: MidasColors.raised,
                                  child: const Icon(
                                      Icons.audiotrack_rounded,
                                      size: 18,
                                      color: MidasColors.goldDeep)))
                          : Container(
                              color: MidasColors.raised,
                              child: const Icon(Icons.audiotrack_rounded,
                                  size: 18,
                                  color: MidasColors.goldDeep)),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(item.title,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: MidasTheme.ui(12.5, weight: 600)),
                          const SizedBox(height: 4),
                          Row(children: [
                            PlatformBadge(
                                platform: item.platform, compact: true),
                            const SizedBox(width: 6),
                            Flexible(
                              child: Text(
                                  '${item.container.toUpperCase()}'
                                  '${item.duration != null ? ' • ${_clockString(item.duration!.round())}' : ''}',
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: MidasTheme.ui(10.5,
                                      color: MidasColors.textDim)),
                            ),
                          ]),
                        ]),
                  ),
                ]),
              ),
            ),
          ),
        );
      },
    );
  }
}

class _EditorPane extends StatelessWidget {
  final StudioItem item;
  const _EditorPane({super.key, required this.item});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: DefaultTabController(
        length: 5,
        child: Column(children: [
          TabBar(
            labelColor: MidasColors.gold,
            unselectedLabelColor: MidasColors.textDim,
            indicatorColor: MidasColors.gold,
            dividerColor: MidasColors.border,
            labelStyle: MidasTheme.ui(13, weight: 700),
            tabs: [
              Tab(text: tr('studio.tab.cover')),
              Tab(text: tr('studio.tab.convert')),
              Tab(text: tr('studio.tab.subtitles')),
              Tab(text: tr('studio.tab.trim')),
              Tab(text: tr('studio.tab.crop')),
            ],
          ),
          Expanded(
            child: TabBarView(children: [
              _CoverTab(item: item),
              _ConvertTab(item: item),
              _SubtitlesTab(item: item),
              _TrimTab(item: item),
              _CropTab(item: item),
            ]),
          ),
        ]),
      ),
    );
  }
}

/// Gold progress bar shown while a studio job runs on [itemId].
class _JobProgress extends ConsumerWidget {
  final String itemId;
  const _JobProgress({required this.itemId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final studio = ref.watch(studioProvider);
    if (!studio.isBusy(itemId)) return const SizedBox.shrink();
    final percent = studio.progress[itemId];
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const SizedBox(height: 14),
      GoldProgressBar(percent: percent ?? 0, active: true),
      const SizedBox(height: 6),
      Text(
          percent != null
              ? '${percent.toStringAsFixed(0)}%'
              : tr('studio.working'),
          style:
              MidasTheme.ui(11.5, color: MidasColors.gold, weight: 600)),
    ]);
  }
}

// --------------------------------------------------------------- cover tab

class _CoverTab extends ConsumerStatefulWidget {
  final StudioItem item;
  const _CoverTab({required this.item});

  @override
  ConsumerState<_CoverTab> createState() => _CoverTabState();
}

class _CoverTabState extends ConsumerState<_CoverTab> {
  static const double _viewport = 280;

  final _tc = TransformationController();
  int _quarterTurns = 0; // clockwise 90° steps
  int? _outSize; // optional export size in px (longest edge)
  int _coverBust = 0; // bumped after Apply to bypass the image cache
  Uint8List? _replacementBytes;
  String? _replacementB64;
  Size? _imageSize; // natural pixels of the edited image
  String? _resolvedKey;
  bool _applying = false;

  @override
  void initState() {
    super.initState();
    // Keeps the zoom slider in sync with scroll/pinch gestures.
    _tc.addListener(_onTransform);
  }

  void _onTransform() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _tc.removeListener(_onTransform);
    _tc.dispose();
    super.dispose();
  }

  ImageProvider? _provider() {
    if (_replacementBytes != null) return MemoryImage(_replacementBytes!);
    final api = ref.read(apiProvider);
    if (api == null || !widget.item.hasCover) return null;
    return NetworkImage(api.studioCoverUrl(widget.item.id,
        cacheBust: widget.item.coverVersion + _coverBust));
  }

  void _resolveImageSize(ImageProvider provider, String key) {
    _resolvedKey = key;
    final stream = provider.resolve(const ImageConfiguration());
    late final ImageStreamListener listener;
    listener = ImageStreamListener((info, _) {
      if (mounted && _resolvedKey == key) {
        setState(() => _imageSize = Size(info.image.width.toDouble(),
            info.image.height.toDouble()));
      }
      stream.removeListener(listener);
    }, onError: (_, __) => stream.removeListener(listener));
    stream.addListener(listener);
  }

  /// Zooms around the viewport centre so the slider matches drag/scroll.
  void _setZoom(double zoom) {
    final current = _tc.value.getMaxScaleOnAxis();
    if (current <= 0) return;
    final factor = zoom / current;
    const c = _viewport / 2;
    final t = _tc.value.getTranslation();
    final low = -(zoom - 1) * _viewport;
    _tc.value = Matrix4.identity()
      ..translate((c - factor * (c - t.x)).clamp(low, 0.0),
          (c - factor * (c - t.y)).clamp(low, 0.0))
      ..scale(zoom);
  }

  /// Maps the visible square of the InteractiveViewer back to source-pixel
  /// crop coordinates (after the rotation the engine applies first).
  Map<String, dynamic>? _transform() {
    final t = <String, dynamic>{};
    final turns = _quarterTurns % 4;
    if (turns != 0) t['rotate'] = turns * 90;
    final img = _imageSize;
    if (img != null && img.width > 0 && img.height > 0) {
      final odd = turns.isOdd;
      final effW = odd ? img.height : img.width;
      final effH = odd ? img.width : img.height;
      // The editor shows the (rotated) image with BoxFit.cover.
      final coverScale =
          math.max(_viewport / effW, _viewport / effH);
      final offX = (_viewport - effW * coverScale) / 2;
      final offY = (_viewport - effH * coverScale) / 2;
      final m = _tc.value;
      final zoom = m.getMaxScaleOnAxis();
      final translation = m.getTranslation();
      final visX = -translation.x / zoom;
      final visY = -translation.y / zoom;
      final visSize = _viewport / zoom;
      var x = ((visX - offX) / coverScale).clamp(0.0, effW);
      var y = ((visY - offY) / coverScale).clamp(0.0, effH);
      final w = math.min(visSize / coverScale, effW - x);
      final h = math.min(visSize / coverScale, effH - y);
      final fullFrame = x < 1 &&
          y < 1 &&
          (w - effW).abs() < 1 &&
          (h - effH).abs() < 1;
      if (w > 4 && h > 4 && !fullFrame) {
        t['crop'] = {
          'x': x.round(),
          'y': y.round(),
          'width': w.round(),
          'height': h.round(),
        };
      }
      // Optional export resize: aspect preserved, never upscaled.
      final size = _outSize;
      if (size != null) {
        final crop = t['crop'] as Map<String, dynamic>?;
        final srcW = (crop?['width'] as int?)?.toDouble() ?? effW;
        final srcH = (crop?['height'] as int?)?.toDouble() ?? effH;
        final scale = size / math.max(srcW, srcH);
        if (scale < 1) {
          t['width'] = math.max(1, (srcW * scale).round());
          t['height'] = math.max(1, (srcH * scale).round());
        }
      }
    }
    return t.isEmpty ? null : t;
  }

  Future<void> _pickImage() async {
    final result = await FilePicker.platform
        .pickFiles(type: FileType.image, withData: true);
    final bytes = result?.files.single.bytes;
    if (bytes == null || !mounted) return;
    setState(() {
      _replacementBytes = bytes;
      _replacementB64 = base64Encode(bytes);
      _imageSize = null;
      _resolvedKey = null;
      _quarterTurns = 0;
      _tc.value = Matrix4.identity();
    });
  }

  void _reset() => setState(() {
        _replacementBytes = null;
        _replacementB64 = null;
        _imageSize = null;
        _resolvedKey = null;
        _quarterTurns = 0;
        _outSize = null;
        _tc.value = Matrix4.identity();
      });

  Future<void> _apply() async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    setState(() => _applying = true);
    final error = await api.studioSetCover(widget.item.id,
        imageBase64: _replacementB64, transform: _transform());
    if (!mounted) return;
    setState(() {
      _applying = false;
      if (error == null) {
        _coverBust++;
        _replacementBytes = null;
        _replacementB64 = null;
        _imageSize = null;
        _resolvedKey = null;
        _quarterTurns = 0;
        _outSize = null;
        _tc.value = Matrix4.identity();
      }
    });
    _showStudioResult(context, error);
    if (error == null) ref.read(studioProvider.notifier).refresh();
  }

  @override
  Widget build(BuildContext context) {
    final busy =
        ref.watch(studioProvider).isBusy(widget.item.id) || _applying;
    final provider = _provider();
    final providerKey = _replacementBytes != null
        ? 'mem-${_replacementBytes.hashCode}-$_quarterTurns'
        : 'net-${widget.item.coverVersion}-$_coverBust';
    if (provider != null && providerKey != _resolvedKey) {
      _resolveImageSize(provider, providerKey);
    }

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
          width: _viewport,
          height: _viewport,
          decoration: BoxDecoration(
            color: MidasColors.bg,
            border: Border.all(color: MidasColors.border),
            borderRadius: BorderRadius.circular(10),
          ),
          clipBehavior: Clip.antiAlias,
          child: provider == null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Text(tr('studio.cover.none'),
                        textAlign: TextAlign.center,
                        style: MidasTheme.ui(12,
                            color: MidasColors.textDim)),
                  ),
                )
              : InteractiveViewer(
                  transformationController: _tc,
                  minScale: 1,
                  maxScale: 8,
                  child: SizedBox(
                    width: _viewport,
                    height: _viewport,
                    child: RotatedBox(
                      quarterTurns: _quarterTurns % 4,
                      child: Image(
                        image: provider,
                        fit: BoxFit.cover,
                        gaplessPlayback: true,
                        errorBuilder: (_, __, ___) => Container(
                            color: MidasColors.raised,
                            child: const Icon(
                                Icons.image_not_supported_rounded,
                                color: MidasColors.goldDeep)),
                      ),
                    ),
                  ),
                ),
        ),
        const SizedBox(width: 20),
        Expanded(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(tr('studio.cover.hint'),
                    style:
                        MidasTheme.ui(12, color: MidasColors.textDim)),
                const SizedBox(height: 12),
                // Explicit crop controls: a zoom slider (drag the preview
                // to reposition) plus an optional export size.
                Row(children: [
                  const Icon(Icons.zoom_in_rounded,
                      size: 16, color: MidasColors.textDim),
                  const SizedBox(width: 4),
                  Text(tr('studio.cover.zoom'),
                      style: MidasTheme.ui(11.5,
                          color: MidasColors.textDim, weight: 600)),
                  Expanded(
                    child: SliderTheme(
                      data: SliderTheme.of(context).copyWith(
                        activeTrackColor: MidasColors.gold,
                        inactiveTrackColor: Colors.black,
                        thumbColor: MidasColors.goldBright,
                        overlayColor:
                            MidasColors.gold.withValues(alpha: 0.15),
                        trackHeight: 3,
                      ),
                      child: Slider(
                        value: _tc.value
                            .getMaxScaleOnAxis()
                            .clamp(1.0, 8.0)
                            .toDouble(),
                        min: 1,
                        max: 8,
                        onChanged: busy || provider == null
                            ? null
                            : (v) => setState(() => _setZoom(v)),
                      ),
                    ),
                  ),
                ]),
                Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                  Text(tr('studio.cover.size'),
                      style: MidasTheme.ui(11.5,
                          color: MidasColors.textDim, weight: 600)),
                  const SizedBox(width: 4),
                  for (final s in const [null, 1000, 640, 500])
                    ChoiceChip(
                      label: Text(
                          s == null
                              ? tr('studio.cover.size.original')
                              : '$s px',
                          style: MidasTheme.ui(11.5)),
                      selected: _outSize == s,
                      selectedColor:
                          MidasColors.gold.withValues(alpha: 0.18),
                      onSelected: busy
                          ? null
                          : (_) => setState(() => _outSize = s),
                    ),
                ]),
                const SizedBox(height: 12),
                Wrap(spacing: 8, runSpacing: 8, children: [
                  OutlinedButton.icon(
                    onPressed: busy
                        ? null
                        : () => setState(() =>
                            _quarterTurns = (_quarterTurns + 3) % 4),
                    icon: const Icon(Icons.rotate_90_degrees_ccw_rounded,
                        size: 16),
                    label: Text(tr('studio.cover.rotate_left')),
                  ),
                  OutlinedButton.icon(
                    onPressed: busy
                        ? null
                        : () => setState(() =>
                            _quarterTurns = (_quarterTurns + 1) % 4),
                    icon: const Icon(Icons.rotate_90_degrees_cw_rounded,
                        size: 16),
                    label: Text(tr('studio.cover.rotate_right')),
                  ),
                  OutlinedButton.icon(
                    onPressed: busy ? null : _pickImage,
                    icon: const Icon(Icons.image_rounded, size: 16),
                    label: Text(tr('studio.cover.replace')),
                  ),
                  TextButton(
                      onPressed: busy ? null : _reset,
                      child: Text(tr('studio.cover.reset'))),
                ]),
                const SizedBox(height: 16),
                ElevatedButton.icon(
                  onPressed: busy ||
                          (_replacementB64 == null &&
                              !widget.item.hasCover)
                      ? null
                      : _apply,
                  icon: const Icon(Icons.check_rounded, size: 18),
                  label: Text(tr('studio.cover.apply')),
                ),
                _JobProgress(itemId: widget.item.id),
              ]),
        ),
      ]),
    );
  }
}

// ------------------------------------------------------------- convert tab

class _ConvertTab extends ConsumerStatefulWidget {
  final StudioItem item;
  const _ConvertTab({required this.item});

  @override
  ConsumerState<_ConvertTab> createState() => _ConvertTabState();
}

class _ConvertTabState extends ConsumerState<_ConvertTab> {
  static const _audioTargets = ['mp3', 'm4a', 'flac', 'opus'];
  static const _videoTargets = ['mp4', 'mkv'];
  static const _bitrates = [128, 160, 192, 256, 320];

  String? _target;
  int? _bitrate;
  bool _keepOriginal = true;
  bool _applying = false;

  Widget _targetChips(List<String> choices, bool busy) {
    return Wrap(spacing: 8, runSpacing: 8, children: [
      for (final target in choices)
        ChoiceChip(
          label: Text(target.toUpperCase()),
          selected: _target == target,
          selectedColor: MidasColors.gold.withValues(alpha: 0.18),
          onSelected: busy
              ? null
              : (v) =>
                  setState(() => _target = v ? target : null),
        ),
    ]);
  }

  String _summary() {
    final item = widget.item;
    final parts = <String>[
      item.container.toUpperCase(),
      if (item.duration != null) _clockString(item.duration!.round()),
      if (item.width != null && item.height != null)
        '${item.width}×${item.height}',
      if (item.audioCodec != null)
        '${item.audioCodec}'
            '${item.audioBitrateKbps != null ? ' ${item.audioBitrateKbps} kbps' : ''}',
    ];
    return parts.join('  •  ');
  }

  Future<void> _apply() async {
    final api = ref.read(apiProvider);
    final target = _target;
    if (api == null || target == null) return;
    final settings = ref.read(settingsProvider);
    final wantsBitrate =
        _audioTargets.contains(target) && target != 'flac';
    setState(() => _applying = true);
    final error = await api.studioConvert(widget.item.id, target,
        bitrateKbps: wantsBitrate
            ? (_bitrate ?? settings?.audioBitrate ?? 192)
            : null,
        keepOriginal: _keepOriginal);
    if (!mounted) return;
    setState(() => _applying = false);
    _showStudioResult(context, error);
    if (error == null) {
      ref.read(studioProvider.notifier).refresh();
      ref.read(downloadsProvider.notifier).refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    final busy =
        ref.watch(studioProvider).isBusy(item.id) || _applying;
    final settings = ref.watch(settingsProvider);
    final audioChoices =
        _audioTargets.where((t) => t != item.container).toList();
    final videoChoices =
        _videoTargets.where((t) => t != item.container).toList();
    final selectedBitrate = _bitrate ?? settings?.audioBitrate ?? 192;
    final bitrateValue =
        _bitrates.contains(selectedBitrate) ? selectedBitrate : 192;
    final showBitrate = _target != null &&
        _audioTargets.contains(_target) &&
        _target != 'flac';

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(tr('studio.convert.current').toUpperCase(),
            style: MidasTheme.ui(11,
                color: MidasColors.textDim,
                weight: 700,
                letterSpacing: 1.2)),
        const SizedBox(height: 6),
        Text(_summary(), style: MidasTheme.ui(13)),
        const SizedBox(height: 18),
        Text(tr('studio.convert.target').toUpperCase(),
            style: MidasTheme.ui(11,
                color: MidasColors.textDim,
                weight: 700,
                letterSpacing: 1.2)),
        const SizedBox(height: 8),
        if (item.isAudio)
          _targetChips(audioChoices, busy)
        else ...[
          // Video files can be remuxed or have their sound extracted
          // (e.g. MP4 -> MP3); each path gets its own labelled group.
          Text(tr('studio.convert.group_video'),
              style: MidasTheme.ui(12, color: MidasColors.textDim)),
          const SizedBox(height: 6),
          _targetChips(videoChoices, busy),
          const SizedBox(height: 12),
          Text(tr('studio.convert.group_audio'),
              style: MidasTheme.ui(12, color: MidasColors.textDim)),
          const SizedBox(height: 6),
          _targetChips(audioChoices, busy),
        ],
        if (_target != null &&
            !item.isAudio &&
            _audioTargets.contains(_target)) ...[
          const SizedBox(height: 10),
          Text(tr('studio.convert.audio_note'),
              style: MidasTheme.ui(12, color: MidasColors.gold)),
        ],
        if (showBitrate) ...[
          const SizedBox(height: 14),
          Row(children: [
            Text('${tr('studio.convert.bitrate')}:',
                style:
                    MidasTheme.ui(13, color: MidasColors.textDim)),
            const SizedBox(width: 10),
            DropdownButton<int>(
              value: bitrateValue,
              dropdownColor: MidasColors.raised,
              style: MidasTheme.ui(13),
              items: [
                for (final b in _bitrates)
                  DropdownMenuItem(value: b, child: Text('$b kbps')),
              ],
              onChanged:
                  busy ? null : (v) => setState(() => _bitrate = v),
            ),
          ]),
        ],
        const SizedBox(height: 6),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          dense: true,
          title: Text(tr('studio.convert.keep_original'),
              style: MidasTheme.ui(13)),
          value: _keepOriginal,
          onChanged: busy
              ? null
              : (v) => setState(() => _keepOriginal = v),
        ),
        const SizedBox(height: 8),
        ElevatedButton.icon(
          onPressed: _target == null || busy ? null : _apply,
          icon: const Icon(Icons.bolt_rounded, size: 18),
          label: Text(tr('studio.convert.apply')),
        ),
        _JobProgress(itemId: item.id),
      ]),
    );
  }
}

// ----------------------------------------------------------- subtitles tab

class _SubtitlesTab extends ConsumerStatefulWidget {
  final StudioItem item;
  const _SubtitlesTab({required this.item});

  @override
  ConsumerState<_SubtitlesTab> createState() => _SubtitlesTabState();
}

class _SubtitlesTabState extends ConsumerState<_SubtitlesTab> {
  final _editorCtl = TextEditingController();
  final _fontCtl = TextEditingController(text: '24');
  int? _extractedIndex;
  bool _hasContent = false;
  bool _working = false;
  String _burnPosition = 'bottom';

  @override
  void dispose() {
    _editorCtl.dispose();
    _fontCtl.dispose();
    super.dispose();
  }

  Future<void> _extract(int index) async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    setState(() => _working = true);
    final result =
        await api.studioExtractSubtitle(widget.item.id, index);
    if (!mounted) return;
    setState(() {
      _working = false;
      final content = result['content'];
      if (content is String) {
        _editorCtl.text = content;
        _extractedIndex = index;
        _hasContent = true;
      }
    });
    final message = result['message'];
    if (message is String) _showStudioResult(context, message);
  }

  Future<void> _remove(int index) async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    setState(() => _working = true);
    final error =
        await api.studioDeleteSubtitle(widget.item.id, index);
    if (!mounted) return;
    setState(() {
      _working = false;
      if (error == null && _extractedIndex == index) {
        _extractedIndex = null;
      }
    });
    _showStudioResult(context, error);
    if (error == null) ref.read(studioProvider.notifier).refresh();
  }

  Future<void> _save() async {
    final api = ref.read(apiProvider);
    if (api == null || _editorCtl.text.trim().isEmpty) return;
    setState(() => _working = true);
    final error = await api.studioSaveSubtitle(
        widget.item.id, _editorCtl.text,
        replaceIndex: _extractedIndex);
    if (!mounted) return;
    setState(() => _working = false);
    _showStudioResult(context, error);
    if (error == null) ref.read(studioProvider.notifier).refresh();
  }

  Future<void> _burn() async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    final tracks = widget.item.subtitles;
    final content = _hasContent ? _editorCtl.text : null;
    final streamIndex = content != null
        ? null
        : (tracks.isNotEmpty ? tracks.first.index : null);
    if (content == null && streamIndex == null) return;
    setState(() => _working = true);
    final error = await api.studioBurnSubtitle(widget.item.id,
        streamIndex: streamIndex,
        content: content,
        position: _burnPosition,
        fontSize: int.tryParse(_fontCtl.text.trim()) ?? 24);
    if (!mounted) return;
    setState(() => _working = false);
    _showStudioResult(context, error);
    if (error == null) ref.read(studioProvider.notifier).refresh();
  }

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    final busy = ref.watch(studioProvider).isBusy(item.id) || _working;
    final canBurn = !item.isAudio &&
        (item.subtitles.isNotEmpty || _hasContent);

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (item.subtitles.isEmpty)
          Text(tr('studio.subs.none'),
              style: MidasTheme.ui(13, color: MidasColors.textDim))
        else
          for (final track in item.subtitles)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(children: [
                const Icon(Icons.subtitles_rounded,
                    size: 16, color: MidasColors.gold),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                      '#${track.index}  •  ${track.language ?? 'und'}'
                      '${track.codec != null ? '  •  ${track.codec}' : ''}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: MidasTheme.ui(13)),
                ),
                TextButton(
                    onPressed:
                        busy ? null : () => _extract(track.index),
                    child: Text(tr('studio.subs.extract'))),
                TextButton(
                    onPressed:
                        busy ? null : () => _remove(track.index),
                    child: Text(tr('studio.subs.remove'),
                        style: MidasTheme.ui(13,
                            color: MidasColors.red))),
              ]),
            ),
        if (_hasContent) ...[
          const SizedBox(height: 14),
          Text(tr('studio.subs.editor_hint'),
              style: MidasTheme.ui(12, color: MidasColors.textDim)),
          const SizedBox(height: 8),
          TextField(
            controller: _editorCtl,
            maxLines: 10,
            style: MidasTheme.ui(12).copyWith(
                fontFamily: 'Consolas',
                fontFamilyFallback: const ['monospace']),
          ),
          const SizedBox(height: 10),
          ElevatedButton.icon(
            onPressed: busy ? null : _save,
            icon: const Icon(Icons.save_rounded, size: 18),
            label: Text(tr('studio.subs.save')),
          ),
        ],
        if (canBurn) ...[
          const SizedBox(height: 18),
          const Divider(color: MidasColors.border, height: 1),
          const SizedBox(height: 14),
          Text(tr('studio.subs.burn').toUpperCase(),
              style: MidasTheme.ui(11,
                  color: MidasColors.textDim,
                  weight: 700,
                  letterSpacing: 1.2)),
          const SizedBox(height: 6),
          Text(tr('studio.subs.burn_warning'),
              style: MidasTheme.ui(12, color: MidasColors.textDim)),
          const SizedBox(height: 10),
          Row(children: [
            Text('${tr('studio.subs.position')}:',
                style:
                    MidasTheme.ui(13, color: MidasColors.textDim)),
            const SizedBox(width: 10),
            DropdownButton<String>(
              value: _burnPosition,
              dropdownColor: MidasColors.raised,
              style: MidasTheme.ui(13),
              items: [
                for (final p in ['bottom', 'middle', 'top'])
                  DropdownMenuItem(
                      value: p,
                      child: Text(tr('studio.subs.position.$p'))),
              ],
              onChanged: busy
                  ? null
                  : (v) => setState(
                      () => _burnPosition = v ?? 'bottom'),
            ),
            const SizedBox(width: 16),
            Text('${tr('studio.subs.font_size')}:',
                style:
                    MidasTheme.ui(13, color: MidasColors.textDim)),
            const SizedBox(width: 8),
            SizedBox(
              width: 56,
              child: TextField(
                controller: _fontCtl,
                textAlign: TextAlign.center,
                style: MidasTheme.ui(12),
                decoration: const InputDecoration(
                  isDense: true,
                  contentPadding: EdgeInsets.symmetric(
                      horizontal: 6, vertical: 8),
                ),
              ),
            ),
            const SizedBox(width: 16),
            OutlinedButton.icon(
              onPressed: busy ? null : _burn,
              icon:
                  const Icon(Icons.local_fire_department_rounded,
                      size: 16),
              label: Text(tr('studio.subs.burn')),
            ),
          ]),
        ],
        _JobProgress(itemId: item.id),
      ]),
    );
  }
}

// ---------------------------------------------------------------- trim tab

class _TrimTab extends ConsumerStatefulWidget {
  final StudioItem item;
  const _TrimTab({required this.item});

  @override
  ConsumerState<_TrimTab> createState() => _TrimTabState();
}

class _TrimTabState extends ConsumerState<_TrimTab> {
  late List<RangeValues> _segments;
  int _selected = 0;
  String _mode = 'keep';
  bool _precise = false;
  bool _keepOriginal = true;
  bool _applying = false;
  late final TextEditingController _startCtl;
  late final TextEditingController _endCtl;

  double get _duration => widget.item.duration ?? 0;

  @override
  void initState() {
    super.initState();
    _segments = [RangeValues(0, _duration)];
    _startCtl = TextEditingController(text: _clockString(0));
    _endCtl =
        TextEditingController(text: _clockString(_duration.round()));
  }

  @override
  void dispose() {
    _startCtl.dispose();
    _endCtl.dispose();
    super.dispose();
  }

  void _syncFields() {
    final segment = _segments[_selected];
    _startCtl.text = _clockString(segment.start.round());
    _endCtl.text = _clockString(segment.end.round());
  }

  void _applyFields() {
    final segment = _segments[_selected];
    final parsedStart =
        _parseClock(_startCtl.text) ?? segment.start.round();
    final parsedEnd = _parseClock(_endCtl.text) ?? segment.end.round();
    setState(() {
      var start =
          parsedStart.toDouble().clamp(0.0, _duration).toDouble();
      var end = parsedEnd.toDouble().clamp(0.0, _duration).toDouble();
      if (end <= start) end = math.min(start + 1, _duration);
      if (end <= start) start = math.max(end - 1, 0);
      _segments[_selected] = RangeValues(start, end);
      _syncFields();
    });
  }

  void _selectSegment(int i) => setState(() {
        _selected = i;
        _syncFields();
      });

  void _addSegment() => setState(() {
        final last = _segments.last;
        final start = math.min(last.end, _duration - 1);
        _segments.add(RangeValues(math.max(start, 0), _duration));
        _selected = _segments.length - 1;
        _syncFields();
      });

  void _removeSegment(int i) => setState(() {
        if (_segments.length <= 1) return;
        _segments.removeAt(i);
        if (_selected >= _segments.length) {
          _selected = _segments.length - 1;
        }
        _syncFields();
      });

  Future<void> _apply() async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    final segments = [..._segments]
      ..sort((a, b) => a.start.compareTo(b.start));
    for (var i = 0; i < segments.length; i++) {
      final s = segments[i];
      final overlaps = i > 0 && s.start < segments[i - 1].end;
      if (s.end - s.start < 0.5 || overlaps) {
        _showStudioResult(context, tr('studio.trim.invalid'));
        return;
      }
    }
    setState(() => _applying = true);
    final error = await api.studioTrim(
        widget.item.id,
        [
          for (final s in segments)
            {'start_sec': s.start, 'end_sec': s.end},
        ],
        mode: _mode,
        keepOriginal: _keepOriginal,
        precise: _precise);
    if (!mounted) return;
    setState(() => _applying = false);
    _showStudioResult(context, error);
    if (error == null) {
      ref.read(studioProvider.notifier).refresh();
      ref.read(downloadsProvider.notifier).refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    final busy =
        ref.watch(studioProvider).isBusy(item.id) || _applying;

    if (_duration <= 0) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Text(tr('studio.trim.no_duration'),
              textAlign: TextAlign.center,
              style: MidasTheme.ui(13, color: MidasColors.textDim)),
        ),
      );
    }

    final segment = _segments[_selected];
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (item.thumbnail != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: Image.network(item.thumbnail!,
                  height: 80,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) =>
                      const SizedBox.shrink()),
            ),
          ),
        Text(_clockString(_duration.round()),
            style: MidasTheme.ui(12, color: MidasColors.textDim)),
        const SizedBox(height: 4),
        Row(children: [
          SizedBox(
            width: 66,
            child: TextField(
              controller: _startCtl,
              textAlign: TextAlign.center,
              style: MidasTheme.ui(12),
              decoration: const InputDecoration(
                isDense: true,
                contentPadding:
                    EdgeInsets.symmetric(horizontal: 6, vertical: 8),
              ),
              onSubmitted: (_) => _applyFields(),
              onEditingComplete: _applyFields,
            ),
          ),
          Expanded(
            child: SliderTheme(
              data: SliderTheme.of(context).copyWith(
                activeTrackColor: MidasColors.gold,
                inactiveTrackColor: MidasColors.border,
                thumbColor: MidasColors.goldBright,
                overlayColor:
                    MidasColors.gold.withValues(alpha: 0.15),
                trackHeight: 3,
                rangeThumbShape: const RoundRangeSliderThumbShape(
                    enabledThumbRadius: 7),
              ),
              child: RangeSlider(
                values: segment,
                min: 0,
                max: _duration,
                onChanged: busy
                    ? null
                    : (v) => setState(() {
                          _segments[_selected] = v;
                          _syncFields();
                        }),
              ),
            ),
          ),
          SizedBox(
            width: 66,
            child: TextField(
              controller: _endCtl,
              textAlign: TextAlign.center,
              style: MidasTheme.ui(12),
              decoration: const InputDecoration(
                isDense: true,
                contentPadding:
                    EdgeInsets.symmetric(horizontal: 6, vertical: 8),
              ),
              onSubmitted: (_) => _applyFields(),
              onEditingComplete: _applyFields,
            ),
          ),
        ]),
        const SizedBox(height: 8),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (var i = 0; i < _segments.length; i++)
            InputChip(
              label: Text(
                  '${_clockString(_segments[i].start.round())} – '
                  '${_clockString(_segments[i].end.round())}',
                  style: MidasTheme.ui(11.5,
                      color: i == _selected
                          ? MidasColors.gold
                          : MidasColors.text)),
              selected: i == _selected,
              selectedColor:
                  MidasColors.gold.withValues(alpha: 0.12),
              onPressed: busy ? null : () => _selectSegment(i),
              onDeleted: busy || _segments.length <= 1
                  ? null
                  : () => _removeSegment(i),
            ),
          TextButton.icon(
            onPressed: busy ? null : _addSegment,
            icon: const Icon(Icons.add_rounded, size: 16),
            label: Text(tr('studio.trim.add_cut')),
          ),
        ]),
        const SizedBox(height: 14),
        SegmentedButton<String>(
          segments: [
            ButtonSegment(
                value: 'keep',
                label: Text(tr('studio.trim.mode.keep'))),
            ButtonSegment(
                value: 'remove',
                label: Text(tr('studio.trim.mode.remove'))),
          ],
          selected: {_mode},
          onSelectionChanged: busy
              ? null
              : (s) => setState(() => _mode = s.first),
        ),
        const SizedBox(height: 6),
        Text(
            _mode == 'keep'
                ? tr('studio.trim.mode.keep_hint')
                : tr('studio.trim.mode.remove_hint'),
            style: MidasTheme.ui(12, color: MidasColors.textDim)),
        const SizedBox(height: 6),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          dense: true,
          title: Text(tr('studio.trim.precise'),
              style: MidasTheme.ui(13)),
          value: _precise,
          onChanged:
              busy ? null : (v) => setState(() => _precise = v),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          dense: true,
          title: Text(tr('studio.trim.keep_original'),
              style: MidasTheme.ui(13)),
          value: _keepOriginal,
          onChanged: busy
              ? null
              : (v) => setState(() => _keepOriginal = v),
        ),
        const SizedBox(height: 8),
        ElevatedButton.icon(
          onPressed: busy ? null : _apply,
          icon: const Icon(Icons.content_cut_rounded, size: 18),
          label: Text(tr('studio.trim.apply')),
        ),
        _JobProgress(itemId: item.id),
      ]),
    );
  }
}

// ---------------------------------------------------------------- crop tab

/// "Trim the video size": keep only the selected part of the picture.
/// The selection is sent to the engine as fractions of the frame, so it
/// works for any resolution without the UI knowing exact pixel sizes.
class _CropTab extends ConsumerStatefulWidget {
  final StudioItem item;
  const _CropTab({required this.item});

  @override
  ConsumerState<_CropTab> createState() => _CropTabState();
}

class _CropTabState extends ConsumerState<_CropTab> {
  RangeValues _h = const RangeValues(0, 1); // left..right fractions
  RangeValues _v = const RangeValues(0, 1); // top..bottom fractions
  double _frameAt = 0;       // scrub slider position (seconds)
  double _frameLoaded = 0;   // frame currently shown (only set on drag end)
  bool _keepOriginal = true;
  bool _applying = false;

  double get _duration => widget.item.duration ?? 0;

  @override
  void initState() {
    super.initState();
    // A quarter in: usually past intros/black frames, cheap to seek to.
    _frameAt = _duration > 0 ? _duration * 0.25 : 0;
    _frameLoaded = _frameAt;
  }

  bool get _isFullFrame =>
      _h.start <= 0.001 &&
      _v.start <= 0.001 &&
      _h.end >= 0.999 &&
      _v.end >= 0.999;

  bool get _tooSmall =>
      (_h.end - _h.start) < 0.05 || (_v.end - _v.start) < 0.05;

  Future<void> _apply() async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    setState(() => _applying = true);
    final error = await api.studioCrop(widget.item.id,
        left: _h.start,
        top: _v.start,
        right: _h.end,
        bottom: _v.end,
        keepOriginal: _keepOriginal);
    if (!mounted) return;
    setState(() => _applying = false);
    _showStudioResult(context, error);
    if (error == null) {
      setState(() {
        _h = const RangeValues(0, 1);
        _v = const RangeValues(0, 1);
      });
      ref.read(studioProvider.notifier).refresh();
      ref.read(downloadsProvider.notifier).refresh();
    }
  }

  SliderThemeData _sliderTheme(BuildContext context) =>
      SliderTheme.of(context).copyWith(
        activeTrackColor: MidasColors.gold,
        inactiveTrackColor: MidasColors.border,
        thumbColor: MidasColors.goldBright,
        overlayColor: MidasColors.gold.withValues(alpha: 0.15),
        trackHeight: 3,
        rangeThumbShape:
            const RoundRangeSliderThumbShape(enabledThumbRadius: 7),
      );

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    final api = ref.read(apiProvider);
    final busy = ref.watch(studioProvider).isBusy(item.id) || _applying;

    if (item.isAudio || item.width == null || item.height == null ||
        item.width! <= 0 || item.height! <= 0 || api == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Text(tr('studio.crop.video_only'),
              textAlign: TextAlign.center,
              style: MidasTheme.ui(13, color: MidasColors.textDim)),
        ),
      );
    }

    final srcW = item.width!.toDouble();
    final srcH = item.height!.toDouble();
    final aspect = srcW / srcH;
    final outW = ((_h.end - _h.start) * srcW).round();
    final outH = ((_v.end - _v.start) * srcH).round();
    final frameUrl = api.studioFrameUrl(item.id,
        atSec: _frameLoaded, cacheBust: item.coverVersion);

    Widget scrim() =>
        Container(color: Colors.black.withValues(alpha: 0.55));

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(tr('studio.crop.hint'),
            style: MidasTheme.ui(12, color: MidasColors.textDim)),
        const SizedBox(height: 14),
        LayoutBuilder(builder: (context, constraints) {
          // Fit the preview into the pane; vertical videos are capped in
          // height so the controls below stay visible.
          var imgW = math.min(constraints.maxWidth - 40, 440.0);
          var imgH = imgW / aspect;
          if (imgH > 280) {
            imgH = 280;
            imgW = imgH * aspect;
          }
          final selLeft = _h.start * imgW;
          final selTop = _v.start * imgH;
          final selW = (_h.end - _h.start) * imgW;
          final selH = (_v.end - _v.start) * imgH;
          return Column(crossAxisAlignment: CrossAxisAlignment.start,
              children: [
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: SizedBox(
                  width: imgW,
                  height: imgH,
                  child: Stack(children: [
                    Positioned.fill(
                      child: Image.network(frameUrl,
                          fit: BoxFit.fill,
                          gaplessPlayback: true,
                          errorBuilder: (_, __, ___) => Container(
                              color: MidasColors.raised,
                              child: const Icon(Icons.movie_rounded,
                                  color: MidasColors.goldDeep))),
                    ),
                    // Dark scrims over everything outside the selection.
                    Positioned(left: 0, top: 0, bottom: 0,
                        width: selLeft, child: scrim()),
                    Positioned(right: 0, top: 0, bottom: 0,
                        width: imgW - selLeft - selW, child: scrim()),
                    Positioned(left: selLeft, top: 0, width: selW,
                        height: selTop, child: scrim()),
                    Positioned(left: selLeft, bottom: 0, width: selW,
                        height: imgH - selTop - selH, child: scrim()),
                    Positioned(
                      left: selLeft,
                      top: selTop,
                      width: selW,
                      height: selH,
                      child: Container(
                        decoration: BoxDecoration(
                          border: Border.all(
                              color: MidasColors.gold, width: 1.5),
                        ),
                      ),
                    ),
                  ]),
                ),
              ),
              // Vertical range = which rows of the picture survive.
              SizedBox(
                width: 36,
                height: imgH,
                child: RotatedBox(
                  quarterTurns: 1,
                  child: SliderTheme(
                    data: _sliderTheme(context),
                    child: RangeSlider(
                      values: _v,
                      min: 0,
                      max: 1,
                      onChanged: busy
                          ? null
                          : (v) => setState(() => _v = v),
                    ),
                  ),
                ),
              ),
            ]),
            // Horizontal range = which columns of the picture survive.
            SizedBox(
              width: imgW,
              child: SliderTheme(
                data: _sliderTheme(context),
                child: RangeSlider(
                  values: _h,
                  min: 0,
                  max: 1,
                  onChanged:
                      busy ? null : (v) => setState(() => _h = v),
                ),
              ),
            ),
          ]);
        }),
        const SizedBox(height: 4),
        Text(
            tr('studio.crop.output', args: {
              'size': '$outW × $outH px'
            }),
            style: MidasTheme.ui(12, color: MidasColors.textDim)),
        if (_duration > 0) ...[
          const SizedBox(height: 12),
          Row(children: [
            Text(tr('studio.crop.frame'),
                style: MidasTheme.ui(12, color: MidasColors.textDim)),
            const SizedBox(width: 8),
            Expanded(
              child: SliderTheme(
                data: _sliderTheme(context),
                child: Slider(
                  value: _frameAt.clamp(0, _duration),
                  min: 0,
                  max: _duration,
                  onChanged: busy
                      ? null
                      : (v) => setState(() => _frameAt = v),
                  onChangeEnd: (v) =>
                      setState(() => _frameLoaded = v),
                ),
              ),
            ),
            Text(_clockString(_frameAt.round()),
                style: MidasTheme.ui(12, color: MidasColors.textDim)),
          ]),
        ],
        const SizedBox(height: 6),
        // Same toggle style as Convert/Trim: the app-wide switchTheme
        // gives a dark thumb on the gold track, so the knob stays visible
        // (a gold-on-gold override made it invisible).
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          dense: true,
          title: Text(tr('studio.crop.keep_original'),
              style: MidasTheme.ui(13)),
          value: _keepOriginal,
          onChanged: busy
              ? null
              : (v) => setState(() => _keepOriginal = v),
        ),
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: busy || _isFullFrame || _tooSmall ? null : _apply,
          icon: const Icon(Icons.crop_rounded, size: 18),
          label: Text(tr('studio.crop.apply')),
        ),
        _JobProgress(itemId: item.id),
      ]),
    );
  }
}
