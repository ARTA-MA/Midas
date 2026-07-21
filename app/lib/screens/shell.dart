import 'package:animations/animations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/i18n/strings.dart';
import '../core/theme/midas_theme.dart';
import '../providers/app_providers.dart';
import '../widgets/widgets.dart';
import 'home/home_screen.dart';
import 'queue/queue_screen.dart';
import 'settings/settings_screen.dart';
import 'studio/studio_screen.dart';

class Shell extends ConsumerStatefulWidget {
  const Shell({super.key});

  @override
  ConsumerState<Shell> createState() => _ShellState();
}

class _ShellState extends ConsumerState<Shell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final liveCount = ref
        .watch(downloadsProvider)
        .active
        .where((d) => d.isLive)
        .length;
    final deps = ref.watch(depsProvider);

    final pages = [
      HomeScreen(onDownloadQueued: () => setState(() => _index = 1)),
      const QueueScreen(),
      const StudioScreen(),
      const SettingsScreen(),
    ];

    return Scaffold(
      body: Row(children: [
        Container(
          width: 224,
          color: MidasColors.surface,
          padding: const EdgeInsets.symmetric(vertical: 28),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Center(child: MidasWordmark(size: 34)),
                const SizedBox(height: 4),
                Center(
                  child: Text(tr('app.tagline'),
                      textAlign: TextAlign.center,
                      style:
                          MidasTheme.ui(10.5, color: MidasColors.textDim)),
                ),
                const SizedBox(height: 34),
                _NavItem(
                    icon: Icons.auto_awesome_rounded,
                    label: tr('nav.home'),
                    selected: _index == 0,
                    onTap: () => setState(() => _index = 0)),
                _NavItem(
                    icon: Icons.download_rounded,
                    label: tr('nav.downloads'),
                    selected: _index == 1,
                    badge: liveCount,
                    onTap: () => setState(() => _index = 1)),
                _NavItem(
                    icon: Icons.auto_fix_high_rounded,
                    label: tr('nav.studio'),
                    selected: _index == 2,
                    onTap: () {
                      setState(() => _index = 2);
                      // Entering the tab always shows the current list
                      // of editable files (BUG 2).
                      ref.read(studioProvider.notifier).refresh();
                    }),
                _NavItem(
                    icon: Icons.tune_rounded,
                    label: tr('nav.settings'),
                    selected: _index == 3,
                    onTap: () => setState(() => _index = 3)),
                const Spacer(),
                if (deps.loaded && deps.anyMissing)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: HoverScale(
                      child: InkWell(
                        borderRadius: BorderRadius.circular(10),
                        onTap: () => setState(() => _index = 3),
                        child: Container(
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(
                                color: MidasColors.goldDeep),
                            color: MidasColors.gold.withValues(alpha: 0.08),
                          ),
                          child: Row(children: [
                            const Icon(Icons.priority_high_rounded,
                                size: 16, color: MidasColors.gold),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text('Finish setup:\ninstall tools',
                                  style: MidasTheme.ui(11.5,
                                      color: MidasColors.gold,
                                      weight: 600)),
                            ),
                          ]),
                        ),
                      ),
                    ),
                  ),
              ]),
        ),
        const VerticalDivider(width: 1, thickness: 1),
        Expanded(
          child: PageTransitionSwitcher(
            duration: const Duration(milliseconds: 350),
            transitionBuilder: (child, primary, secondary) =>
                FadeThroughTransition(
              animation: primary,
              secondaryAnimation: secondary,
              fillColor: MidasColors.bg,
              child: child,
            ),
            child: KeyedSubtree(
                key: ValueKey(_index), child: pages[_index]),
          ),
        ),
      ]),
    );
  }
}

class _NavItem extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool selected;
  final int badge;
  final VoidCallback onTap;
  const _NavItem(
      {required this.icon,
      required this.label,
      required this.selected,
      required this.onTap,
      this.badge = 0});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 3),
      child: HoverScale(
        scale: 1.015,
        child: Material(
          color: selected
              ? MidasColors.gold.withValues(alpha: 0.12)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(10),
          child: InkWell(
            borderRadius: BorderRadius.circular(10),
            onTap: onTap,
            child: Padding(
              padding:
                  const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
              child: Row(children: [
                Icon(icon,
                    size: 19,
                    color:
                        selected ? MidasColors.gold : MidasColors.textDim),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(label,
                      style: MidasTheme.ui(14,
                          color: selected
                              ? MidasColors.text
                              : MidasColors.textDim,
                          weight: selected ? 700 : 500)),
                ),
                if (badge > 0)
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 7, vertical: 2),
                    decoration: BoxDecoration(
                      gradient: MidasColors.goldGradient,
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text('$badge',
                        style: MidasTheme.ui(11,
                            color: const Color(0xFF141414), weight: 800)),
                  ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}
