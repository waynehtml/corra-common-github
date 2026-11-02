"""Authentication helpers for acquiring Azure tokens in Databricks."""

import json
import requests
# Following library is not available on serverless compute
from azure.identity import DefaultAzureCredential, ClientSecretCredential


class AuthUtils:
    """Utility wrapper for retrieving Azure access tokens via client credentials."""

    tenant_id: str = None
    client_id: str = None
    client_secret: str = None

    credentials: ClientSecretCredential = None

    def __init__(
        self, tenant_id: str, client_id: str, client_secret: str
    ):
        """Initialize the client credential flow from Databricks secrets.

        Args:
            tenant_id (str): Secret key storing the Azure tenant ID.
            client_id (str): Secret key storing the Azure client ID.
            client_secret (str): Secret key storing the Azure client secret.
        """
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret

        self.credentials = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )

    def get_storage_token(self):
        """Get an access token for Azure Storage."""
        return self.credentials.get_token("https://storage.azure.com/.default").token

    def get_keyvault_token(self):
        """Get an access token for Azure Key Vault."""
        return self.credentials.get_token("https://vault.azure.net/.default").token

    def get_graph_token(self):
        """Get an access token for Microsoft Graph."""
        return self.credentials.get_token("https://graph.microsoft.com/.default").token

    def get_sql_token(self):
        """Get an access token for Azure SQL."""
        return self.credentials.get_token("https://database.windows.net/.default").token
    
    def get_token_with_scope(self, scope: str) -> str:
        """Request an access token for an arbitrary scope.

        This helper mirrors ``az account get-access-token --scope <scope>`` and ensures the
        ``/.default`` suffix is present.

        Args:
            scope: Resource scope URL (with or without the ``/.default`` suffix).

        Returns:
            A bearer token string for the requested scope.
        """
        if not scope.endswith("/.default"):
            scope = f"{scope}/.default"
        token = self.credentials.get_token(scope)
        return token.token
