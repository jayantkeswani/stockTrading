variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "stock-trading-prod"
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "asia-south1"
}

variable "zone" {
  description = "GCP zone"
  type        = string
  default     = "asia-south1-c"
}

variable "machine_type" {
  description = "GCE VM machine type"
  type        = string
  default     = "e2-medium"
}

variable "disk_size_gb" {
  description = "Boot disk size in GB"
  type        = number
  default     = 30
}

variable "github_owner" {
  description = "GitHub username or org"
  type        = string
  default     = "jayantkeswani"
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "stockTrading"
}
