import 'package:equatable/equatable.dart';
import 'package:flutter/material.dart';

import '../../core/network/api_client.dart';

/// What a problem report is about (mirrors the backend's `ProblemCategory`).
enum ProblemCategory {
  orderMistake('order_mistake', 'Order mistake', Icons.assignment_late_rounded),
  customerHelp('customer_help', 'Customer needed help', Icons.support_agent_rounded),
  appConfusing('app_confusing', 'App was confusing', Icons.help_outline_rounded),
  network('network', 'Connection problem', Icons.wifi_off_rounded),
  slow('slow', 'Something was slow', Icons.hourglass_bottom_rounded),
  other('other', 'Other', Icons.more_horiz_rounded);

  const ProblemCategory(this.code, this.label, this.icon);

  final String code;
  final String label;
  final IconData icon;

  static ProblemCategory parse(String code) => values.firstWhere((c) => c.code == code, orElse: () => other);
}

class ProblemReport extends Equatable {
  const ProblemReport({
    required this.id,
    required this.category,
    required this.note,
    required this.orderNumber,
    required this.tableLabel,
    required this.reportedBy,
    required this.reportedByRole,
    required this.createdAt,
  });

  final String id;
  final ProblemCategory category;
  final String? note;
  final int? orderNumber;
  final String? tableLabel;
  final String reportedBy;
  final String reportedByRole;
  final DateTime createdAt;

  factory ProblemReport.fromJson(Map<String, dynamic> json) => ProblemReport(
    id: json['id'] as String,
    category: ProblemCategory.parse(json['category'] as String),
    note: json['note'] as String?,
    orderNumber: json['order_number'] as int?,
    tableLabel: json['table_label'] as String?,
    reportedBy: json['reported_by'] as String,
    reportedByRole: json['reported_by_role'] as String,
    createdAt: DateTime.parse(json['created_at'] as String).toLocal(),
  );

  @override
  List<Object?> get props => [id, category, note, orderNumber, tableLabel, reportedBy, createdAt];
}

class ProblemReportsRepository {
  const ProblemReportsRepository(this._api);

  final ApiClient _api;

  Future<void> report(ProblemCategory category, {String? note, String? orderId}) async {
    final text = note?.trim();
    await _api.postJson<Map<String, dynamic>>(
      '/problem-reports',
      data: {
        'category': category.code,
        if (text != null && text.isNotEmpty) 'note': text,
        'order_id': ?orderId,
      },
    );
  }

  Future<List<ProblemReport>> recent() async {
    final data = await _api.getJson<List<dynamic>>('/problem-reports', query: {'limit': 200});
    return [for (final row in data) ProblemReport.fromJson(row as Map<String, dynamic>)];
  }
}
