# Maintainer notes — aota-restricted-shell (outside normal runtime)

- Full command catalog details (`/bin/echo` max_args/options, `/bin/ls` path-capable, `/bin/sleep` numeric), env allowlist (`PATH`, `HOME`, `LANG`, etc., dangerous `LD_PRELOAD`/`GH_TOKEN` never inherited), output truncation accounting, `ToolResultGovernance` projection preserved for maintainers.
- Network truth (`NEW_NETWORK_SUBSYSTEM_CREATED=no`, no OS isolation claim, `fail_closed_no_network_commands`) preserved.
- `MAINTAINER_CONTENT_IN_NORMAL_LLM_CONTEXT=no`.
