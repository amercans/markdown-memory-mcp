---
name: backup-before-migrations-old
description: Take a database snapshot and verify it restores before running schema migrations
type: feedback
---

# Backup before migrations (old copy)

Before any schema migration, take a snapshot of the database and restore it into a scratch
instance to confirm that it works. Only then run the migration. A dropped column cannot be
undone from the application, and an untested snapshot is not a backup.
