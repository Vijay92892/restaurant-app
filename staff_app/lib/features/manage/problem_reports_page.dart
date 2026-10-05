import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:intl/intl.dart';

import '../problems/problem_reports.dart';
import 'manage_cubits.dart';

/// What staff reported during service, newest first (pilot observation).
class ProblemReportsPage extends StatelessWidget {
  const ProblemReportsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Problem reports')),
      body: BlocBuilder<ProblemReportsCubit, ManageState<List<ProblemReport>>>(
        builder: (context, state) {
          final reports = state.data;
          if (reports == null) {
            return Center(
              child: state.loading
                  ? const CircularProgressIndicator()
                  : Text(state.error ?? 'Could not load reports.'),
            );
          }
          return RefreshIndicator(
            onRefresh: context.read<ProblemReportsCubit>().load,
            child: reports.isEmpty
                ? ListView(
                    children: const [
                      SizedBox(height: 120),
                      Center(child: Text('Nothing reported yet.')),
                    ],
                  )
                : ListView.separated(
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    itemCount: reports.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (context, index) => _ReportTile(reports[index]),
                  ),
          );
        },
      ),
    );
  }
}

class _ReportTile extends StatelessWidget {
  const _ReportTile(this.report);

  final ProblemReport report;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final when = DateFormat.MMMd().add_jm().format(report.createdAt);
    final order = report.orderNumber == null ? '' : ' · order #${report.orderNumber}, ${report.tableLabel}';
    return ListTile(
      leading: Icon(report.category.icon),
      title: Text(report.category.label),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (report.note != null) Text(report.note!, style: theme.textTheme.bodyMedium),
          Text(
            '${report.reportedBy} (${report.reportedByRole}) · $when$order',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
      isThreeLine: report.note != null,
    );
  }
}
