import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/i18n/strings.dart';
import '../../core/theme/midas_theme.dart';
import '../../core/utils/format.dart';
import '../../models/models.dart';
import '../../providers/app_providers.dart';
import '../../widgets/widgets.dart';

enum _HomePhase { idle, analyzing, preview, error }

/// Per-download choices made on the preview card (TASK 6 + TASK 9).
class _DownloadOptions {
  final Map<String, dynamic>? overrides;
  final Map<String, int>? section;
  const _DownloadOptions({this.overrides, this.section});
}

class HomeScreen extends ConsumerStatefulWidget {
  final VoidCallback? onDownloadQueued;
  const HomeScreen({super.key, this.onDownloadQueued});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  final _controller = TextEditingController();
  _HomePhase _phase = _HomePhase.idle;
  AnalysisResult? _preview;
  String _errorMessage = '';
  Timer? _clipboardTimer;
  String? _lastClipboard;
  // Manual typing support (BUG): analyze edited links automatically and
  // clear the preview card when the bar is emptied.
  Timer? _typeDebounce;
  String _lastSubmitted = '';

  static const _hosts = [
    'youtube.com', 'youtu.be', 'open.spotify.com', 'soundcloud.com',
    'instagram.com', 'tiktok.com', 'reddit.com', 'redd.it',
  ];

  @override
  void initState() {
    super.initState();
    // 500ms (was 1200ms): copied links reach the card noticeably sooner.
    _clipboardTimer = Timer.periodic(
        const Duration(milliseconds: 500), (_) => _pollClipboard());
    _controller.addListener(_onLinkTextChanged);
  }

  @override
  void dispose() {
    _clipboardTimer?.cancel();
    _typeDebounce?.cancel();
    _controller.removeListener(_onLinkTextChanged);
    _controller.dispose();
    super.dispose();
  }

  /// Reacts to every edit in the link bar (typing, paste, delete):
  ///   * emptied bar -> the preview/error card disappears immediately,
  ///   * an edited link -> re-analyzed automatically after a short pause,
  ///   * non-link text -> any stale card is dismissed.
  void _onLinkTextChanged() {
    final text = _controller.text.trim();
    _typeDebounce?.cancel();
    if (text.isEmpty) {
      _lastSubmitted = '';
      if (_phase != _HomePhase.idle) {
        setState(() {
          _phase = _HomePhase.idle;
          _preview = null;
        });
      }
      return;
    }
    _typeDebounce = Timer(const Duration(milliseconds: 600), () {
      if (!mounted) return;
      final current = _controller.text.trim();
      if (current != text) return; // still typing
      final alreadyShown = current == _lastSubmitted &&
          (_phase == _HomePhase.analyzing || _phase == _HomePhase.preview);
      if (alreadyShown) return;
      if (current.startsWith('http')) {
        _analyze();
      } else if (_phase != _HomePhase.idle) {
        // The bar no longer holds a link; drop the stale card.
        setState(() {
          _phase = _HomePhase.idle;
          _preview = null;
        });
      }
    });
  }

  Future<void> _pollClipboard() async {
    final settings = ref.read(settingsProvider);
    if (settings == null || !settings.clipboardWatch) return;
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim();
    if (text == null || text == _lastClipboard) return;
    _lastClipboard = text;
    final isSupported = text.startsWith('http') &&
        _hosts.any((h) => text.contains(h));
    if (isSupported && text != _controller.text && mounted) {
      _controller.text = text;
      _analyze();
    }
  }

  Future<void> _analyze() async {
    final url = _controller.text.trim();
    final api = ref.read(apiProvider);
    if (url.isEmpty || api == null) return;
    _typeDebounce?.cancel();
    _lastSubmitted = url;
    setState(() {
      _phase = _HomePhase.analyzing;
      _preview = null;
    });
    try {
      final result = await api.analyze(url);
      if (!mounted) return;
      // The user kept editing while this analysis ran; the newer text wins
      // (its own debounce/analyze cycle is already underway).
      if (_controller.text.trim() != url) return;
      setState(() {
        if (result.isError) {
          _phase = _HomePhase.error;
          _errorMessage = result.message ?? 'This link could not be read.';
        } else {
          _phase = _HomePhase.preview;
          _preview = result;
        }
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _phase = _HomePhase.error;
        _errorMessage = 'Something went wrong while reading the link.';
      });
    }
  }

