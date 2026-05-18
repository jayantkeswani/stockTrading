resource "google_compute_firewall" "allow_http" {
  name    = "stock-trading-allow-http"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["http-server"]

  depends_on = [google_project_service.compute]
}

resource "google_compute_firewall" "allow_ssh" {
  name    = "stock-trading-allow-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["stock-trading"]

  depends_on = [google_project_service.compute]
}
