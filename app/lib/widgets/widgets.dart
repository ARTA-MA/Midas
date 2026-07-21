import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:shimmer/shimmer.dart';

import '../core/theme/midas_theme.dart';

/// "Midas" wordmark: display face + animated gold gradient sheen.
class MidasWordmark extends StatelessWidget {
  final double size;
  const MidasWordmark({super.key, this.size = 40});

  @override
  Widget build(BuildContext context) {
    return ShaderMask(
      shaderCallback: (bounds) =>
          MidasColors.goldGradient.createShader(bounds),
      child: Text('MIDAS',
          style: MidasTheme.display(size, weight: 700)
              .copyWith(color: Colors.white, letterSpacing: size * 0.22)),
    )
        .animate(onPlay: (c) => c.repeat())
        .shimmer(
            duration: 3200.ms,
            delay: 1800.ms,
            color: MidasColors.goldBright.withValues(alpha: 0.55));
  }
}

/// Hover/press micro-interaction wrapper.
/// Click-only micro-interaction: scales down smoothly while the pointer is
/// held and springs back on release. Implemented with a [Listener], so the
/// wrapped widget keeps handling its own taps. Nothing reacts to hover -
/// the widget is completely static under the mouse (only the cursor
/// changes, which never affects layout).
class PressableScale extends StatefulWidget {
  final Widget child;
  final double pressedScale;
  const PressableScale(
      {super.key, required this.child, this.pressedScale = 0.9});

  @override
  State<PressableScale> createState() => _PressableScaleState();
}

class _PressableScaleState extends State<PressableScale> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      child: Listener(
        behavior: HitTestBehavior.deferToChild,
        onPointerDown: (_) => setState(() => _pressed = true),
        onPointerUp: (_) => setState(() => _pressed = false),
        onPointerCancel: (_) => setState(() => _pressed = false),
        child: AnimatedScale(
          scale: _pressed ? widget.pressedScale : 1,
          duration: const Duration(milliseconds: 130),
          curve: Curves.easeOutCubic,
          child: widget.child,
        ),
      ),
    );
  }
}

class HoverScale extends StatefulWidget {
  final Widget child;
  final double scale;
  const HoverScale({super.key, required this.child, this.scale = 1.02});

  @override
  State<HoverScale> createState() => _HoverScaleState();
}

class _HoverScaleState extends State<HoverScale> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: AnimatedScale(
        scale: _hover ? widget.scale : 1,
        duration: const Duration(milliseconds: 260),
        curve: Curves.easeOutCubic,
        child: widget.child,
      ),
    );
  }
}

const Map<String, (Color, IconData, String)> _platformStyle = {
  'youtube': (Color(0xFFE53935), Icons.play_arrow_rounded, 'YouTube'),
  'spotify': (Color(0xFF1DB954), Icons.music_note_rounded, 'Spotify'),
  'soundcloud': (Color(0xFFFF7700), Icons.graphic_eq_rounded, 'SoundCloud'),
  'instagram': (Color(0xFFD81B60), Icons.camera_alt_rounded, 'Instagram'),
  'tiktok': (Color(0xFF9C27B0), Icons.music_video_rounded, 'TikTok'),
  'reddit': (Color(0xFFFF4500), Icons.forum_rounded, 'Reddit'),
};

class PlatformBadge extends StatelessWidget {
  final String platform;
  final bool compact;
  const PlatformBadge({super.key, required this.platform, this.compact = false});

  @override
  Widget build(BuildContext context) {
    final (color, icon, label) = _platformStyle[platform] ??
        (MidasColors.textDim, Icons.link_rounded, platform);
    return Container(
      padding: EdgeInsets.symmetric(
          horizontal: compact ? 8 : 10, vertical: compact ? 3 : 5),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: compact ? 12 : 14, color: color),
        const SizedBox(width: 5),
        Text(label,
            style: MidasTheme.ui(compact ? 11 : 12,
                color: color, weight: 700)),
      ]),
    );
  }
}

/// Animated golden progress bar with a moving sheen while active.
class GoldProgressBar extends StatelessWidget {
  final double percent; // 0..100
  final bool active;
  const GoldProgressBar(
      {super.key, required this.percent, this.active = true});

  @override
  Widget build(BuildContext context) {
    final fraction = (percent / 100).clamp(0.0, 1.0);
    return ClipRRect(
      borderRadius: BorderRadius.circular(999),
      child: Stack(children: [
        Container(height: 8, color: MidasColors.raised),
        LayoutBuilder(builder: (context, constraints) {
          Widget fill = AnimatedContainer(
            duration: const Duration(milliseconds: 300),
            curve: Curves.easeOut,
            height: 8,
            width: constraints.maxWidth * fraction,
            decoration:
                const BoxDecoration(gradient: MidasColors.goldGradient),
          );
          if (active) {
            fill = fill.animate(onPlay: (c) => c.repeat()).shimmer(
                duration: 1600.ms,
                color: Colors.white.withValues(alpha: 0.35));
          }
          return fill;
        }),
      ]),
    );
  }
}

class MidasEmptyState extends StatelessWidget {
  final String image;
  final String title;
  final String body;
  const MidasEmptyState(
      {super.key,
      required this.image,
      required this.title,
      required this.body});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(16),
          child: Image.asset(image, width: 340, fit: BoxFit.cover),
        )
            .animate()
            .fadeIn(duration: 500.ms)
            .scale(begin: const Offset(0.96, 0.96), curve: Curves.easeOut),
        const SizedBox(height: 24),
        Text(title, style: MidasTheme.display(30))
            .animate()
            .fadeIn(delay: 150.ms),
        const SizedBox(height: 8),
        Text(body,
                textAlign: TextAlign.center,
                style: MidasTheme.ui(14, color: MidasColors.textDim))
            .animate()
            .fadeIn(delay: 250.ms),
      ]),
    );
  }
}

class MidasErrorState extends StatelessWidget {
  final String title;
  final String message;
  const MidasErrorState(
      {super.key, required this.title, required this.message});

  @override
  Widget build(BuildContext context) {
    return Column(mainAxisSize: MainAxisSize.min, children: [
      ClipRRect(
        borderRadius: BorderRadius.circular(16),
        child:
            Image.asset('assets/images/error_state.jpg', width: 300),
      )
          .animate()
          .fadeIn(duration: 400.ms)
          .shake(hz: 3, rotation: 0.01, duration: 500.ms),
      const SizedBox(height: 18),
      Text(title, style: MidasTheme.display(24)),
      const SizedBox(height: 6),
      Text(message,
          textAlign: TextAlign.center,
          style: MidasTheme.ui(14, color: MidasColors.red.withValues(alpha: 0.9))),
    ]).animate().fadeIn();
  }
}

/// Shimmer skeleton shown while a link is being analyzed.
class SkeletonPreviewCard extends StatelessWidget {
  const SkeletonPreviewCard({super.key});

  @override
  Widget build(BuildContext context) {
    Widget block(double w, double h) => Container(
          width: w,
          height: h,
          decoration: BoxDecoration(
              color: MidasColors.raised,
              borderRadius: BorderRadius.circular(8)),
        );
    return Shimmer.fromColors(
      baseColor: MidasColors.raised,
      highlightColor: MidasColors.border,
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Row(children: [
            block(180, 102),
            const SizedBox(width: 20),
            Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    block(120, 22),
                    const SizedBox(height: 12),
                    block(double.infinity, 18),
                    const SizedBox(height: 8),
                    block(200, 14),
                  ]),
            ),
          ]),
        ),
      ),
    );
  }
}
