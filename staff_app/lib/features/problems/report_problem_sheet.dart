import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import '../../core/network/api_exception.dart';
import '../orders/order_models.dart';
import 'problem_reports.dart';

/// "Something went wrong" — any staff member, any time, optionally about one order. Feeds the
/// pilot review (order mistakes, guests who needed help, confusing screens, Wi-Fi drops).
Future<void> showReportProblemSheet(BuildContext context, {StaffOrder? order}) async {
  final repository = context.read<ProblemReportsRepository>();
  final messenger = ScaffoldMessenger.of(context);
  final sent = await showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    builder: (_) => _ReportSheet(repository: repository, order: order),
  );
  if (sent == true) {
    messenger.showSnackBar(const SnackBar(content: Text('Thanks — your manager will see this.')));
  }
}

class _ReportSheet extends StatefulWidget {
  const _ReportSheet({required this.repository, this.order});

  final ProblemReportsRepository repository;
  final StaffOrder? order;

  @override
  State<_ReportSheet> createState() => _ReportSheetState();
}

class _ReportSheetState extends State<_ReportSheet> {
  final _note = TextEditingController();
  ProblemCategory? _category;
  bool _sending = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    if (widget.order != null) _category = ProblemCategory.orderMistake;
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final category = _category;
    if (category == null) return;
    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      await widget.repository.report(category, note: _note.text, orderId: widget.order?.id);
      if (mounted) Navigator.pop(context, true);
    } on ApiException catch (error) {
      if (mounted) {
        setState(() {
          _sending = false;
          _error = error.message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final order = widget.order;
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.viewInsetsOf(context).bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            order == null
                ? 'Report a problem'
                : 'Report a problem with #${order.orderNumber} (${order.tableLabel})',
            style: theme.textTheme.titleLarge,
          ),
          const SizedBox(height: 4),
          Text('Helps us fix what gets in your way. Your manager sees it.', style: theme.textTheme.bodySmall),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final category in ProblemCategory.values)
                ChoiceChip(
                  avatar: Icon(category.icon, size: 18),
                  label: Text(category.label),
                  selected: _category == category,
                  onSelected: (_) => setState(() => _category = category),
                ),
            ],
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _note,
            maxLength: 500,
            maxLines: 3,
            minLines: 2,
            decoration: const InputDecoration(
              labelText: 'What happened? (optional)',
              hintText: 'e.g. guest could not find the PIN',
            ),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
            ),
          FilledButton.icon(
            onPressed: _category == null || _sending ? null : _send,
            icon: const Icon(Icons.flag_rounded),
            label: Text(_sending ? 'Sending…' : 'Send report'),
          ),
        ],
      ),
    );
  }
}
