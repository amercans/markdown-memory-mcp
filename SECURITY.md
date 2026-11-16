# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead:
**Security -> Report a vulnerability** on this repository
(<https://github.com/amercans/vault-recall/security/advisories/new>).

Include what you found, how to reproduce it and the version you tested
(`vault-recall --version`). You can expect an acknowledgement within a week.

## Scope

Of most interest:

- a way for an MCP client to read or write a file outside the vault, or to write when the server
  was not started with `--allow-write`;
- a way to make `write_note` change an index file or replace a note without `overwrite`;
- a note or prompt that makes the hook block, crash a prompt or print anything other than the
  documented JSON;
- note content leaving the machine.

The threat model and known limitations are in [ARCHITECTURE.md](ARCHITECTURE.md#threat-model)
and under "Limitations" in the README.

## Supported versions

Only the latest release receives fixes.