  Future<void> _download(_DownloadOptions options) async {
    final preview = _preview;
    final api = ref.read(apiProvider);
    if (preview == null || api == null) return;

    var mode = 'single';
    String? items;
    var selectedIndices = const <int>[];
    if (preview.platform == 'spotify' && preview.kind == 'playlist') {
      // Spotify albums/playlists get a track picker of their own (BUG 5).
      final choice = await showDialog<_SpotifyChoice>(
        context: context,
        builder: (context) => _SpotifyTrackPickerDialog(preview: preview),
      );
      if (choice == null || !mounted) return;
      mode = 'playlist';
      selectedIndices = choice.selectedIndices;
    } else if (preview.isPlaylist) {
      final choice = await showDialog<_PlaylistChoice>(
        context: context,
        builder: (context) => _PlaylistPickerDialog(preview: preview),
      );
      if (choice == null || !mounted) return;
      mode = choice.mode;
      items = choice.items;
    }

    final error = await api.createDownload(preview.url, mode, preview,
        overrides: options.overrides,
        items: mode == 'playlist' ? items : null,
        selectedIndices: selectedIndices,
        // The engine only accepts a clip range for single videos.
        section: mode == 'single' ? options.section : null);
    if (!mounted) return;
    if (error == null) {
      setState(() {
        _phase = _HomePhase.idle;
        _preview = null;
        _controller.clear();
      });
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Row(children: [
          const Icon(Icons.auto_awesome_rounded,
              color: MidasColors.gold, size: 18),
          const SizedBox(width: 10),
          Text('Added to the queue — turning it to gold…',
              style: MidasTheme.ui(14)),
        ]),
      ));
      widget.onDownloadQueued?.call();
    } else {
      setState(() {
        _phase = _HomePhase.error;
        _errorMessage = error;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Stack(children: [
      // Golden-marble backdrop covering the whole page, strongest at the
      // top and fading into the black canvas toward the bottom.
      Positioned.fill(
        child: ShaderMask(
          shaderCallback: (bounds) => const LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Colors.white, Color(0x66FFFFFF), Color(0x22FFFFFF)],
            stops: [0.0, 0.5, 1.0],
          ).createShader(bounds),
          blendMode: BlendMode.dstIn,
          child: Opacity(
            opacity: 0.5,
            child: Image.asset('assets/images/header_bg.jpg',
                width: double.infinity,
                height: double.infinity,
                fit: BoxFit.cover),
          ),
        ),
      ),
      Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 40),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const MidasWordmark(size: 62)
                    .animate()
                    .fadeIn(duration: 600.ms)
                    .slideY(begin: -0.12, curve: Curves.easeOut),
                const SizedBox(height: 10),
                Text(tr('app.tagline'),
                        style:
                            MidasTheme.ui(15, color: MidasColors.textDim))
                    .animate()
                    .fadeIn(delay: 200.ms),
                const SizedBox(height: 44),
                _buildLinkBox()
                    .animate()
                    .fadeIn(delay: 300.ms)
                    .slideY(begin: 0.1, curve: Curves.easeOut),
                const SizedBox(height: 30),
                SizedBox(height: 240, child: _buildResultArea()),
              ],
            ),
          ),
        ),
      ),
      // Developer log panel (Settings > Developers > Show logs).
      if (ref.watch(settingsProvider)?.showLogs ?? false)
        const Positioned(left: 0, right: 0, bottom: 0, child: _LogPanel()),
    ]);
  }

  Widget _buildLinkBox() {
    return TextField(
      controller: _controller,
      onSubmitted: (_) => _analyze(),
      style: MidasTheme.ui(15),
      decoration: InputDecoration(
        hintText: tr('home.paste_hint'),
        prefixIcon:
            const Icon(Icons.link_rounded, color: MidasColors.textDim),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 18, vertical: 20),
        suffixIcon: Padding(
          padding: const EdgeInsets.only(right: 8),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            IconButton(
              tooltip: 'Paste',
              icon: const Icon(Icons.content_paste_rounded,
                  size: 19, color: MidasColors.textDim),
              onPressed: () async {
                final data = await Clipboard.getData(Clipboard.kTextPlain);
                if (data?.text != null) {
                  _controller.text = data!.text!.trim();
                  _analyze();
                }
              },
            ),
            const SizedBox(width: 4),
            ElevatedButton(
                onPressed: _analyze, child: Text(tr('home.analyze'))),
          ]),
        ),
      ),
    );
  }

  Widget _buildResultArea() {
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 300),
      child: switch (_phase) {
        _HomePhase.idle => const SizedBox(key: ValueKey('idle')),
        _HomePhase.analyzing => const Column(
            key: ValueKey('analyzing'),
            children: [SkeletonPreviewCard()],
          ),
        _HomePhase.error => Center(
            key: const ValueKey('error'),
            child: SingleChildScrollView(
              child: MidasErrorState(
                  title: tr('error.title'), message: _errorMessage),
            ),
          ),
        _HomePhase.preview => SingleChildScrollView(
            key: ValueKey('preview-${_preview!.url}'),
            child: _PreviewCard(
              preview: _preview!,
              onDownload: _download,
            ),
          ),
      },
    );
  }
}

