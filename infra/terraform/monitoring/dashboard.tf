# SPDX-License-Identifier: Apache-2.0
#
# One dashboard, matching the alerts: every panel reads a metric an alert reads, so an operator
# looking at a firing alert can see the series behind it without building a query.

resource "google_monitoring_dashboard" "governance" {
  project        = var.project_id
  dashboard_json = file("${path.module}/../../../monitoring/dashboards/agenticorg-governance.json")
}
