#!/bin/bash
# Refuses to publish anything that looks private. Scans tracked files and the whole git
# history for:
#   - IPv4 and MAC addresses outside the documentation ranges,
#   - e-mail addresses other than example domains and GitHub no-reply addresses,
#   - real home-directory paths and tracked symbolic links,
#   - private key material,
#   - every line of your own pattern list (case-insensitive substring match), kept OUTSIDE
#     the repository: $MARKDOWN_MEMORY_LEAK_PATTERNS or ~/.config/markdown-memory-mcp/forbidden-patterns.txt
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
LIST="${MARKDOWN_MEMORY_LEAK_PATTERNS:-$HOME/.config/markdown-memory-mcp/forbidden-patterns.txt}"
found=0
report() { echo "leak-scan: $1"; found=1; }

# Everything that will be published: current files, every patch in history, commit metadata.
content() {
  git ls-files -z | xargs -0 cat 2>/dev/null
  git log -p --all 2>/dev/null
  git log --all --format='%an %ae %cn %ce %B' 2>/dev/null
}
all="$(content)"

ips="$(printf '%s\n' "$all" | perl -ne 'print "$&\n" while /(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])/g' | sort -u \
  | grep -Ev '^(192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|0\.0\.0\.0$|127\.0\.0\.1$)')"
[ -n "$ips" ] && report "IPv4 addresses outside documentation ranges: $(echo "$ips" | tr '\n' ' ')"

macs="$(printf '%s\n' "$all" | perl -ne 'while (/(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,2}:){5}[0-9A-Fa-f]{1,2}(?![0-9A-Fa-f:])/g) { print join(":", map { sprintf "%02x", hex } split /:/, $&), "\n" }' | sort -u | grep -v '^00:00:5e:00:53:')"
[ -n "$macs" ] && report "MAC addresses outside the documentation range: $(echo "$macs" | tr '\n' ' ')"

mails="$(printf '%s\n' "$all" | perl -ne 'print lc($&), "\n" while /[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g' | sort -u \
  | grep -Ev '@(([a-z0-9-]+\.)*example\.(com|org|net)|users\.noreply\.github\.com)$')"
[ -n "$mails" ] && report "e-mail addresses: $(echo "$mails" | tr '\n' ' ')"

# Real home-directory paths. Placeholders such as /Users/... or /home/user are fine.
paths="$(printf '%s\n' "$all" | perl -ne 'print "$&\n" while m{/(?:Users|home)/[A-Za-z][A-Za-z0-9._-]*}g' | sort -u \
  | grep -Ev '^/(Users|home)/(user|username|name|you|example|runner)$')"
[ -n "$paths" ] && report "home-directory paths: $(echo "$paths" | tr '\n' ' ')"

# Symlinks can point at files outside the repository.
links="$(git ls-files -s | awk '$1 == 120000 { print $4 }'; git log --all --raw --format= 2>/dev/null | awk '$2 == 120000 { print $6 }')"
[ -n "$links" ] && report "symbolic links: $(echo "$links" | sort -u | tr '\n' ' ')"

printf '%s\n' "$all" | grep -q -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' && report "private key material"

if [ -f "$LIST" ]; then
  # grep -i does not fold the Turkish capital dotted I (U+0130) to "i". Map it to "I" first so
  # a pattern written with plain letters still matches text that uses it.
  hits="$(printf '%s\n' "$all" | perl -CSD -pe 'tr/\x{130}/I/' \
    | grep -ioF -f <(grep -v '^[[:space:]]*$' "$LIST" | grep -v '^#') | tr 'A-Z' 'a-z' | sort -u)"
  [ -n "$hits" ] && report "forbidden patterns: $(echo "$hits" | tr '\n' ' ')"
else
  echo "leak-scan: note - no private pattern list at $LIST"
fi

[ "$found" = 0 ] && echo "leak-scan: clean"
exit "$found"
