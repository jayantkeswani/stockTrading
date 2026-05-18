output "vm_external_ip" {
  description = "Static external IP of the VM"
  value       = google_compute_address.static_ip.address
}

output "deploy_private_key" {
  description = "SSH private key for deploy user (add to GitHub Secrets as SSH_PRIVATE_KEY)"
  value       = tls_private_key.deploy.private_key_pem
  sensitive   = true
}

output "deploy_public_key" {
  description = "SSH public key (already added to VM metadata)"
  value       = tls_private_key.deploy.public_key_openssh
}

output "gemini_api_key" {
  description = "Gemini API key (add to GitHub Secrets as GOOGLE_API_KEY)"
  value       = google_apikeys_key.gemini.key_string
  sensitive   = true
}

output "service_account_email" {
  description = "VM service account email"
  value       = google_service_account.vm.email
}
