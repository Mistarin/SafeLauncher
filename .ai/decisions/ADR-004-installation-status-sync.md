# ADR-004: Installation State Is Private and Device-Specific

## Decision

Synchronize enough private library metadata for cloud-only/not-installed records and account-wide stats to appear across devices, while treating playable installation/path state as device-local.

## Consequence

An installed device can show a game as playable while another shows it as not installed. Installing later must attach to the existing stable identity and retain stats.
