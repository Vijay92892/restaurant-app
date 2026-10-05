import 'package:flutter/material.dart';

import 'live_updates.dart';

/// "Live" / "Connecting…" / "Offline" with an icon and text, never colour alone.
class LiveIndicator extends StatelessWidget {
  const LiveIndicator(this.status, {super.key, this.compact = false});

  final LiveStatus status;

  /// Icon only (with a tooltip), for narrow app bars.
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final (icon, label, color) = switch (status) {
      LiveStatus.live => (
        Icons.wifi_rounded,
        'Live',
        dark ? const Color(0xFF4CCF72) : const Color(0xFF1A7F37),
      ),
      LiveStatus.connecting => (
        Icons.sync_rounded,
        'Connecting…',
        dark ? const Color(0xFFF5C56B) : const Color(0xFF8A5300),
      ),
      LiveStatus.offline => (
        Icons.wifi_off_rounded,
        'Offline',
        dark ? const Color(0xFFFB7D9A) : const Color(0xFF9F1239),
      ),
    };
    final tooltip = switch (status) {
      LiveStatus.live => 'Live: new orders appear instantly',
      LiveStatus.connecting => 'Connecting to live updates…',
      LiveStatus.offline => 'Offline: showing the last orders received; retrying',
    };
    return Tooltip(
      message: tooltip,
      child: Semantics(
        label: tooltip,
        excludeSemantics: true,
        child: Container(
          padding: EdgeInsets.symmetric(horizontal: compact ? 8 : 12, vertical: 6),
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: color.withValues(alpha: 0.5)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 18, color: color),
              if (!compact) ...[
                const SizedBox(width: 6),
                Text(
                  label,
                  style: TextStyle(color: color, fontWeight: FontWeight.w800),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
