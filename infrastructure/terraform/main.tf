terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  backend "gcs" {
    bucket = "stock-trading-prod-terraform-state"
    prefix = "terraform/state"
  }
}

provider "google" {
  project                = var.project_id
  region                 = var.region
  zone                   = var.zone
  user_project_override  = true
  billing_project        = var.project_id
}
