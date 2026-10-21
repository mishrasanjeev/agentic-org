# Commercial licence

The AgenticOrg platform is source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE) (SPDX `PolyForm-Noncommercial-1.0.0`).
The licensor is **Orchestrum Technologies LLP**.

**SDK carve-out:** the AgenticOrg SDKs (`sdk/`, `sdk-ts/`) and the MCP server (`mcp-server/`)
are open source under the Apache License 2.0 and free for any use, including commercial use.
They do not need a commercial licence. Everything on this page is about the platform: every
other part of this repository.

**Only self use is free. Commercial use of any sort needs Orchestrum's prior approval and a
written agreement with Orchestrum Technologies LLP.**

This page explains the licence in plain language. The [LICENSE](LICENSE) text is what
applies; if this page and the licence differ, the licence wins.

## What is free

You may use, copy and modify the AgenticOrg platform without a fee and without asking, for the
purposes the PolyForm Noncommercial License 1.0.0 permits:

- **Personal use**: personal study, hobby projects, private entertainment and amateur
  pursuits, with no anticipated commercial application.
- **Personal evaluation and testing**: trying the software out, experimenting and testing it,
  for yourself or for the benefit of public knowledge, with no anticipated commercial
  application.
- **Non-commercial research and education**: research, teaching and learning that is not
  carried out for a commercial purpose.
- **Non-commercial organisations**: use by a charitable organisation, educational institution,
  public research organisation, public safety or health organisation, environmental
  protection organisation or government institution, as the licence's "Noncommercial
  Organizations" section allows.

You may share copies, with or without your changes, for those purposes. Anyone who gets a copy
from you must also get the licence terms (or their URL) and the `Required Notice:` lines at the
top of [LICENSE](LICENSE).

## What needs a written agreement

Any commercial use of the platform needs a written agreement with Orchestrum Technologies LLP
before it starts. That includes, for example:

- **Internal business use** by a company or any other organisation operating for profit,
  including running AgenticOrg for your own staff, customers, operations or back office.
- **Hosting**: offering AgenticOrg, or a service built on it, as a hosted, managed or
  software-as-a-service product to anyone else.
- **Resale**: selling, reselling, sublicensing, renting or distributing AgenticOrg, or any
  product that includes it, for a fee or other commercial advantage.
- **Embedding**: building AgenticOrg, or any part of it, into a commercial product or service,
  including rebranded or issuer-branded offerings.
- **Consulting delivery**: using AgenticOrg to deliver consulting, implementation,
  integration, support or managed services to clients.
- **Business evaluation**: evaluating AgenticOrg inside a business with a view to any of the
  above. Write to us first; we can confirm evaluation terms in writing.

If you are not sure whether your use is commercial, ask before you rely on it.

## How to get a commercial licence

Write to [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in) with:

1. your organisation's name and country;
2. how you intend to use AgenticOrg (internal use, hosted service, product, consulting
   delivery) and who the users will be;
3. how you will deploy it (self-hosted, your own cloud, or Orchestrum-hosted).

A commercial licence exists only when a written agreement has been signed by Orchestrum
Technologies LLP. An enquiry, an issue, a pull request or a download is not an agreement.
Prices, support and service levels are set only in that agreement.

## Existing users and earlier versions

The PolyForm Noncommercial License 1.0.0 applies to every version of AgenticOrg published on or
after 21 October 2026, including all later updates, fixes and security patches. Versions
published before 21 October 2026 remain available under the Apache License 2.0 they were
released with.

If you already use AgenticOrg commercially, including if you started before 21 October 2026,
you need a written agreement with Orchestrum Technologies LLP to use any version published on
or after that date commercially, including updates and security fixes. Commercial use of the
AgenticOrg or Grantex names or logos, of Orchestrum-hosted services or of the brand needs
Orchestrum's prior written approval whichever version you use; see [TRADEMARKS.md](TRADEMARKS.md).

Hosted subscriptions to Orchestrum-operated services are governed by the plan or agreement you
accepted for them.

## Licence scope

| Directory | Licence |
| --- | --- |
| `sdk/` (Python SDK) | Apache License 2.0: open source, free for any use including commercial |
| `sdk-ts/` (TypeScript SDK) | Apache License 2.0: open source, free for any use including commercial |
| `mcp-server/` (MCP server) | Apache License 2.0: open source, free for any use including commercial |
| `tests/fixtures/ucp/`, `core/commerce/acp/schemas/` (vendored protocol schemas) | Apache License 2.0 (third-party, unchanged) |
| Everything else: the AgenticOrg platform (`api/`, `core/`, `auth/`, `connectors/`, `ui/` and the website, workers, `docs/`, `scripts/`, `migrations/`, tests) | PolyForm Noncommercial License 1.0.0: commercial use needs a written agreement with Orchestrum Technologies LLP |

## Third-party material

Material that Orchestrum did not write keeps its own licence, and this licence does not apply
to it:

- `tests/fixtures/ucp/`: Universal Commerce Protocol JSON Schemas, Apache License 2.0.
- `core/commerce/acp/schemas/`: Agentic Commerce Protocol JSON Schemas, Apache License 2.0.
- Dependencies installed from package registries, under their authors' own licences.

A copy of the Apache License 2.0 is in [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt).

## Contributions

Contributions are accepted under the [contributor licence](CONTRIBUTING.md#contributor-licence),
which lets Orchestrum license them commercially as well as under the PolyForm Noncommercial
License 1.0.0.

## Contact

Orchestrum Technologies LLP: [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in).
