import os

os.environ.update(
    {
        "APP_ENV": "test",
        "AIRTABLE_PAT": "test-token",
        "AIRTABLE_BASE_ID": "app-test",
        "ADMIN_USERNAME": "admin-test",
        "ADMIN_PASSWORD_HASH": "test-only-hash",
        "JWT_SECRET": "test-secret-at-least-32-characters-long",
        "JWT_ISSUER": "shajra-test",
        "JWT_AUDIENCE": "shajra-admin-test",
        "PUBLIC_WRITES_ENABLED": "false",
        "RELATIONSHIP_WRITES_ENABLED": "false",
        "NORMALIZED_READS_ENABLED": "false",
    }
)