class _PreviewCard extends ConsumerStatefulWidget {
  final AnalysisResult preview;
  final void Function(_DownloadOptions options) onDownload;
  const _PreviewCard({required this.preview, required this.onDownload});

  @override
  ConsumerState<_PreviewCard> createState() => _PreviewCardState();
}

class _PreviewCardState extends ConsumerState<_PreviewCard> {
  // '' means "follow the global setting" (TASK 6).
  String _quality = '';
  String _audioFormat = '';

  // "Download a section" state (TASK 9).
  bool _sectionEnabled = false;
  late RangeValues _range;
  late final TextEditingController _startCtl;
  late final TextEditingController _endCtl;

  AnalysisResult get preview => widget.preview;
  int get _durationSec => preview.duration ?? 0;
  bool get _isSpotify => preview.platform == 'spotify';
  // SoundCloud is music-only: the engine always downloads audio for it, so
  // offering 2160p/1080p video qualities there was wrong (BUG).
  bool get _isAudioOnly =>
      _isSpotify || preview.platform == 'soundcloud';
  bool get _canSection =>
      !preview.isPlaylist && !_isSpotify && _durationSec > 0;

  @override
  void initState() {
    super.initState();
    _range = RangeValues(0, _durationSec.toDouble());
    _startCtl = TextEditingController(text: _clockString(0));
    _endCtl = TextEditingController(text: _clockString(_durationSec));
  }

  @override
  void dispose() {
    _startCtl.dispose();
    _endCtl.dispose();
    super.dispose();
  }

  void _startDownload() {
    final settings = ref.read(settingsProvider);
    final overrides = <String, dynamic>{};
    if (!_isAudioOnly && _quality.isNotEmpty) {
      overrides['quality'] = _quality;
    }
    final effectiveQuality =
        _quality.isEmpty ? (settings?.quality ?? 'best') : _quality;
    if ((_isAudioOnly || effectiveQuality == 'audio') &&
        _audioFormat.isNotEmpty) {
      overrides['audio_format'] = _audioFormat;
    }
    Map<String, int>? section;
    if (_canSection && _sectionEnabled) {
      final start = _range.start.round();
      final end = _range.end.round();
      if (start < end) section = {'start_sec': start, 'end_sec': end};
    }
    widget.onDownload(_DownloadOptions(
        overrides: overrides.isEmpty ? null : overrides,
        section: section));
  }

