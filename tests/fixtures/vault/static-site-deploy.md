---
name: static-site-deploy
description: Deploy a small static site with rsync, always after a dry run
type: reference
---

# Static site deploy

Build locally, run `rsync --dry-run` against the web root, read the list of deletions, then
run it for real. Cache rules are in [[nginx-cache-headers]]. The old landing page copy is in
[[missing-description]].
