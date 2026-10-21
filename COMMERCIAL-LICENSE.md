# Commercial licence

The AgenticOrg platform is source-available under the
[Orchestrum Personal Use License 1.0](LICENSE) (SPDX `LicenseRef-Orchestrum-Personal-Use-1.0`).
The licensor is **Orchestrum Technologies LLP**.

**SDK carve-out:** the AgenticOrg SDKs (`sdk/`, `sdk-ts/`) and the MCP server (`mcp-server/`)
are open source under the Apache License 2.0 and free for any use, including commercial use.
They do not need a commercial licence. Everything on this page is about the platform: every
other part of this repository.

**Only an individual using the platform for their own personal purposes is free. Any
organisation needs prior written permission in a signed agreement with Orchestrum Technologies
LLP, with no exception for evaluation, testing, charities, education, research or government.**

This page explains the licence in plain language. The [LICENSE](LICENSE) text is what
applies; if this page and the licence differ, the licence wins.

## What is free

An **individual** may use, copy and modify the AgenticOrg platform without a fee and without
asking, for their own personal purposes, as long as they are not acting for, on behalf of, or
for the benefit of any organisation. For example: personal study, trying it out on your own
computer, and hobby projects.

That is the only free use. You may not distribute the platform, modified or not, without a
written agreement, and every copy you keep must carry the licence and copyright notice.

## What needs a written agreement

Any use other than an individual's personal use needs prior written permission from Orchestrum
Technologies LLP in a signed agreement, before it starts, whether the use is paid or free.
That includes, for example:

- **Any organisation**: a company, partnership, LLP, firm, a sole proprietor's business,
  charity, non-profit, school, university, research institution or government body, for any
  purpose, including internal use for your own staff, customers, operations or back office.
- **Evaluation, testing and pilots**: trying, testing or piloting AgenticOrg by or for an
  organisation, even briefly and even without production data.
- **Hosting**: offering AgenticOrg, or a service built on it, as a hosted, managed or
  software-as-a-service product to anyone else.
- **Resale**: selling, reselling, sublicensing, renting or distributing AgenticOrg, or any
  product that includes it.
- **Embedding**: building AgenticOrg, or any part of it, into another product or service,
  including rebranded or issuer-branded offerings.
- **Consulting delivery**: using AgenticOrg to deliver consulting, implementation,
  integration, support or managed services to clients.

If you are not sure whether your use is personal, ask before you rely on it.

## How to get a commercial licence

Write to [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in) with:

1. your organisation's name and country;
2. how you intend to use AgenticOrg (evaluation, internal use, hosted service, product,
   consulting delivery) and who the users will be;
3. how you will deploy it (self-hosted, your own cloud, or Orchestrum-hosted).

A licence exists only when a written agreement has been signed by Orchestrum Technologies LLP.
An enquiry, an issue, a pull request or a download is not an agreement. Prices, support and
service levels are set only in that agreement.

## Existing users and earlier versions

The Orchestrum Personal Use License 1.0 applies to every version of AgenticOrg published on or
after 21 October 2026, including all later updates, fixes and security patches. Versions
published before 21 October 2026 remain available under the Apache License 2.0 they were
released with.

If your organisation already uses AgenticOrg, including if it started before 21 October 2026,
it needs a signed agreement with Orchestrum Technologies LLP to use any version published on or
after that date, including updates and security fixes. Commercial use of the AgenticOrg or
Grantex names or logos, of Orchestrum-hosted services or of the brand needs Orchestrum's prior
written approval whichever version you use; see [TRADEMARKS.md](TRADEMARKS.md).

Hosted subscriptions to Orchestrum-operated services are governed by the plan or agreement you
accepted for them.

## Licence scope

| Directory | Licence |
| --- | --- |
| `sdk/` (Python SDK) | Apache License 2.0: open source, free for any use including commercial |
| `sdk-ts/` (TypeScript SDK) | Apache License 2.0: open source, free for any use including commercial |
| `mcp-server/` (MCP server) | Apache License 2.0: open source, free for any use including commercial |
| `tests/fixtures/ucp/`, `core/commerce/acp/schemas/` (vendored protocol schemas) | Apache License 2.0 (third-party, unchanged) |
| Everything else: the AgenticOrg platform (`api/`, `core/`, `auth/`, `connectors/`, `ui/` and the website, workers, `docs/`, `scripts/`, `migrations/`, tests) | Orchestrum Personal Use License 1.0: individuals' personal use only; any organisation needs a signed agreement with Orchestrum Technologies LLP |

## Third-party material

Material that Orchestrum did not write keeps its own licence, and this licence does not apply
to it:

- `tests/fixtures/ucp/`: Universal Commerce Protocol JSON Schemas, Apache License 2.0.
- `core/commerce/acp/schemas/`: Agentic Commerce Protocol JSON Schemas, Apache License 2.0.
- Dependencies installed from package registries, under their authors' own licences.

A copy of the Apache License 2.0 is in [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt).

## Contributions

Contributions are accepted under the [contributor licence](CONTRIBUTING.md#contributor-licence),
which lets Orchestrum license them under the Orchestrum Personal Use License 1.0 and under
commercial agreements.

## Contact

Orchestrum Technologies LLP: [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in).