  @override
  Widget build(BuildContext context) {
    final typeLabel = preview.isPlaylist
        ? '${preview.contentType.toUpperCase()} • ${preview.count} items'
        : 'SINGLE';
    // Zero hover behavior anywhere on the card (TASK): this Theme override
    // is inherited by every InkWell/TextField on the card AND by the pills'
    // popup menus (captured themes), so nothing lights up under the mouse.
    // Click feedback and the entrance animation below are untouched.
    return Theme(
      data: Theme.of(context).copyWith(
        hoverColor: Colors.transparent,
        focusColor: Colors.transparent,
      ),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: SizedBox(
                    width: 190,
                    height: 107,
                    child: preview.thumbnail != null
                        ? Image.network(preview.thumbnail!,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) =>
                                const _ThumbFallback())
                        : const _ThumbFallback(),
                  ),
                ),
                const SizedBox(width: 20),
                Expanded(
                  child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                        Row(children: [
                          PlatformBadge(platform: preview.platform),
                          const SizedBox(width: 8),
                          Flexible(
                            child: Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 8, vertical: 4),
                              decoration: BoxDecoration(
                                borderRadius: BorderRadius.circular(999),
                                border: Border.all(
                                    color: MidasColors.border),
                              ),
                              child: Text(typeLabel,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: MidasTheme.ui(10.5,
                                      color: MidasColors.textDim,
                                      weight: 700,
                                      letterSpacing: 0.6)),
                            ),
                          ),
                          if (preview.duration != null &&
                              preview.duration! > 0) ...[
                            const SizedBox(width: 8),
                            Text(formatDuration(preview.duration),
                                style: MidasTheme.ui(12,
                                    color: MidasColors.textDim)),
                          ],
                        ]),
                        const SizedBox(height: 10),
                        Text(preview.title,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: MidasTheme.display(21, weight: 650)),
                        const SizedBox(height: 4),
                        Text(preview.author,
                            style: MidasTheme.ui(13,
                                color: MidasColors.textDim)),
                        if (_canSection && _sectionEnabled) ...[
                          const SizedBox(height: 12),
                          _buildSectionEditor(),
                        ],
                      ]),
                        ),
                        const SizedBox(width: 16),
                        // Primary action stack: the gold download button
                        // with its format/quality pills right beneath it,
                        // so they never steal a full line from the card.
                        Column(
                            crossAxisAlignment: CrossAxisAlignment.end,
                            children: [
                              _buildDownloadButton(),
                              const SizedBox(height: 10),
                              ConstrainedBox(
                                constraints:
                                    const BoxConstraints(maxWidth: 200),
                                child: _buildSelectors(),
                              ),
                            ]),
                      ]),
                ),
              ]),
        ),
      ),
    )
        .animate()
        .fadeIn(duration: 350.ms)
        .slideY(begin: 0.06, curve: Curves.easeOut)
        .then()
        .shimmer(
            duration: 900.ms,
            color: MidasColors.gold.withValues(alpha: 0.12));
  }

  /// Circular gold primary action, pinned to the card's top-right (TASK 2).
  /// Zero hover behavior: the button is completely static under the mouse
  /// and only animates on click (smooth press/scale via PressableScale).
  Widget _buildDownloadButton() {
    return PressableScale(
      pressedScale: 0.88,
      child: Container(
        width: 44,
        height: 44,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: MidasColors.goldGradient,
          boxShadow: [
            BoxShadow(
                color: MidasColors.gold.withValues(alpha: 0.35),
                blurRadius: 14),
          ],
        ),
        child: Material(
          color: Colors.transparent,
          shape: const CircleBorder(),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: _startDownload,
            hoverColor: Colors.transparent,
            focusColor: Colors.transparent,
            child: const Icon(Icons.download_rounded,
                size: 22, color: Color(0xFF141414)),
          ),
        ),
      ),
    );
  }

  String _qualityLabel(String quality) => switch (quality) {
        'audio' => tr('home.quality.audio'),
        'best' => tr('home.quality.best'),
        _ => '${quality}p',
      };

  Widget _buildSelectors() {
    final settings = ref.watch(settingsProvider);
    final globalQuality = settings?.quality ?? 'best';
    final effectiveQuality = _quality.isEmpty ? globalQuality : _quality;
    final audioActive = _isAudioOnly || effectiveQuality == 'audio';
    final globalAudioFormat = settings?.audioFormat ?? 'mp3';

    return Wrap(
      spacing: 8,
      runSpacing: 8,
      alignment: WrapAlignment.end,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        // Spotify and SoundCloud are always audio; the video quality
        // choice is hidden for them.
        if (!_isAudioOnly)
          _selectorPill(
            icon: Icons.tune_rounded,
            label: _quality.isEmpty
                ? tr('home.quality.default',
                    args: {'value': _qualityLabel(globalQuality)})
                : _qualityLabel(_quality),
            items: [
              for (final q in ['', 'best', '2160', '1440', '1080', '720', 'audio'])
                PopupMenuItem<String>(
                  value: q,
                  child: Text(
                      q.isEmpty
                          ? tr('home.quality.default', args: {
                              'value': _qualityLabel(globalQuality)
                            })
                          : _qualityLabel(q),
                      style: MidasTheme.ui(13)),
                ),
            ],
            onSelected: (v) => setState(() => _quality = v),
          ),
        if (audioActive)
          _selectorPill(
            icon: Icons.music_note_rounded,
            label: _audioFormat.isEmpty
                ? tr('home.quality.default',
                    args: {'value': globalAudioFormat.toUpperCase()})
                : _audioFormat.toUpperCase(),
            items: [
              for (final f in ['', 'mp3', 'm4a', 'flac', 'opus'])
                PopupMenuItem<String>(
                  value: f,
                  child: Text(
                      f.isEmpty
                          ? tr('home.quality.default', args: {
                              'value': globalAudioFormat.toUpperCase()
                            })
                          : f.toUpperCase(),
                      style: MidasTheme.ui(13)),
                ),
            ],
            onSelected: (v) => setState(() => _audioFormat = v),
          ),
        if (_canSection) _buildSectionToggle(),
      ],
    );
  }

  Widget _selectorPill({
    required IconData icon,
    required String label,
    required List<PopupMenuEntry<String>> items,
    required ValueChanged<String> onSelected,
  }) {
    // Zero hover behavior: the pill is completely static under the mouse.
    // The only feedback is the click-time press animation, and the local
    // Theme override disables the popup trigger's built-in hover highlight.
    return PressableScale(
      pressedScale: 0.94,
      child: Theme(
        data: Theme.of(context).copyWith(
            hoverColor: Colors.transparent,
            focusColor: Colors.transparent),
        child: PopupMenuButton<String>(
        tooltip: '',
        color: MidasColors.raised,
        onSelected: onSelected,
        itemBuilder: (_) => items,
        child: Container(
          padding:
              const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: MidasColors.border),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 14, color: MidasColors.gold),
            const SizedBox(width: 6),
            Text(label,
                style: MidasTheme.ui(11.5,
                    color: MidasColors.text, weight: 600)),
            const Icon(Icons.arrow_drop_down_rounded,
                size: 18, color: MidasColors.textDim),
          ]),
        ),
        ),
      ),
    );
  }

  Widget _buildSectionToggle() {
    // Zero hover behavior: only the enabled/disabled state (a click)
    // changes the look, animated smoothly by the AnimatedContainer.
    return PressableScale(
      pressedScale: 0.94,
      child: InkWell(
        borderRadius: BorderRadius.circular(999),
        hoverColor: Colors.transparent,
        focusColor: Colors.transparent,
        onTap: () => setState(() => _sectionEnabled = !_sectionEnabled),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOutCubic,
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(999),
            border: Border.all(
                color: _sectionEnabled
                    ? MidasColors.gold
                    : MidasColors.border),
            color: _sectionEnabled
                ? MidasColors.gold.withValues(alpha: 0.08)
                : null,
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.content_cut_rounded,
                size: 14,
                color:
                    _sectionEnabled ? MidasColors.gold : MidasColors.textDim),
            const SizedBox(width: 6),
            Text(tr('home.section.toggle'),
                style: MidasTheme.ui(11.5,
                    color:
                        _sectionEnabled ? MidasColors.gold : MidasColors.text,
                    weight: 600)),
          ]),
        ),
      ),
    );
  }

  Widget _buildSectionEditor() {
    final clipLen = (_range.end - _range.start).round();
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const SizedBox(height: 10),
      Row(children: [
        SizedBox(width: 66, child: _timeField(_startCtl, isStart: true)),
        Expanded(
          child: SliderTheme(
            data: SliderTheme.of(context).copyWith(
              activeTrackColor: MidasColors.gold,
              inactiveTrackColor: MidasColors.border,
              thumbColor: MidasColors.goldBright,
              // No hover/press halo around the thumbs: the card stays
              // completely still under the mouse (TASK).
              overlayShape: SliderComponentShape.noOverlay,
              rangeTrackShape: const RoundedRectRangeSliderTrackShape(),
              trackHeight: 3,
              rangeThumbShape:
                  const RoundRangeSliderThumbShape(enabledThumbRadius: 7),
            ),
            child: RangeSlider(
              values: _range,
              min: 0,
              max: _durationSec.toDouble(),
              onChanged: (v) => setState(() {
                _range = v;
                _syncTimeFields();
              }),
            ),
          ),
        ),
        SizedBox(width: 66, child: _timeField(_endCtl, isStart: false)),
      ]),
      const SizedBox(height: 4),
      Text(
          tr('home.section.clip',
              args: {'length': _clockString(clipLen)}),
          style:
              MidasTheme.ui(11.5, color: MidasColors.gold, weight: 600)),
    ]);
  }

  Widget _timeField(TextEditingController controller,
      {required bool isStart}) {
    return TextField(
      controller: controller,
      textAlign: TextAlign.center,
      style: MidasTheme.ui(12),
      decoration: InputDecoration(
        isDense: true,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 6, vertical: 8),
        hintText:
            isStart ? tr('home.section.start') : tr('home.section.end'),
      ),
      onSubmitted: (_) => _applyTimeFields(),
      onEditingComplete: _applyTimeFields,
    );
  }

  void _applyTimeFields() {
    final parsedStart = _parseClock(_startCtl.text) ?? _range.start.round();
    final parsedEnd = _parseClock(_endCtl.text) ?? _range.end.round();
    setState(() {
      var start = parsedStart.clamp(0, _durationSec).toInt();
      var end = parsedEnd.clamp(0, _durationSec).toInt();
      if (end <= start) end = (start + 1).clamp(0, _durationSec).toInt();
      if (end <= start) start = end > 0 ? end - 1 : 0;
      _range = RangeValues(start.toDouble(), end.toDouble());
      _syncTimeFields();
    });
  }

  void _syncTimeFields() {
    _startCtl.text = _clockString(_range.start.round());
    _endCtl.text = _clockString(_range.end.round());
  }
}

