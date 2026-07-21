import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/i18n/strings.dart';
import '../../core/theme/midas_theme.dart';
import '../../core/utils/format.dart';
import '../../models/models.dart';
import '../../providers/app_providers.dart';
import '../../services/api_client.dart';
import '../../widgets/widgets.dart';

class QueueScreen extends ConsumerWidget {
  const QueueScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(downloadsProvider);
    // Paused items stay in the live section so Resume is one click away.
    final live =
        state.active.where((d) => d.isLive || d.isPaused).toList();
    final liveIds = live.map((d) => d.id).toSet();
    final done = state.history.where((d) => !liveIds.contains(d.id)).toList();
    final anyLive = state.active.any((d) => d.isLive);
    final anyPaused = state.active.any((d) => d.isPaused) ||
        state.history.any((d) => d.isPaused);

    final Widget content;
    if (state.loaded && live.isEmpty && done.isEmpty) {
      content = MidasEmptyState(
        image: 'assets/images/empty_state.jpg',
        title: tr('queue.empty.title'),
        body: tr('queue.empty.body'),
      );
    } else {
      content = Padding(
      padding: const EdgeInsets.fromLTRB(36, 30, 36, 0),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(tr('nav.downloads'), style: MidasTheme.display(34)),
          const Spacer(),
          if (anyLive)
            TextButton.icon(
              onPressed: () => ref.read(apiProvider)?.pauseAll(),
              icon: const Icon(Icons.pause_rounded, size: 18),
              label: Text(tr('queue.pause_all')),
            ),
          if (anyPaused)
            TextButton.icon(
              onPressed: () async {
                await ref.read(apiProvider)?.resumeAll();
                ref.read(downloadsProvider.notifier).refresh();
              },
              icon: const Icon(Icons.play_arrow_rounded, size: 18),
              label: Text(tr('queue.resume_all')),
            ),
          if (done.isNotEmpty)
            TextButton.icon(
              onPressed: () async {
                await ref.read(apiProvider)?.clearHistory();
                ref.read(downloadsProvider.notifier).refresh();
              },
              icon: const Icon(Icons.delete_sweep_rounded, size: 18),
              label: const Text('Clear history'),
            ),
        ]),
        const SizedBox(height: 18),
        Expanded(
          child: ListView(children: [
            for (final item in live)
              _DownloadCard(item: item, key: ValueKey('l-${item.id}')),
            if (live.isNotEmpty && done.isNotEmpty) ...[
              const SizedBox(height: 22),
              Text(tr('queue.completed').toUpperCase(),
                  style: MidasTheme.ui(11.5,
                      color: MidasColors.textDim,
                      weight: 700,
                      letterSpacing: 1.2)),
              const SizedBox(height: 10),
            ],
            for (final item in done)
              _DownloadCard(item: item, key: ValueKey('h-${item.id}')),
            const SizedBox(height: 24),
          ]),
        ),
      ]),
    );
    }

    return Stack(children: [
      // Same golden backdrop as Home, so pages feel continuous.
      Positioned.fill(
        child: ShaderMask(
          shaderCallback: (bounds) => const LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Colors.white, Color(0x44FFFFFF), Color(0x11FFFFFF)],
            stops: [0.0, 0.5, 1.0],
          ).createShader(bounds),
          blendMode: BlendMode.dstIn,
          child: Opacity(
            opacity: 0.35,
            child: Image.asset('assets/images/header_bg.jpg',
                width: double.infinity,
                height: double.infinity,
                fit: BoxFit.cover),
          ),
        ),
      ),
      content,
    ]);
  }
}

class _DownloadCard extends ConsumerWidget {
  final DownloadItem item;
  const _DownloadCard({super.key, required this.item});

  /// Card artwork: the platform thumbnail when there is one; otherwise the
  /// cover art embedded in the finished file (local/converted items have no
  /// web thumbnail but usually carry their own art); otherwise the icon.
  Widget _thumbnailFor(DownloadItem item, ApiClient? api) {
    if (item.thumbnail != null) {
      return Image.network(item.thumbnail!,
          fit: BoxFit.cover,
          errorBuilder: (_, __, ___) => _thumbFallback());
    }
    if (api != null && item.status == 'finished') {
      return Image.network(api.studioCoverUrl(item.id),
          fit: BoxFit.cover,
          gaplessPlayback: true,
          errorBuilder: (_, __, ___) => _thumbFallback());
    }
    return _thumbFallback();
  }

  Widget _thumbFallback() => Container(
      color: MidasColors.raised,
      child: const Icon(Icons.audiotrack_rounded,
          color: MidasColors.goldDeep));

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final api = ref.read(apiProvider);
    final isDownloading =
        item.status == 'downloading' || item.status == 'processing';

    // The platform badge opens the second (status) line in every state, so
    // the right edge of the card stays reserved for the action icons.
    final badge = PlatformBadge(platform: item.platform, compact: true);

