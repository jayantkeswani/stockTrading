#!/bin/bash
set -e

PROJECT_ID="stock-trading-prod"
REGION="asia-south1"
BUCKET_NAME="${PROJECT_ID}-terraform-state"

echo "=== Stock Trading — Terraform Bootstrap ==="
echo "Project: $PROJECT_ID"
echo "Region:  $REGION"
echo "Bucket:  gs://$BUCKET_NAME"
echo ""

echo "Activating gcloud config..."
gcloud config configurations activate stock-trading

echo ""
echo "Enabling required APIs..."
for api in cloudresourcemanager.googleapis.com iam.googleapis.com storage.googleapis.com compute.googleapis.com apikeys.googleapis.com generativelanguage.googleapis.com; do
  echo "  Enabling $api..."
  gcloud services enable "$api" --project=$PROJECT_ID
done

echo ""
echo "Creating Terraform state bucket..."
gcloud storage buckets create gs://$BUCKET_NAME \
  --project=$PROJECT_ID \
  --location=$REGION \
  --uniform-bucket-level-access 2>/dev/null || echo "Bucket already exists, continuing."

echo "Enabling versioning on state bucket..."
gcloud storage buckets update gs://$BUCKET_NAME --versioning

echo ""
echo "Bootstrap complete."
echo ""
echo "Next steps:"
echo "  cd infrastructure/terraform"
echo "  terraform init"
echo "  terraform plan"
echo "  terraform apply"
echo ""
echo "After apply, retrieve sensitive outputs:"
echo "  terraform output -raw vm_external_ip"
echo "  terraform output -raw deploy_private_key"
echo "  terraform output -raw gemini_api_key"