class _PlaylistChoice {
  final String mode; // single | playlist
  final String? items; // --playlist-items string, null = all
  const _PlaylistChoice(this.mode, [this.items]);
}

/// Checkbox picker for playlist links (TASK 7).
class _PlaylistPickerDialog extends StatefulWidget {
  final AnalysisResult preview;
  const _PlaylistPickerDialog({required this.preview});

  @override
  State<_PlaylistPickerDialog> createState() => _PlaylistPickerDialogState();
}

class _PlaylistPickerDialogState extends State<_PlaylistPickerDialog> {
  late final Set<int> _selected;
  final _searchCtl = TextEditingController();
  String _filter = '';

  List<PlaylistEntry> get _entries => widget.preview.entries;

  @override
  void initState() {
    super.initState();
    _selected = _entries.map((e) => e.index).toSet();
  }

  @override
  void dispose() {
    _searchCtl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final entries = _entries;
    final total = entries.isEmpty ? widget.preview.count : entries.length;
    final filtered = _filter.isEmpty
        ? entries
        : entries
            .where((e) =>
                e.title.toLowerCase().contains(_filter.toLowerCase()))
            .toList();

    return AlertDialog(
      title: Row(children: [
        Expanded(
          child: Text(
              widget.preview.title.isEmpty
                  ? tr('playlist.title')
                  : widget.preview.title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis),
        ),
        const SizedBox(width: 10),
        Text('$total',
            style: MidasTheme.ui(13, color: MidasColors.textDim)),
      ]),
      content: entries.isEmpty
          ? Text(tr('playlist.body', args: {'count': '$total'}))
          : SizedBox(
              width: 540,
              height: 420,
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      TextButton(
                          onPressed: () => setState(() {
                                _selected
                                  ..clear()
                                  ..addAll(entries.map((e) => e.index));
                              }),
                          child: Text(tr('playlist.select_all'))),
                      TextButton(
                          onPressed: () =>
                              setState(() => _selected.clear()),
                          child: Text(tr('playlist.select_none'))),
                      const Spacer(),
                      Text(
                          tr('playlist.selected', args: {
                            'selected': '${_selected.length}',
                            'count': '$total',
                          }),
                          style: MidasTheme.ui(12,
                              color: MidasColors.gold, weight: 600)),
                    ]),
                    if (entries.length > 30) ...[
                      const SizedBox(height: 8),
                      TextField(
                        controller: _searchCtl,
                        style: MidasTheme.ui(13),
                        decoration: InputDecoration(
                          isDense: true,
                          hintText: tr('playlist.search'),
                          prefixIcon: const Icon(Icons.search_rounded,
                              size: 18, color: MidasColors.textDim),
                        ),
                        onChanged: (v) =>
                            setState(() => _filter = v.trim()),
                      ),
                    ],
                    const SizedBox(height: 8),
                    Expanded(
                      child: ListView.builder(
                        itemCount: filtered.length,
                        itemBuilder: (context, i) {
                          final entry = filtered[i];
                          final checked = _selected.contains(entry.index);
                          return CheckboxListTile(
                            dense: true,
                            controlAffinity:
                                ListTileControlAffinity.leading,
                            activeColor: MidasColors.gold,
                            checkColor: const Color(0xFF141414),
                            value: checked,
                            onChanged: (v) => setState(() {
                              if (v == true) {
                                _selected.add(entry.index);
                              } else {
                                _selected.remove(entry.index);
                              }
                            }),
                            title: Row(children: [
                              _entryArtwork(entry.thumbnail),
                              Expanded(
                                child: Text(
                                    '${entry.index}.  ${entry.title}',
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: MidasTheme.ui(13)),
                              ),
                            ]),
                            secondary: entry.duration != null
                                ? Text(formatDuration(entry.duration),
                                    style: MidasTheme.ui(11.5,
                                        color: MidasColors.textDim))
                                : null,
                          );
                        },
                      ),
                    ),
                  ]),
            ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(tr('playlist.cancel'))),
        if (widget.preview.kind == 'both')
          OutlinedButton(
              onPressed: () =>
                  Navigator.pop(context, const _PlaylistChoice('single')),
              child: Text(tr('playlist.single'))),
        TextButton(
            onPressed: () =>
                Navigator.pop(context, const _PlaylistChoice('playlist')),
            child:
                Text(tr('playlist.all', args: {'count': '$total'}))),
        if (entries.isNotEmpty)
          ElevatedButton(
            onPressed: _selected.isEmpty
                ? null
                : () {
                    // Collapse checked indices into "1,4,7-12" ranges.
                    final items = _selected.length == entries.length
                        ? null
                        : _collapseIndices(_selected.toList());
                    Navigator.pop(
                        context, _PlaylistChoice('playlist', items));
                  },
            child: Text(tr('playlist.download_selected')),
          ),
      ],
    );
  }
}

