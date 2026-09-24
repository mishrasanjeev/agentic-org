# Screening Disposition

The Screening Disposition agent (`core/agents/screening_disposition/`) turns one screening hit into
a proposed `screening_disposition` an analyst can accept or override in under two minutes:
per-identifier comparisons with cited evidence, a written rationale, a proposed outcome and a
confidence band. **It never closes a hit, in any configuration.**

<!-- snippet: tests/unit/screening_disposition/test_disposition_agent.py#run-disposition -->
```python
from core.agents.screening_disposition import DispositionConfig, DispositionDependencies, run_screening_disposition
from core.cases.grant_authorizer import case_authorizer

outcome = await run_screening_disposition(
    tenant_id="",
    case_id="case-0001",
    screening_result=screening_result,
    hit_id=screening_result.hits[0].hit_id,
    subject=party,  # the screened party from the underwriter's hand-off
    associated_entities=["Oakhollow Bakery Cooperative"],
    config=DispositionConfig(),
    deps=DispositionDependencies(
        provider=provider,
        authorizer=case_authorizer("", "case-0001", "screening_disposition", "aml.cdd.onboarding"),
    ),
)
disposition = outcome.disposition  # schema: screening_disposition, review is null
```

The empty tenant ID is a placeholder. A real run needs a tenant UUID, one active shared
`screening_disposition` registration, an allowed case purpose and a valid delegated grant;
otherwise the provider call is refused.

## What a run does

1. **Gather evidence.** The subject is screened again through the provider tool gateway
   (`screen_person` or `screen_business`, restricted to the hit's list type) to confirm the
   provider still returns the list entry and to cite fresh records. If the provider does not
   offer screening or the call fails, the screening result the case already holds is used
   (`hit_confirmed: null`). If the entry is no longer returned, the proposal is
   `insufficient_information` with a `low` band (`hit_confirmed: false`).
2. **Compare** the subject with the hit on each identifier:

   | Identifier | `match` | `partial_match` | `mismatch` | `not_comparable` |
   |---|---|---|---|---|
   | `name` | normalised names (or an alias) equal | similarity ≥ 0.85 | below 0.85 | — |
   | `date_of_birth` | full dates agree | agree to the precision both give (year or month) | disagree | missing on either side |
   | `nationality` | any shared country | — | none shared | missing on either side |
   | `address` | same country and postal code | same country | different country | missing on either side |
   | `associated_entities` | a normalised name in common | — | none in common | none on either side |

   Every comparison that is not `not_comparable` cites the hit's record at the compared field
   (for example `dates_of_birth[0]`).
3. **Propose** by fixed rules - never from the model:
   - names do not match, or dates of birth disagree → `false_positive`;
   - names match (fully or partly) and dates of birth agree → `true_match`;
   - names match, no date of birth to compare, associated entities overlap → `true_match`;
   - otherwise → `insufficient_information`.

   Nationality and address never decide alone. The **confidence band** (`high`, `medium`, `low`)
   counts identifiers that support the outcome against those that contradict it. It is metadata
   for the reviewer and gates nothing.
4. **Explain.** The model receives only the list type, the comparison results, the proposed
   outcome and the band, behind the untrusted-content guard and, when
   `pseudonymisation.pre_model` is on, pseudonymisation. Its rationale is refused - and a template
   rationale built from the comparisons is used instead - when it is empty or longer than 1,200
   characters, states a different outcome, talks of closing, clearing or dismissing the hit,
   still holds a placeholder, or repeats untrusted text such as a list alias.
5. **Assemble** the disposition with `review: null`, validate it against `screening_disposition`
   and check that every cited record came from the held result or this run's provider calls.

## No automatic closure

- The tool set is `{screen_person, screen_business}`; the gateway refuses to build a tool set
  containing anything but read tools, so no closing, clearing or decision tool can be added by
  configuration.
- Nothing in the package closes, clears, dismisses or decides - a test inspects every callable,
  the run parameters and the configuration fields.
- Every disposition leaves the agent unreviewed, whatever the configuration and whatever the model
  says.

## Recording an analyst's review

`apply_review` is the override capture model the approvals console uses. The analyst identity comes
from the authenticated session, never from the submitted body (which cannot carry one).

<!-- snippet: tests/unit/screening_disposition/test_disposition_comparison_and_review.py#record-override -->
```python
from datetime import UTC, datetime

from core.agents.screening_disposition import DispositionReviewRequest, apply_review

submitted = DispositionReviewRequest(
    action="overridden",
    final_outcome="insufficient_information",
    reason="Requesting a certified passport copy.",
)
reviewed = apply_review(
    disposition,
    submitted,
    analyst_id=authenticated_user_id,  # from the authenticated session, never the request body
    reviewed_at=datetime.now(UTC),
)
```

Refusal reasons (`DispositionReviewError.reason`): `disposition_invalid`, `already_reviewed`,
`request_invalid`, `analyst_invalid` (empty or an `agent:` identity), `reviewed_at_naive`,
`accepted_outcome_differs`, `override_outcome_unchanged`, `override_reason_required`.

Recording a review closes nothing. Closing a hit remains the analyst's action in the operator's
system of record.

## What is recorded

`DispositionOutcome.case_record()` holds the prompt id, version and SHA-256, whether the rationale
came from the model or the template (and why a model rationale was refused), model confidence
(metadata), whether the hit was confirmed on re-screening, whether pseudonymisation was on, and
every tool call with request and response hashes.

Metric: `agenticorg_screening_dispositions_proposed_total{outcome,band}`, plus the provider gateway
metrics described in [Business Onboarding Underwriter](business-underwriter.md#metrics).

## Tests

- `tests/unit/screening_disposition/` — every hit fixture, outcome and band rules, rationale
  refusal, re-screening, no automatic closure, grants, prompt integrity, pseudonymisation, review
  capture and a replayed cassette.
- `tests/security/test_disposition_adversarial.py` — an instruction in a screening alias changes
  no proposal, tool call or model request.
