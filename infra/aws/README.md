# Hosted Archie security baseline

Provision these resources in `ap-southeast-2` before setting `ARCHIE_ENV=production`:

- Cognito User Pool with hosted OAuth (authorisation code + PKCE), required TOTP MFA, and an app client whose callback/logout URLs are the HTTPS Archie origin.
- Private, versioned S3 bucket encrypted with a customer-managed KMS key. Block all public access; only the Archie worker/service role may read or write objects.
- PostgreSQL in private subnets for users, projects, memberships, jobs, uploads, and audit events.
- ECS/Fargate tasks running non-root behind an HTTPS ALB and AWS WAF. Put the frontend behind CloudFront with an Origin Access Control for S3.
- Secrets Manager entries for `DATABASE_URL`, Cognito configuration, and provider credentials. Do not place any secret in a task definition, repository, browser response, report, or export.

The current file-backed backend is intentionally refused in production until its storage adapter is replaced with private S3/PostgreSQL persistence. This prevents accidental public deployment of local project data.

Required production environment variables:

```text
ARCHIE_ENV=production
ARCHIE_PUBLIC_ORIGIN=https://app.example.com
ARCHIE_COGNITO_REGION=ap-southeast-2
ARCHIE_COGNITO_USER_POOL_ID=...
ARCHIE_COGNITO_CLIENT_ID=...
DATABASE_URL=postgresql://...
ARCHIE_S3_BUCKET=archie-production-artifacts
```
