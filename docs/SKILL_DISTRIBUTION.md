# Standalone Agent Skill

The canonical instructions and MIT notice are in `deepsee/skill/`. The existing
`SKILL_MD` Python symbol reads that file; wheels include both files. Runtime
architecture, CLI commands and MCP tools are unchanged. This is an instruction
package with an external runtime dependency, not a hosted vision service.

## Installation

Review and check out a trusted commit from https://github.com/rikfish163-rgb/deepsee,
then install its runtime with `python -m pip install .` (Python >=3.9). Optional
`.[image]` adds Pillow; `.[ocr]` adds local OCR dependencies. No MCP SDK is required
for the built-in stdio server. Use `deepsee setup` to configure a provider privately.
Do not publish the resulting config, caches, history, screenshots or API keys.

Copy the contents of `deepsee/skill/` into a directory named `deepsee` at the
appropriate destination. Review existing files before replacing them.

| Client | Skill destination | MCP (optional, separate registration) |
| --- | --- | --- |
| Codex | `deepsee install --skill`; respects `CODEX_HOME`, defaults to `~/.codex` | existing client stdio registration |
| Claude Code | `~/.claude/skills/deepsee/` or project `.claude/skills/deepsee/` | `claude mcp add --scope user deepsee -- /absolute/path/to/deepsee mcp` |
| Cursor | project `.cursor/skills/deepsee/` | merge server entry below into `.cursor/mcp.json` |
| Copilot | project `.github/skills/deepsee/` or personal `~/.copilot/skills/deepsee/` | VS Code uses `servers`, not `mcpServers`; other Copilot clients differ |

Cursor/Claude JSON server entry (merge; never replace unrelated entries):

```json
{"mcpServers":{"deepsee":{"command":"/absolute/path/to/deepsee","args":["mcp"],"env":{"DEEPSEE_HOME":"/absolute/private/deepsee-profile"}}}}
```

VS Code `.vscode/mcp.json` equivalent:

```json
{"servers":{"deepsee":{"type":"stdio","command":"/absolute/path/to/deepsee","args":["mcp"],"env":{"DEEPSEE_HOME":"/absolute/private/deepsee-profile"}}}}
```

Examples are templates requiring local paths, not automatically installed configs.
Keep secrets out of shared client configuration. `DEEPSEE_HOME` isolates deepsee
configuration/storage, not filesystem/network access: this runtime is not a sandbox.
Review provider fallbacks; images/questions can leave the machine. Skill safety
instructions are agent guidance, not enforced access controls. Installing the
skill does not enable hooks, proxy, web panel or MCP. Do not use bare `deepsee
install` for a skill-only installation, because its default installs all integrations.

Native GUI discovery and authenticated remote vision were not tested here. Cloud
agents require the runtime and images in their own environment; a local stdio
server is not automatically available remotely. Use CLI when MCP is unavailable.

## Packaging and checks

The distribution allowlist is exactly `SKILL.md` and `LICENSE`; no user data,
configuration, executable payloads or credentials belong in the skill archive.
From the repository root, the following creates a submission candidate locally:

```python
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
with ZipFile('deepsee-skill.zip', 'w', ZIP_DEFLATED) as archive:
    for name in ('SKILL.md', 'LICENSE'):
        archive.write(Path('deepsee/skill') / name, 'deepsee/' + name)
```

Run `python -m pytest -q`. Distribution tests cover isolated install/uninstall,
repeat install, documented tool schemas, NDJSON stdio initialize/list/status and
credential non-disclosure in status. Existing tests exercise mocked vision calls.
Demo mode is synthetic and is not an accuracy evaluation.

Client references (checked 2026-10-22):
- https://code.claude.com/docs/en/skills
- https://cursor.com/help/customization/skills
- https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills

## Agensi assessment (2026-10-22; no submission authorized)

Public sources: https://www.agensi.io/terms (updated October 18),
https://www.agensi.io/privacy (updated October 12),
https://www.agensi.io/security and https://www.agensi.io/sell .

The current terms require a ZIP with valid SKILL.md frontmatter, automated
8-point scanning and manual review. Official marketing says free listing and
24–48 hour review; neither is an independent audit or service guarantee.
Creators retain ownership and grant a non-exclusive worldwide distribution
licence. Fees are 30% of net sale price excluding sales tax; processing is included,
refunds reverse creator earnings, and the split may change with 30 days' notice.
Paid listings require an enabled payout method (Stripe Connect or Zoneless USDC).
Payout availability for the creator's country has not been established.

Crucially, current delivery is connector-only, with no buyer file download/save/
export and only one connected agent per account. This differs from independent
local installation and needs clarification for CLI/MCP runtime prerequisites.
Buyer restrictions and paid licence notices also need an explicit MIT exception;
no licence change or exclusivity is proposed by this repository change.

The privacy policy records agent search/load/use requests and retains usage logs
for two years; submitted ZIP contents and scan findings are collected. Do not send
credentials or images to the marketplace. Its scanning and security controls are
self-reported; no independent security audit, corporate registry validation or
email sender authentication was completed. Public LinkedIn search associates
Joeri Brons with Agensi, but this does not authenticate the Gmail invitation.

Recommendation: technically suitable for independent instruction distribution,
with runtime prerequisites. Do not accept commercial terms yet. First obtain
written clarification of MIT precedence, connector-only delivery of skills with
local dependencies, free listing conditions, takedown/exit handling, legal entity
registration and country-specific payout eligibility. A free discovery listing
could be useful after these are resolved; revenue projections are unverified.
