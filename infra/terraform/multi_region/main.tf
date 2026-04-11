###############################################################################
# Multi-region active-passive scaffold for AgenticOrg
#
# Scope:
#   * Primary region:   asia-south1 (Mumbai)     — active
#   * Secondary region: asia-south2 (Delhi)      — standby
#
#   We provision a second GKE cluster, a Cloud SQL cross-region replica,
#   a regional Cloud Storage bucket, and a Cloud DNS record that can be
#   flipped to fail over.
#
#   This is NOT a hot-hot setup. Writes always go to the primary Cloud SQL
#   instance; the replica lags by ~5 seconds. On failover the SRE runs the
#   promotion runbook (docs/BACKUP_AND_DR.md) to turn the replica into a
#   primary, then updates the DNS record via `gcloud dns ...`.
#
# This file is a scaffold — it is intentionally commented-out for safety
# so applying it doesn't create real infrastructure without deliberate
# review. Run `terraform plan` with the variables below set before a drill.
###############################################################################

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.20.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.primary_region
}

provider "google" {
  alias   = "secondary"
  project = var.project_id
  region  = var.secondary_region
}

variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "primary_region" {
  type    = string
  default = "asia-south1"
}

variable "secondary_region" {
  type    = string
  default = "asia-south2"
}

# ---------------------------------------------------------------------------
# GKE — secondary cluster
# ---------------------------------------------------------------------------

# resource "google_container_cluster" "secondary" {
#   provider = google.secondary
#   name     = "agenticorg-prod-gke-dr"
#   location = var.secondary_region
#
#   remove_default_node_pool = true
#   initial_node_count       = 1
#
#   min_master_version = "1.29"
#
#   release_channel {
#     channel = "REGULAR"
#   }
#
#   workload_identity_config {
#     workload_pool = "${var.project_id}.svc.id.goog"
#   }
# }
#
# resource "google_container_node_pool" "secondary_default" {
#   provider   = google.secondary
#   name       = "default"
#   cluster    = google_container_cluster.secondary.name
#   location   = var.secondary_region
#   node_count = 2
#
#   node_config {
#     machine_type = "e2-standard-4"
#     disk_size_gb = 100
#   }
#
#   autoscaling {
#     min_node_count = 2
#     max_node_count = 20
#   }
# }

# ---------------------------------------------------------------------------
# Cloud SQL — cross-region read replica
# ---------------------------------------------------------------------------

# resource "google_sql_database_instance" "replica" {
#   provider         = google.secondary
#   name             = "agenticorg-prod-replica"
#   database_version = "POSTGRES_16"
#   region           = var.secondary_region
#
#   master_instance_name = "agenticorg-prod"
#
#   settings {
#     tier              = "db-custom-4-16384"
#     availability_type = "ZONAL"  # replicas are zonal; we promote on failover
#     backup_configuration {
#       enabled            = false  # primary does the backups
#       binary_log_enabled = false
#     }
#     ip_configuration {
#       ipv4_enabled    = true
#       private_network = var.private_network
#     }
#   }
#
#   replica_configuration {
#     failover_target = false
#   }
# }

# ---------------------------------------------------------------------------
# Cloud Storage — dual-region buckets for backups and invoices
# ---------------------------------------------------------------------------

# resource "google_storage_bucket" "dr_artifacts" {
#   name     = "agenticorg-dr-artifacts"
#   location = "ASIA"  # dual-region: asia-south1 + asia-south2
#
#   versioning {
#     enabled = true
#   }
#
#   lifecycle_rule {
#     condition {
#       age = 90
#     }
#     action {
#       type = "SetStorageClass"
#       storage_class = "NEARLINE"
#     }
#   }
# }

# ---------------------------------------------------------------------------
# Cloud DNS — failover-capable A record for api.agenticorg.ai
# ---------------------------------------------------------------------------

# resource "google_dns_record_set" "api_primary" {
#   managed_zone = "agenticorg-ai"
#   name         = "api.agenticorg.ai."
#   type         = "A"
#   ttl          = 60
#
#   routing_policy {
#     primary_backup {
#       primary {
#         internal_load_balancers {
#           load_balancer_type = "regionalL4ilb"
#           ip_address         = var.primary_lb_ip
#           ip_protocol        = "tcp"
#           port               = "443"
#           network_url        = var.network_url
#           project            = var.project_id
#           region             = var.primary_region
#         }
#       }
#       backup_geo {
#         location = var.secondary_region
#         rrdatas  = [var.secondary_lb_ip]
#       }
#     }
#   }
# }

# Outputs intentionally commented until the scaffold is un-commented.
# output "secondary_cluster_endpoint" {
#   value = google_container_cluster.secondary.endpoint
# }
