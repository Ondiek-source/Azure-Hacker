# deploy.ps1 — Deploy Azure Hacker portal to Azure Container Instances

$RESOURCE_GROUP = "azure-hacker-rg"
$ACR_NAME = "azurehackerregistry"
$STORAGE_ACCOUNT = "azurehackerstorage"
$FILE_SHARE = "worker-output"
$CONTAINER_NAME = "azure-hacker-portal"
$IMAGE = "azurehackerregistry.azurecr.io/azure-hacker-worker:latest"
$DNS_LABEL = "azure-hacker"

# Get credentials
$STORAGE_KEY = az storage account keys list --resource-group $RESOURCE_GROUP --account-name $STORAGE_ACCOUNT --query "[0].value" -o tsv
$ACR_PASSWORD = az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv

# Delete old container if exists
az container delete --resource-group $RESOURCE_GROUP --name $CONTAINER_NAME --yes 2>$null

# Deploy web portal
az container create `
    --resource-group $RESOURCE_GROUP `
    --name $CONTAINER_NAME `
    --image $IMAGE `
    --dns-name-label $DNS_LABEL `
    --registry-login-server "$ACR_NAME.azurecr.io" `
    --registry-username $ACR_NAME `
    --registry-password $ACR_PASSWORD `
    --cpu 2 `
    --memory 3.5 `
    --os-type Linux `
    --restart-policy Always `
    --ip-address public `
    --ports 8000 `
    --azure-file-volume-account-name $STORAGE_ACCOUNT `
    --azure-file-volume-account-key $STORAGE_KEY `
    --azure-file-volume-share-name $FILE_SHARE `
    --azure-file-volume-mount-path "/outputs"

# Get the FQDN
$FQDN = az container show --resource-group $RESOURCE_GROUP --name $CONTAINER_NAME --query "ipAddress.fqdn" -o tsv

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Portal deployed!" -ForegroundColor Green
Write-Host "  URL: http://${FQDN}:8000" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Check logs with:" -ForegroundColor Yellow
Write-Host "  az container logs --resource-group $RESOURCE_GROUP --name $CONTAINER_NAME" -ForegroundColor Yellow
