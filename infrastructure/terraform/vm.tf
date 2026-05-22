resource "tls_private_key" "deploy" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "google_service_account" "vm" {
  account_id   = "stock-trading-vm"
  display_name = "Stock Trading VM Service Account"

  depends_on = [google_project_service.compute]
}

resource "google_project_iam_member" "vm_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.vm.email}"

  depends_on = [google_project_service.aiplatform]
}

resource "google_compute_address" "static_ip" {
  name   = "stock-trading-ip"
  region = var.region

  depends_on = [google_project_service.compute]
}

resource "google_compute_instance" "app" {
  name         = "stock-trading-vm"
  machine_type = var.machine_type
  zone         = var.zone

  tags = ["stock-trading", "http-server"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = var.disk_size_gb
      type  = "pd-ssd"
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.static_ip.address
    }
  }

  metadata = {
    ssh-keys = "deploy:${tls_private_key.deploy.public_key_openssh}"
  }

  metadata_startup_script = file("${path.module}/../scripts/vm-startup.sh")

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  allow_stopping_for_update = true

  lifecycle {
    ignore_changes = [metadata_startup_script]
  }

  depends_on = [
    google_project_service.compute,
  ]
}
