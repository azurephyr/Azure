# Live Staging Certification

The repository includes a guarded live test runner at
`scripts/live_staging_certification.py`. It is intentionally separate from
normal bot startup.

## Offline Plan

```bash
python scripts/live_staging_certification.py --guild-id <staging-guild-id>
```

This does not connect to Discord or mutate anything.

## Safe Live Run

Use only a disposable staging guild where the bot is allowed to create and
delete resources prefixed with `azure-cert-`:

```bash
set AZURE_LIVE_TEST_GUILD_ID=<staging-guild-id>
python scripts/live_staging_certification.py --guild-id <staging-guild-id> --execute --report live-report.json
```

The runner requires the configured `AZURE_DISCORD_TOKEN`, checks the bot's
permissions, creates/edits/deletes a role, category, channel, permission
overwrite, and message, and verifies that no prefixed resources remain.

It never performs bans, kicks, timeouts, mass deletion, or server-wide
permission changes. Do not run it against a production guild.
