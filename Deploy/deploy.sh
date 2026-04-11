#!/bin/bash
set -e

# ── Config (edit these) ──
RESOURCE_GROUP="azure-hacker-rg"
LOCATION="eastus"
ACR_NAME="azurehackerregistry"
ACI_NAME="azure-hacker-worker"
IMAGE="azurehackerregistry.azurecr.io/azure-hacker-worker:latest"
STORAGE_ACCOUNT="azurehackerstorage"
FILE_SHARE="worker-output"

# ── Create resource group ──
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION"

# ── Create ACR (if not exists) ──
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled true

# ── Get ACR credentials ──
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" -o tsv)

# ── Create storage account + file share for output ──
az storage account create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$STORAGE_ACCOUNT" \
  --location "$LOCATION" \
  --sku Standard_LRS

STORAGE_KEY=$(az storage account keys list \
  --resource-group "$RESOURCE_GROUP" \
  --account-name "$STORAGE_ACCOUNT" \
  --query "[0].value" -o tsv)

az storage share create \
  --name "$FILE_SHARE" \
  --account-name "$STORAGE_ACCOUNT" \
  --account-key "$STORAGE_KEY"

# ── Deploy container ──
az container create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACI_NAME" \
  --image "$IMAGE" \
  --registry-login-server "azurehackerregistry.azurecr.io" \
  --registry-username "$ACR_USERNAME" \
  --registry-password "$ACR_PASSWORD" \
  --cpu 1 \
  --memory 1.5 \
  --os-type Linux \
  --restart-policy Never \
  --command-line "python -m worker.platform.cli --config /config.json" \
  --azure-file-volume-account-name "$STORAGE_ACCOUNT" \
  --azure-file-volume-account-key "$STORAGE_KEY" \
  --azure-file-volume-share-name "$FILE_SHARE" \
  --azure-file-volume-mount-path "/outputs" \
  --secure-environment-variables \
    JOB_ID="deploy-$(date +%s)"

echo ""
echo "Deployed. Check logs with:"
echo "  az container logs --resource-group $RESOURCE_GROUP --name $ACI_NAME"
echo "  az container attach --resource-group $RESOURCE_GROUP --name $ACI_NAME"
