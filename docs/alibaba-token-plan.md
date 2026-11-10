# Alibaba Personal Token Plan

This adapter calls the official Bailian CLI, `bl usage token-plan --output json`.
It reads the selected CLI console account's provider-reported quota, not Hermes
request counts. It does not make inference requests or read credential files.

## Setup

Install and authenticate the official CLI using Alibaba's
[installation guide](https://help.aliyun.com/zh/model-studio/cli/installation).
A current CLI with monthly-window support is required; bl 2.8.4 was verified.
Personal usage needs a console login, `bl auth login --console`. An inference
API key alone does not authenticate every console command.

Check `bl usage token-plan --output json` before enabling this adapter. Confirm
the usage and reset time match the intended account and region in the console.
The adapter follows the CLI's active account/config; it does not change it.

Enable only in the intended Hermes profile:

```bash
hermes config set plugins.entries.quota.settings.alibabaTokenPlanEnabled true
# Optional if bl is not on the gateway's PATH. Supply your trusted absolute path.
hermes config set plugins.entries.quota.settings.bailianCliPath /absolute/path/to/bl
hermes quota refresh
hermes quota provider alibaba-token-plan
```

`/quota alibaba` and `/quota alibaba-token-plan-cn` filter the same provider.
The `-cn` filter alias does not select a different CLI region. Use the correct
Bailian login/config for that. A named profile uses `hermes -p PROFILE ...`.

The CLI executable must already be installed. The adapter never installs it,
opens a login, refreshes tokens itself, or changes the CLI configuration.
Collection defaults off. The Desktop provider-display toggle only controls
visibility; `alibabaTokenPlanEnabled` controls backend collection.

## Quota semantics

- `per1MonthPercentage: 0.125` means 12.5% used, not 0.125% used.
- Every valid reported Hour/Day/Week/Month window gets its own bar. Monthly-only
  responses do not acquire invented 5-hour or weekly bars.
- Resets come from `per...ResetTime` in Unix milliseconds, converted to UTC ISO.
  No calendar-month reset date is inferred.
- `hasQuotaData: false`, missing usage, or invalid ratios report `no-data`.
  Missing data never becomes a zero-used or fully-remaining allowance.
- This endpoint does not provide a purchased tier or a Credits denominator.
  Neither is guessed. Add-on bundles and Harness benefits are not merged into
  the plan percentage. This version does not query Team seats or legacy Coding
  Plan request quotas, and does not support a separate Qwen Cloud platform.
- The displayed data belongs to the CLI login. It is not matched automatically
  to the inference API key Hermes uses. Verify the account when switching logins.

## Execution and errors

The adapter invokes a fixed argument list without a shell, from the user's home.
It passes only home/config discovery, trusted absolute PATH entries, locale,
timezone, temporary-directory, and proxy settings. Ambient API keys, cookies,
Node options, cloud credentials, and SSH-agent state are not inherited.
Relative/project PATH directories and discovered symlink targets into the
current project are excluded. An explicitly configured absolute executable is
an operator trust decision. Shell launchers are rejected.

Stdout is capped at 256 KiB plus one overflow-detection byte; stderr is discarded.
A 12-second shared deadline bounds the read and process wait, followed by a
bounded cleanup grace. POSIX timeouts/overflow kill the process group. Windows
cleanup kills the direct child only; POSIX execution was live-verified, Windows
execution has not been validated. npm `.cmd` launchers are not supported.

Machine-readable unavailable states include `opt-in-disabled`, `cli-not-found`,
`config-invalid`, `not-logged-in`, `network-error`, `timeout`, `output-too-large`,
`cli-error`, `bad-json`, `bad-response`, and `no-data`. Raw CLI output or error
messages never enter the display cache.

The existing cache and Desktop refresh cadence are reused. After installing
new Python code, a running Desktop backend may need a restart when idle. Fresh
`hermes quota` CLI calls load the installed provider immediately. No other
profiles need to be enabled or changed.

## Fork scope

Version 3.2.0 in this fork is based on upstream v3.0.0 to preserve the installed
baseline. It does not claim to include upstream's v3.1.0 UI redesign. The original
installer affects all existing profiles; do not run it for a default-only update.

## References

- [Official usage command](https://www.alibabacloud.com/help/en/model-studio/cli/usage-quota)
- [Official Personal Token Plan FAQ](https://help.aliyun.com/zh/model-studio/token-plan-personal-faq)
- [Official CLI implementation](https://github.com/modelstudioai/cli/blob/main/packages/commands/src/commands/usage/token-plan.ts)

The adapter is an independent implementation using the documented CLI contract.
The inline Alibaba Cloud mark comes from simple-icons, CC0.
