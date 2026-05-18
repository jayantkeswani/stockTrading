resource "google_apikeys_key" "gemini" {
  name         = "gemini-api-key"
  display_name = "Stock Trading Gemini API Key"
  project      = var.project_id

  restrictions {
    api_targets {
      service = "generativelanguage.googleapis.com"
    }
  }

  depends_on = [
    google_project_service.apikeys,
    google_project_service.generativelanguage,
  ]
}
