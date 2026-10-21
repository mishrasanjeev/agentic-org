# Usage and licensing

**AgenticOrg platform: [PolyForm Noncommercial License 1.0.0](LICENSE). Only self use is free.
Commercial use of any sort needs Orchestrum's prior approval and a written agreement with
Orchestrum Technologies LLP.** Agreements: [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in).

**AgenticOrg SDKs (`sdk/`, `sdk-ts/`) and the MCP server (`mcp-server/`): Apache License 2.0,
open source and free for any use, including commercial use.**

| Directory | Licence |
| --- | --- |
| `sdk/` (Python SDK) | Apache License 2.0: open source, free for any use including commercial |
| `sdk-ts/` (TypeScript SDK) | Apache License 2.0: open source, free for any use including commercial |
| `mcp-server/` (MCP server) | Apache License 2.0: open source, free for any use including commercial |
| `tests/fixtures/ucp/`, `core/commerce/acp/schemas/` (vendored protocol schemas) | Apache License 2.0 (third-party, unchanged) |
| Everything else: the AgenticOrg platform (`api/`, `core/`, `auth/`, `connectors/`, `ui/` and the website, workers, `docs/`, `scripts/`, `migrations/`, tests) | PolyForm Noncommercial License 1.0.0: commercial use needs a written agreement with Orchestrum Technologies LLP |

| Document | What it covers |
| --- | --- |
| [LICENSE](LICENSE) | The PolyForm Noncommercial License 1.0.0 text and the `Required Notice:` lines |
| [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) | What is free, what needs an agreement, how to get one |
| [TRADEMARKS.md](TRADEMARKS.md) | Use of the AgenticOrg and Grantex names and logos |
| [CONTRIBUTING.md](CONTRIBUTING.md#contributor-licence) | The terms contributions are accepted on |
| [licensing/scope.json](licensing/scope.json) | Machine-readable record of the licence, change date and third-party paths |
| [Licensing page](docs/user-guide/licensing.md) | The same terms on the public documentation site |

## Change date and earlier versions

The PolyForm Noncommercial License 1.0.0 applies to every version published on or after
21 October 2026, including all later updates. Versions published before that date remain
available under the Apache License 2.0 they were released with.

Existing and new users alike need a written agreement with Orchestrum Technologies LLP for any
commercial use of a platform version published on or after 21 October 2026. Commercial use of the
AgenticOrg or Grantex names, logos, hosted services or brand needs Orchestrum's prior written
approval with any version, including earlier ones.

## What each licence covers

The PolyForm Noncommercial License 1.0.0 covers the platform: all first-party material except
`sdk/`, `sdk-ts/` and `mcp-server/`, including the API, workers, UI and website, scripts,
migrations, tests and documentation. Its source files carry
`SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0`.

The Apache License 2.0 covers `sdk/`, `sdk-ts/` and `mcp-server/`; each has its own `LICENSE`
and its source files carry `SPDX-License-Identifier: Apache-2.0`.
`scripts/check_license_headers.py` enforces the right identifier on new files in each place.

Third-party material keeps its own licence: the vendored UCP schemas in `tests/fixtures/ucp/`
and ACP schemas in `core/commerce/acp/schemas/` are Apache License 2.0 (copy in
[LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)), and dependencies keep their authors'
licences. Hosted plans, infrastructure and model-provider costs are separate from the software
licence.
