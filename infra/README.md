# infra/

**Owner:** Jack. `setup.py` creates everything AgentPass needs in `us-east-1` with boto3, idempotently:

- DynamoDB tables `ap_mandates`, `ap_sessions`, `ap_revocations`, `ap_store`, `ap_audit` (on-demand billing)
- KMS asymmetric key `alias/agentpass-token-signing` (ECC_NIST_P256, SIGN_VERIFY) for ES256 passports
- Cognito user pool `agentpass-users` with app client `agentpass-dashboard` (USER_PASSWORD_AUTH, no secret) and the demo user with a permanent password

```bash
source ~/.aws_workshop_env
python -m infra.setup --demo-user parent --demo-password '<strong password>'
```

It prints and writes the ids to `.env` (gitignored): `AGENTPASS_MODE=aws`, `AGENTPASS_KMS_KEY_ID`,
`COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`. Credentials are never written anywhere.