/// Small rounded cover for one picker row: the track's OWN artwork (BUG 7),
/// so the list looks like the files that will land on disk. Silently
/// collapses to nothing when a track has no art of its own.
Widget _entryArtwork(String? url) {
  if (url == null || url.isEmpty) return const SizedBox.shrink();
  return Padding(
    padding: const EdgeInsets.only(right: 10),
    child: ClipRRect(
      borderRadius: BorderRadius.circular(4),
      child: Image.network(url,
          width: 28,
          height: 28,
          fit: BoxFit.cover,
          gaplessPlayback: true,
          errorBuilder: (_, __, ___) => const SizedBox.shrink()),
    ),
  );
}

class _SpotifyChoice {
  final List<int> selectedIndices; // 0-based; empty = every track
  const _SpotifyChoice(this.selectedIndices);
}

/// Checkbox picker for Spotify albums/playlists (BUG 5) — same dark/gold
/// dialog style as the playlist picker above.
class _SpotifyTrackPickerDialog extends StatefulWidget {
  final AnalysisResult preview;
  const _SpotifyTrackPickerDialog({required this.preview});

  @override
  State<_SpotifyTrackPickerDialog> createState() =>
      _SpotifyTrackPickerDialogState();
}

class _SpotifyTrackPickerDialogState
    extends State<_SpotifyTrackPickerDialog> {
  late final Set<int> _selected;
  final _searchCtl = TextEditingController();
  String _filter = '';

  List<PlaylistEntry> get _entries => widget.preview.entries;

  @override
  void initState() {
    super.initState();
    _selected = _entries.map((e) => e.index).toSet();
  }

  @override
  void dispose() {
    _searchCtl.dispose();
    super.dispose();
  }

  String _label(PlaylistEntry entry) => entry.artist.isEmpty
      ? entry.title
      : '${entry.artist} – ${entry.title}';

  @override
  Widget build(BuildContext context) {
    final entries = _entries;
    final total = entries.isEmpty ? widget.preview.count : entries.length;
    final filtered = _filter.isEmpty
        ? entries
        : entries
            .where((e) =>
                _label(e).toLowerCase().contains(_filter.toLowerCase()))
            .toList();

    return AlertDialog(
      title: Row(children: [
        Expanded(
          child: Text(
              widget.preview.title.isEmpty
                  ? tr('playlist.title')
                  : widget.preview.title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis),
        ),
        const SizedBox(width: 10),
        Text('$total',
            style: MidasTheme.ui(13, color: MidasColors.textDim)),
      ]),
      content: entries.isEmpty
          ? Text(tr('playlist.body', args: {'count': '$total'}))
          : SizedBox(
              width: 540,
              height: 420,
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      TextButton(
                          onPressed: () => setState(() {
                                _selected
                                  ..clear()
                                  ..addAll(entries.map((e) => e.index));
                              }),
                          child: Text(tr('playlist.select_all'))),
                      TextButton(
                          onPressed: () =>
                              setState(() => _selected.clear()),
                          child: Text(tr('playlist.select_none'))),
                      const Spacer(),
                      Text(
                          tr('playlist.selected', args: {
                            'selected': '${_selected.length}',
                            'count': '$total',
                          }),
                          style: MidasTheme.ui(12,
                              color: MidasColors.gold, weight: 600)),
                    ]),
                    if (entries.length > 20) ...[
                      const SizedBox(height: 8),
                      TextField(
                        controller: _searchCtl,
                        style: MidasTheme.ui(13),
                        decoration: InputDecoration(
                          isDense: true,
                          hintText: tr('playlist.search'),
                          prefixIcon: const Icon(Icons.search_rounded,
                              size: 18, color: MidasColors.textDim),
                        ),
                        onChanged: (v) =>
                            setState(() => _filter = v.trim()),
                      ),
                    ],
                    const SizedBox(height: 8),
                    Expanded(
                      child: ListView.builder(
                        itemCount: filtered.length,
                        itemBuilder: (context, i) {
                          final entry = filtered[i];
                          final checked = _selected.contains(entry.index);
                          return CheckboxListTile(
                            dense: true,
                            controlAffinity:
                                ListTileControlAffinity.leading,
                            activeColor: MidasColors.gold,
                            checkColor: const Color(0xFF141414),
                            value: checked,
                            onChanged: (v) => setState(() {
                              if (v == true) {
                                _selected.add(entry.index);
                              } else {
                                _selected.remove(entry.index);
                              }
                            }),
                            title: Row(children: [
                              _entryArtwork(entry.thumbnail),
                              Expanded(
                                child: Text(
                                    '${entry.index}.  ${_label(entry)}',
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: MidasTheme.ui(13)),
                              ),
                            ]),
                            secondary: entry.duration != null
                                ? Text(formatDuration(entry.duration),
                                    style: MidasTheme.ui(11.5,
                                        color: MidasColors.textDim))
                                : null,
                          );
                        },
                      ),
                    ),
                  ]),
            ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(tr('playlist.cancel'))),
        TextButton(
            onPressed: () =>
                Navigator.pop(context, const _SpotifyChoice([])),
            child:
                Text(tr('playlist.all', args: {'count': '$total'}))),
        if (entries.isNotEmpty)
          ElevatedButton(
            onPressed: _selected.isEmpty
                ? null
                : () {
                    // 0-based picks for the engine's selected_indices.
                    final indices = (_selected.toList()..sort())
                        .map((i) => i - 1)
                        .toList();
                    Navigator.pop(context, _SpotifyChoice(indices));
                  },
            child: Text(tr('playlist.download_selected')),
          ),
      ],
    );
  }
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

