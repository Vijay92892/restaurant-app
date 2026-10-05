import 'package:flutter/material.dart';

import '../../features/menu/menu_models.dart';
import '../../features/orders/order_models.dart';

/// Order status as icon + text + colour — never colour alone (accessibility).
class StatusBadge extends StatelessWidget {
  const StatusBadge(this.status, {super.key, this.cancelRequested = false});

  final OrderStatus status;
  final bool cancelRequested;

  /// Icon and colour for a status; lighter tones on dark backgrounds keep the contrast.
  static (IconData, Color) styleFor(OrderStatus status, {bool dark = false}) => switch (status) {
    OrderStatus.placed => (Icons.fiber_new_rounded, dark ? const Color(0xFFF5C56B) : const Color(0xFFB26A00)),
    OrderStatus.accepted => (
      Icons.thumb_up_alt_rounded,
      dark ? const Color(0xFFF08A5D) : const Color(0xFFB4451A),
    ),
    OrderStatus.preparing => (
      Icons.local_fire_department_rounded,
      dark ? const Color(0xFFF08A5D) : const Color(0xFFB4451A),
    ),
    OrderStatus.ready => (Icons.check_circle_rounded, readyColor(dark: dark)),
    OrderStatus.served => (
      Icons.restaurant_rounded,
      dark ? const Color(0xFFC0C6CF) : const Color(0xFF4B5563),
    ),
    OrderStatus.completed => (
      Icons.done_all_rounded,
      dark ? const Color(0xFFC0C6CF) : const Color(0xFF4B5563),
    ),
    OrderStatus.cancelled => (Icons.cancel_rounded, dark ? const Color(0xFFFB7D9A) : const Color(0xFF9F1239)),
  };

  static Color readyColor({required bool dark}) => dark ? const Color(0xFF4CCF72) : const Color(0xFF1A7F37);

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final (icon, color) = cancelRequested
        ? (Icons.report_rounded, dark ? const Color(0xFFFB7D9A) : const Color(0xFF9F1239))
        : styleFor(status, dark: dark);
    final label = cancelRequested ? 'Cancel requested' : status.label;
    return Semantics(
      label: 'Status: $label',
      excludeSemantics: true,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: color.withValues(alpha: 0.5)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 18, color: color),
            const SizedBox(width: 6),
            Text(
              label,
              style: TextStyle(color: color, fontWeight: FontWeight.w800),
            ),
          ],
        ),
      ),
    );
  }
}

/// Indian food-type mark: green square with a dot (veg), red with a triangle (non-veg),
/// amber dot (egg). Labelled for screen readers.
class DietMark extends StatelessWidget {
  const DietMark(this.dietary, {super.key, this.size = 16});

  final Dietary? dietary;
  final double size;

  @override
  Widget build(BuildContext context) {
    final diet = dietary;
    if (diet == null) return const SizedBox.shrink();
    final (color, label) = switch (diet) {
      Dietary.veg => (const Color(0xFF1A7F37), 'Vegetarian'),
      Dietary.nonVeg => (const Color(0xFFA52A2A), 'Non-vegetarian'),
      Dietary.egg => (const Color(0xFFB07D00), 'Contains egg'),
    };
    return Semantics(
      label: label,
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          border: Border.all(color: color, width: 2),
          borderRadius: BorderRadius.circular(3),
        ),
        alignment: Alignment.center,
        child: diet == Dietary.nonVeg
            ? Icon(Icons.change_history_rounded, size: size * 0.6, color: color)
            : Container(
                width: size * 0.45,
                height: size * 0.45,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
      ),
    );
  }
}
