# Backup & Recovery

Practical backup/restore for the single-VM deployment. **These procedures are
provided and scripted, but backups are only real once you have tested a restore.**
Do not assume an untested backup will restore.

## What holds state

| Store   | Role                                              | Backed up | How                         |
| ------- | ------------------------------------------------- | --------- | --------------------------- |
| MongoDB | Durable: agent **checkpoints** (resume) + app data| Yes       | `mongodump` (logical)       |
| Qdrant  | Durable: vector index (document chunks)           | Yes       | volume snapshot (tar)       |
| MinIO   | Durable: uploaded documents (object storage)      | Yes       | volume snapshot (tar)       |
| Redis   | **Ephemeral**: job queue / cache                  | No        | rebuilt on restart          |
| Caddy   | ACME certificates                                 | No*       | re-issued automatically     |

Redis is treated as ephemeral: in production, agent state lives in Mongo
(`AGENT_CHECKPOINT_BACKEND=mongo`), and the ingest job queue is transient. A lost
Redis means at most re-queuing in-flight ingest jobs. Caddy certs re-issue on
demand, so they are not part of data backup (the `runner_caddy_data` volume just
avoids hitting ACME rate limits on frequent restarts).

## Scripts

```bash
./scripts/backup.sh                 # -> ./backups/<UTC-timestamp>/
BACKUP_DIR=/mnt/backups ./scripts/backup.sh
./scripts/restore.sh ./backups/<timestamp>   # guarded, destructive
```

`backup.sh` writes `mongo.archive.gz`, `qdrant.tgz`, `minio.tgz`, and a
`MANIFEST.txt` (timestamp + deployed commit). It **does not** back up `.env` —
secrets must be backed up **separately and encrypted** by you.

## Recommendations

- **Frequency**: daily for a demo/low-write deployment; hourly Mongo dumps if
  checkpoint volume is high. Automate with cron calling `backup.sh`.
- **Location**: off the VM (object storage / another host). A backup on the same
  disk does not survive disk loss.
- **Encryption**: encrypt backups at rest (they contain document contents and
  checkpoint data). E.g. pipe through `age`/`gpg`, and always encrypt the `.env`.
- **Retention**: keep N daily + M weekly; prune older.

## Restore procedure

1. Provision the VM and bring the stack up once (`./scripts/deploy.sh`) so the
   named volumes exist.
2. `./scripts/restore.sh ./backups/<timestamp>` — it restores Mongo (logical,
   `--drop`), then stops Qdrant + MinIO, replaces their volume contents, and
   restarts.
3. Verify: `./scripts/smoke-test.sh`.

## Consistency caveats

- The three stores are backed up **sequentially, not atomically**. For a
  crash-consistent snapshot, stop the stack (`./scripts/stop.sh`) before
  `backup.sh`. For a low-write demo the live backup is fine.
- Qdrant/MinIO restores require their services stopped (the script handles this).
- **Checkpoints are forward-only.** Restoring an older Mongo snapshot revives
  only the waiting runs present at snapshot time; runs created after the snapshot
  are lost, and a client holding a newer `checkpoint_id` will get a `404` on
  resume (it starts a new run). This is expected — resume is best-effort across a
  restore, not guaranteed.

## Rollback vs. restore

- **Rollback** (`./scripts/rollback.sh`) changes *code* to a previous commit and
  rebuilds; data volumes are untouched.
- **Restore** changes *data* to a previous snapshot; code is untouched.

A schema-incompatible code rollback should be paired with a data restore from
the matching period. In practice V2 checkpoints are additive, so a code rollback
alone is usually safe (old waiting runs simply expire).
