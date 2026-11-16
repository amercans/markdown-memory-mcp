---
name: nginx-cache-headers
description: Cache-control headers for static assets served by nginx
type: reference
---

# Nginx cache headers

Fingerprinted assets get `Cache-Control: public, max-age=31536000, immutable`. HTML gets
`no-cache` so a new deploy shows up on the next request. Compression is covered in
[[nginx-gzip-settings]].

The reload helper lives at `~/does-not-exist/tool.sh`. The hosts file is `/etc/hosts`.

Links inside code are ignored:

```
[[ignored-link-in-code]]
```

Same for an inline span such as `[[also-ignored]]`.
