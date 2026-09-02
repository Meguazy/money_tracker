# Container Versions and Controlled Upgrades

- Status: implemented Step 1 baseline
- Last verified: 2026-09-01
- Source of truth: `compose.yaml`

## Pinned images

| Service | Version selector | Content digest |
| --- | --- | --- |
| Application PostgreSQL | `postgres:17` | `sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675` |
| Langfuse web | `langfuse:4.26.0` | `sha256:7dcc1419da9062c968a428bc63a4ff0e03d4cc84113125f999754a9037912425` |
| Langfuse worker | `langfuse-worker:4.26.0` | `sha256:9c4da1802500c7ae987e4af1bfb3e1a441f0bff024c2d7a2a5541fa4691155ae` |
| Langfuse PostgreSQL | `postgres:17` | `sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675` |
| ClickHouse | `clickhouse-server:25.12` | `sha256:8a790dd3468db22b1d4e7b18a176f378ff5ff6053b9c48dd4ea1fa71a24c5ba6` |
| Redis | `redis:7` | `sha256:71da9275c5f3fcb97d0fa0c8c5b36cc995327265420f17a04bfd544f458059f7` |
| MinIO | `chainguard/minio:latest` | `sha256:1e408150bcf1bff07a2e468b9ac91347d4da261258921faeaedbe2ed7f0818b4` |

The human-readable selector documents the intended release line; the digest
makes each deployment immutable. A mobile selector such as MinIO's `latest`
does not cause an implicit upgrade while its digest remains unchanged.

## Verification evidence

The installed images were resolved with Docker image inspection and then tested
through the checked-in Compose configuration. The application database passed
its health check, real role/migration/checkpoint integration suite, rollback on
an empty foundation, and fresh bootstrap. The observability profile completed
the non-cluster ClickHouse migrations and passed web, worker, PostgreSQL,
ClickHouse, Redis, and MinIO health checks without deleting or replacing the
pre-existing Langfuse volumes.

## Controlled upgrade

1. Review upstream release notes, schema compatibility, security notices, and
   rollback limitations for the exact candidate versions.
2. Pull the explicit candidate tags, record their resolved content digests, and
   update both tag and digest in `compose.yaml`.
3. Before a stateful upgrade, identify the exact owned volumes and create the
   backup required by that service. Do not use a volume deletion as a migration
   strategy.
4. Validate both Compose profiles, start the candidate services with
   `--wait`, inspect migrations and logs, and exercise their documented health
   endpoints.
5. Run the application PostgreSQL integration suite and all service-independent
   checks. For Langfuse, verify that an existing project and its retained traces
   remain readable.
6. Update this table, the verification date, architecture/deployment views, and
   any changed recovery behavior in the same change.

## Rollback

Return `compose.yaml` to the previously recorded digests only when upstream
schema compatibility permits it. Container rollback never implies database
rollback. If a migration is incompatible, follow the service's supported
recovery procedure and restore a verified backup into an isolated target before
changing the active volumes. Preserve the current volumes until the restored
state has been verified.