    Widget card = Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: SizedBox(
              width: 104,
              height: 58,
              child: _thumbnailFor(item, api),
            ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(item.title.isEmpty ? item.url : item.title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: MidasTheme.ui(14, weight: 700)),
              const SizedBox(height: 8),
              if (isDownloading || item.status == 'queued' ||
                  item.status == 'starting') ...[
                GoldProgressBar(
                    percent: item.percent, active: isDownloading),
                const SizedBox(height: 6),
                Row(children: [
                  badge,
                  const SizedBox(width: 8),
                  Text(_statusLabel(),
                      style: MidasTheme.ui(11.5,
                          color: MidasColors.gold, weight: 700)),
                  const SizedBox(width: 12),
                  if (item.status == 'downloading')
                    Flexible(
                      child: Text(
                          '${item.percent.toStringAsFixed(0)}%  •  '
                          '${formatSpeed(item.speed)}  •  '
                          'ETA ${formatEta(item.eta)}'
                          '${item.itemIndex != null && item.itemCount != null ? '   •  ${item.itemIndex}/${item.itemCount}' : ''}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: MidasTheme.ui(11.5,
                              color: MidasColors.textDim)),
                    ),
                ]),
              ] else if (item.status == 'paused') ...[
                // Frozen at the last percent; the shimmer stays inactive.
                GoldProgressBar(percent: item.percent, active: false),
                const SizedBox(height: 6),
                Row(children: [
                  badge,
                  const SizedBox(width: 8),
                  const Icon(Icons.pause_circle_outline,
                      size: 15, color: MidasColors.goldDeep),
                  const SizedBox(width: 6),
                  Text(tr('queue.paused'),
                      style: MidasTheme.ui(11.5,
                          color: MidasColors.goldDeep, weight: 700)),
                  if (item.percent > 0) ...[
                    const SizedBox(width: 12),
                    Text('${item.percent.toStringAsFixed(0)}%',
                        style: MidasTheme.ui(11.5,
                            color: MidasColors.textDim)),
                  ],
                ]),
              ] else if (item.status == 'error')
                Row(children: [
                  badge,
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(item.error ?? 'Download failed.',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: MidasTheme.ui(12, color: MidasColors.red)),
                  ),
                ])
              else if (item.status == 'cancelled')
                Row(children: [
                  badge,
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text('Cancelled',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: MidasTheme.ui(12,
                            color: MidasColors.textDim)),
                  ),
                ])
              else
                Row(children: [
                  badge,
                  const SizedBox(width: 8),
                  const Icon(Icons.check_circle_rounded,
                      size: 15, color: MidasColors.gold),
                  const SizedBox(width: 6),
                  Flexible(
                    child: Text('Turned to gold',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: MidasTheme.ui(12,
                            color: MidasColors.gold, weight: 600)),
                  ),
                ]),
            ]),
          ),
          const SizedBox(width: 10),
          ..._actions(context, ref, api),
        ]),
      ),
    );

    if (item.status == 'completed') {
      card = card.animate().shimmer(
          duration: 1200.ms,
          color: MidasColors.gold.withValues(alpha: 0.15));
    }
    // Zero hover behavior on download cards: the card is completely static
    // under the mouse; buttons animate on click only (TASK 5).
    return card;
  }

  /// Per-row history delete — removes just this entry, unlike Clear history.
  Widget _removeButton(WidgetRef ref, dynamic api) => PressableScale(
      pressedScale: 0.85,
      child: IconButton(
          onPressed: () async {
            await api?.deleteHistoryItem(item.id);
            ref.read(downloadsProvider.notifier).refresh();
          },
          style: IconButton.styleFrom(
              hoverColor: Colors.transparent,
              focusColor: Colors.transparent,
              highlightColor:
                  MidasColors.textDim.withValues(alpha: 0.12)),
          icon: const Icon(Icons.delete_outline_rounded,
              size: 19, color: MidasColors.textDim)));

  String _statusLabel() => switch (item.status) {
        'queued' => 'Queued',
        'starting' => 'Starting…',
        'processing' => 'Finishing touches…',
        _ => 'Downloading',
      };

  List<Widget> _actions(BuildContext context, WidgetRef ref, dynamic api) {
    // Click-only feedback: a smooth press/scale animation plus a subtle
    // pressed highlight. Nothing reacts to hover (TASK 5).
    Widget iconButton(IconData icon, String tooltip, VoidCallback onTap,
            {Color color = MidasColors.textDim}) =>
        PressableScale(
            pressedScale: 0.85,
            child: IconButton(
                onPressed: onTap,
                style: IconButton.styleFrom(
                    hoverColor: Colors.transparent,
                    focusColor: Colors.transparent,
                    highlightColor: color.withValues(alpha: 0.12)),
                icon: Icon(icon, size: 19, color: color)));

    return switch (item.status) {
      'queued' || 'starting' || 'downloading' || 'processing' => [
          iconButton(Icons.pause_rounded, tr('queue.pause'),
              () => api?.pause(item.id),
              color: MidasColors.gold),
          iconButton(Icons.close_rounded, 'Cancel',
              () => api?.cancel(item.id),
              color: MidasColors.red),
        ],
      'paused' => [
          iconButton(Icons.play_arrow_rounded, tr('queue.resume'),
              () => api?.resume(item.id),
              color: MidasColors.gold),
          iconButton(Icons.close_rounded, 'Cancel',
              () => api?.cancel(item.id),
              color: MidasColors.red),
        ],
      'error' || 'cancelled' => [
          iconButton(Icons.refresh_rounded, 'Retry',
              () => api?.retry(item.id),
              color: MidasColors.gold),
          _removeButton(ref, api),
        ],
      _ => [
          iconButton(Icons.folder_open_rounded, 'Open folder',
              () => api?.openFolder(item.id)),
          _removeButton(ref, api),
        ],
    };
  }
}