/// [3,1,2,7,8,9,12] -> "1-3,7-9,12" (yt-dlp --playlist-items syntax).
String _collapseIndices(List<int> indices) {
  final sorted = [...indices]..sort();
  final parts = <String>[];
  var start = sorted.first;
  var prev = sorted.first;
  for (final i in sorted.skip(1)) {
    if (i == prev + 1) {
      prev = i;
      continue;
    }
    parts.add(start == prev ? '$start' : '$start-$prev');
    start = i;
    prev = i;
  }
  parts.add(start == prev ? '$start' : '$start-$prev');
  return parts.join(',');
}

class _ThumbFallback extends StatelessWidget {
  const _ThumbFallback();

  @override
  Widget build(BuildContext context) {
    return Container(
      color: MidasColors.raised,
      child: const Icon(Icons.music_note_rounded,
          color: MidasColors.goldDeep, size: 36),
    );
  }
}

/// Bottom developer console: live engine + downloader logs, errors in red.
class _LogPanel extends ConsumerWidget {
  const _LogPanel();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final lines = ref.watch(logsProvider);
    return Container(
      height: 180,
      decoration: const BoxDecoration(
        color: Color(0xF20B0B0D),
        border: Border(top: BorderSide(color: Color(0xFF2A2A2E))),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
            child: Row(children: [
              const Icon(Icons.terminal_rounded,
                  size: 14, color: Color(0xFFD4AF37)),
              const SizedBox(width: 6),
              Text('LOGS',
                  style: MidasTheme.ui(11, color: const Color(0xFFD4AF37))
                      .copyWith(letterSpacing: 2)),
              const Spacer(),
              Text('${lines.length} lines',
                  style: MidasTheme.ui(11, color: MidasColors.textDim)),
            ]),
          ),
          Expanded(
            child: ListView.builder(
              reverse: true,
              padding:
                  const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              itemCount: lines.length,
              itemBuilder: (context, i) {
                final line = lines[lines.length - 1 - i];
                final isError = line['level'] == 'error';
                return Text(
                  "${line['ts']}  ${line['source']}  ${line['message']}",
                  style: MidasTheme.ui(11.5,
                      color: isError
                          ? const Color(0xFFC22740)
                          : MidasColors.textDim),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
