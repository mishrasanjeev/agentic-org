# SPDX-License-Identifier: Apache-2.0
#
# The PRD §10 alerts as Cloud Monitoring policies.
#
# The PromQL is NOT written here. Terraform reads
# monitoring/prometheus/agenticorg-alerts.yml - the same file promtool checks and unit-tests in CI
# - and creates one policy per rule. There is one definition of each alert, so a rule and its
# deployed policy cannot drift: change the YAML, test it with promtool, apply.
#
# Managed Service for Prometheus evaluates PromQL against the samples the in-instance collector
# pushes (see PR 2 for the collector itself). Until that lands these policies have no data to
# evaluate, which is why they are in their own module and not applied by default.

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

variable "project_id" {
  type        = string
  description = "The GCP project the services run in."
}

variable "notification_channels" {
  type        = list(string)
  default     = []
  description = "Cloud Monitoring notification channel ids alerts are sent to."
}

variable "rules_file" {
  type        = string
  default     = "../../../monitoring/prometheus/agenticorg-alerts.yml"
  description = "The single source of truth for alert definitions. Do not inline PromQL here."
}

locals {
  rule_groups = yamldecode(file("${path.module}/${var.rules_file}")).groups

  # alert name -> the rule, flattened out of the groups.
  alerts = {
    for rule in flatten([for group in local.rule_groups : group.rules]) :
    rule.alert => rule
  }
}

resource "google_monitoring_alert_policy" "agenticorg" {
  for_each = local.alerts

  project      = var.project_id
  display_name = each.key
  combiner     = "OR"
  severity     = upper(lookup(each.value.labels, "severity", "warning")) == "CRITICAL" ? "CRITICAL" : "WARNING"

  documentation {
    content   = "${each.value.annotations.summary}\n\n${each.value.annotations.description}\n\nRunbook: ${each.value.annotations.runbook}"
    mime_type = "text/markdown"
  }

  conditions {
    display_name = each.key
    condition_prometheus_query_language {
      query               = each.value.expr
      duration            = each.value.for
      evaluation_interval = "60s"
      labels              = each.value.labels
      rule_group          = "agenticorg-governance"
      alert_rule          = each.key
    }
  }

  notification_channels = var.notification_channels
}
