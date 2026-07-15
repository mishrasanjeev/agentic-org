"""Scan governed public surfaces for unregistered product claims."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from core.claims.registry import ClaimRegistryService, load_claim_registry
from core.claims.schema import ClaimRegistryDocument, ClaimTreatment, ValidationIssue, ValidationReport

DEFAULT_SURFACE_GLOBS = (
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "sdk/README.md",
    "mcp-server/README.md",
    "mcp-server/package.json",
    "mcp-server/server.json",
    "mcp-server/src/index.ts",
    "core/agents/prompts/sales_agent.prompt.txt",
    "core/reports/generator.py",
    "core/seed_ca_demo.py",
    "llms*.txt",
    "ui/index.html",
    "ui/public/llms*.txt",
    "ui/public/manifest.json",
    "ui/dist/llms*.txt",
    "ui/nginx.conf",
    "ui/nginx.cloudrun.conf.template",
    "ui/src/pages/Landing.*",
    "ui/src/pages/Pricing.*",
    "ui/src/pages/*Solution.*",
    "ui/src/pages/ads/AdsLanding.*",
    "ui/src/pages/blog/blogData.*",
    "ui/src/pages/resources/contentData.*",
    "ui/src/pages/HowGrantexWorks.*",
    "ui/src/pages/IntegrationWorkflow.*",
    "ui/src/pages/OpenAgenticCommerceProtocol.*",
    "ui/src/pages/Status.*",
    "ui/src/pages/legal/Terms.*",
    "ui/src/pages/legal/Privacy.*",
    "ui/src/pages/legal/Support.*",
    "ui/src/pages/legal/Refund.*",
    "ui/src/components/AgentActivityTicker.*",
    "ui/src/components/AgentsInAction.*",
    "ui/src/components/InteractiveDemo.*",
    "ui/src/components/ROICalculator.*",
    "ui/src/components/SocialProof.*",
    "ui/src/components/WorkflowAnimation.*",
)
_MARKER = re.compile(r"\bclaim-id\s*:\s*([A-Za-z0-9]+(?:[._:-][A-Za-z0-9]+)+)", re.I)
_COMMENT = re.compile(r"<!--.*?-->|\{\s*/\*.*?\*/\s*\}", re.DOTALL)
_SOURCE_COMMENT = re.compile(r"^\s*(?://|/\*|\*/|#)")
_NON_VISIBLE_SOURCE_FIELD = re.compile(
    r"^\s*(?:slug|cluster|keywords|relatedSlugs|icon|gradient|link)\s*:",
    re.I,
)
_TAG = re.compile(r"<[^>]+>")
_LINK = re.compile(r"\[([^]]+)]\([^)]*\)")
_HUMAN_LITERAL = re.compile(
    r"\b(?:title|description|subtitle|label|text|copy|headline|heading|tagline|content|alt|aria-label)"
    r"\s*(?:=|:)\s*\{?[\"']([^\"']+)",
    re.I,
)
_RATING = re.compile(r"\b\d(?:\.\d+)?\s*(?:/\s*5|stars?)\b|\b(?:rating|rated)\s+\d", re.I)
_ILLUSTRATIVE_LABEL = re.compile(r"\b(?:illustrative|example|sample|hypothetical)\b", re.I)

_CLAIM_CERTIFICATION = re.compile(
    r"\b(?:certified|accredited|compliant|compliance[- ]ready|WORM[- ]compliant|"
    r"SOC\s*2(?:\s+Type\s+II)?|ISO\s*27001)\b",
    re.I,
)
_CLAIM_AVAILABILITY = re.compile(
    r"\b(?:production[- ]ready|production[- ]proven|production[- ]tested|deploy[- ]ready|"
    r"enterprise[- ]ready|generally available|limited availability|available now|real[- ]time|"
    r"fully automated|live integration|native integration|official\s+(?:MCP\s+)?registry|"
    r"supports?\s+(?:all|every|any)|integrates?\s+with|connects?\s+with|syncs?\s+with|works?\s+with|"
    r"any\s+MCP[- ]compatible\s+client)\b|"
    r"\b(?:now\s+GA|GA\s+(?:release|available|status))\b",
    re.I,
)
_NUMBER_TEXT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_DURATION_UNIT_TEXT = (
    r"(?:milliseconds?|msecs?|ms|seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)"
)
_DURATION_TEXT = rf"(?:[<>≤≥]\s*)?{_NUMBER_TEXT}\s*(?:[-–—]\s*)?{_DURATION_UNIT_TEXT}\b"
_COMPACT_DURATION_TEXT = rf"(?:[<>≤≥]\s*)?{_NUMBER_TEXT}\s*(?:ms|[smhdw])\b"
_PERCENT_TEXT = rf"{_NUMBER_TEXT}\s*%"
_MULTIPLIER_TEXT = rf"{_NUMBER_TEXT}\s*(?:x|\u00d7)\b"
_HARD_MEASURE = re.compile(rf"(?:{_PERCENT_TEXT}|{_MULTIPLIER_TEXT}|{_DURATION_TEXT})", re.I)
_TIME_VALUE_TEXT = rf"(?:{_DURATION_TEXT}|{_COMPACT_DURATION_TEXT})"
_CLAIM_PERFORMANCE = re.compile(
    r"\b(?:uptime|latency|accuracy|precision|recall|confidence|throughput|overhead|faster|speed|"
    r"SLO|SLA|MTTR|MTTD|MTBF|auto[- ]?match(?:ed|ing)?|match\s+rate|"
    r"first[- ](?:contact|touch)\s+resolution|(?:response|resolution|processing|review)\s+time|"
    r"(?:close|review)\s+cycle|mis[- ]?rout(?:e|ed|ing)\s+rate|classification\s+rate|error\s+rate)\b",
    re.I,
)
_OUTCOME_NOUN = re.compile(
    r"\b(?:ROI|ROAS|revenue|savings?|conversion|productivity|efficiency|payback|pipeline)\b",
    re.I,
)
_DIRECTIONAL_OUTCOME = re.compile(
    r"\b(?:improv(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|reduc(?:e|es|ed|ing)|"
    r"decreas(?:e|es|ed|ing)|cut(?:s|ting)?|boost(?:s|ed|ing)?|"
    r"eliminat(?:e|es|ed|ing)|slash(?:es|ed|ing)?|recover(?:s|ed|ing)?|"
    r"grow(?:s|ing|n)?|drop(?:ped|ping|s)?)\b|"
    r"\bsav(?:e|ed|ing)\b(?:\s+\w+){0,3}\s+\b(?:time|money|costs?|hours?|days?|weeks?|months?)\b",
    re.I,
)
_CURRENCY = re.compile(
    rf"(?:₹|Rs\.?|INR|US\$|\$|USD)\s*{_NUMBER_TEXT}(?:\s*(?:[KkMmLl]|lakhs?|crores?))?|"
    rf"{_NUMBER_TEXT}\s*(?:[KkMmLl]|lakhs?|crores?)\s*(?:INR|USD|rupees?|dollars?)\b",
    re.I,
)
_MONETARY_OUTCOME = re.compile(
    r"\b(?:ROI|ROAS|revenue|savings?|saved|recovered|payback|cost\s+(?:saving|reduction)|"
    r"reduc(?:e|ed|tion)\s+costs?|cut\s+costs?)\b",
    re.I,
)
_COMMERCIAL_CONTEXT = re.compile(
    r"\b(?:price|pricing|cost|plan|tier|subscription|hosted|add[- ]on|"
    r"monthly|annually|annual|per\s+(?:client\s+)?(?:month|year))\b|/(?:mo|month|yr|year)\b",
    re.I,
)
_COMMERCIAL_PLAN = re.compile(
    r"\b(?:free|starter|pro|professional|business|enterprise|hosted|managed|self[- ]hosted)\b"
    r"(?:\s+(?:plan|tier))?",
    re.I,
)
_COMMERCIAL_ENTITLEMENT = re.compile(
    r"\b(?:agents?|agent\s+runs?|tasks?|seats?|storage|connectors?|integrations?|"
    r"workspaces?|api\s+keys?)\b",
    re.I,
)
_COMMERCIAL_LIMIT_VALUE = re.compile(
    rf"\b(?:unlimited|no\s+(?:finite\s+)?(?:cap|limit)|{_NUMBER_TEXT}\+?)\b",
    re.I,
)
_COMMERCIAL_ENTITLEMENT_CLAIM = re.compile(
    rf"(?:"
    rf"\b(?:plan|tier|subscription|entitlement|allowance)\b[^.;\n]{{0,45}}"
    rf"{_COMMERCIAL_LIMIT_VALUE.pattern}\s*{_COMMERCIAL_ENTITLEMENT.pattern}"
    rf"|{_COMMERCIAL_PLAN.pattern}[^.;\n]{{0,35}}"
    rf"\b(?:includes?|offers?|allows?|comes\s+with|up\s+to|limit(?:ed)?\s+to)\b[^.;\n]{{0,25}}"
    rf"{_COMMERCIAL_LIMIT_VALUE.pattern}\s*{_COMMERCIAL_ENTITLEMENT.pattern}"
    rf"|\b(?:includes?|offers?|allows?|comes\s+with|up\s+to|limit(?:ed)?\s+to)\b[^.;\n]{{0,25}}"
    rf"{_COMMERCIAL_LIMIT_VALUE.pattern}\s*{_COMMERCIAL_ENTITLEMENT.pattern}"
    rf"|{_COMMERCIAL_ENTITLEMENT.pattern}\s+(?:is|are|remain)?\s*\bunlimited\b"
    rf"|\bunlimited\b\s*{_COMMERCIAL_ENTITLEMENT.pattern}"
    rf")",
    re.I,
)
_COMMERCIAL_TRIAL_OR_DISCOUNT = re.compile(
    rf"\b(?:free\s+trial|{_NUMBER_TEXT}\s*[- ]\s*(?:day|week|month)\s+(?:free\s+)?trial|"
    rf"no\s+credit\s+card|cancel\s+anytime|money[- ]back|{_PERCENT_TEXT}\s+"
    rf"(?:with\s+(?:an?\s+)?)?(?:annual\s+)?(?:discount|off))\b",
    re.I,
)
_COMMERCIAL_SERVICE = re.compile(
    r"\b(?:24\s*/\s*7\s+support|custom\s+SLA|dedicated\s+(?:CSM|account\s+manager)|"
    r"(?:priority|community)\s+support\s+(?:included|with\s+(?:the\s+)?(?:plan|tier))|"
    r"(?:SSO|SCIM)(?:\s*/\s*(?:SSO|SCIM))?\s+(?:included|with\s+(?:the\s+)?(?:plan|tier)))\b",
    re.I,
)
_COMMERCIAL_ACQUISITION = re.compile(
    r"\b(?:start\s+free|create\s+(?:a\s+)?free\s+account|get\s+(?:an?\s+)?api\s+key\s*[-\u2013\u2014:]?\s*free|"
    r"try\s+(?:it\s+)?free|sign\s+up\s+(?:for\s+)?free|free\s+(?:account|plan|tier))\b",
    re.I,
)
_COMMERCIAL_DENIAL = re.compile(
    r"\b(?:(?:do|does|did|will|would)\s+not\s+(?:assume|offer|include|promise|guarantee|assert|claim)|"
    r"no\s+(?:annual\s+discount|plan\s+offer|commercial|pricing|price|support|service[- ]level)[^.;]{0,50}"
    r"(?:claim|offer|commitment|asserted|displayed)|"
    r"(?:verify|confirm|query|read|review)\b[^.;]{0,100}\b(?:checkout|billing\s+(?:service|catalog)|signed\s+(?:quote|agreement)|commercial\s+terms)|"
    r"not\s+(?:the\s+)?authoritative\s+(?:price|pricing|billing|entitlement)|"
    r"pricing\s+and\s+entitlements\s+are\s+(?:intentionally\s+)?not\s+embedded)\b",
    re.I,
)
_LICENSE_FREE = re.compile(
    r"\b(?:Apache|license|source\s+code)\b[^.;]{0,80}\bfree\s+for\s+commercial\s+use\b",
    re.I,
)
_THROUGHPUT = re.compile(
    rf"\b{_NUMBER_TEXT}\s+(?!(?:{_DURATION_UNIT_TEXT})\b)"
    r"(?:[A-Za-z][A-Za-z0-9_-]*(?:\s+[A-Za-z][A-Za-z0-9_-]*){0,2})\s*"
    r"(?:/|per\s+)(?:milliseconds?|msecs?|seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?)\b",
    re.I,
)
_ZERO_OUTCOME = re.compile(
    r"\b(?:zero|0|no|without)\s+(?:manual\s+)?(?:errors?|defects?|failures?|downtime|incidents?|"
    r"false\s+positives?|false\s+negatives?|mis[- ]?routes?|misses|hallucinations?|rework)\b",
    re.I,
)
_COMPARATIVE_TIME = re.compile(
    rf"(?:\bfrom\s+)?{_TIME_VALUE_TEXT}\s+(?:down\s+)?"
    rf"(?:to|vs\.?|versus|rather\s+than|instead\s+of)\s+{_TIME_VALUE_TEXT}|"
    rf"{_TIME_VALUE_TEXT}\s*,?\s*not\s*(?:{_TIME_VALUE_TEXT}|{_NUMBER_TEXT})\b",
    re.I,
)
_TASK_TEXT = (
    r"(?:deploy(?:ment|ed|ing)?|launch(?:ed|ing)?|go[- ]live|clos(?:e|ed|ing)|reconcil(?:e|ed|ing|iation)|"
    r"process(?:ed|ing)?|review(?:ed|ing)?|resolv(?:e|ed|ing)|respond(?:ed|ing)?|onboard(?:ed|ing)?|"
    r"implement(?:ed|ing|ation)?|set\s*up|configur(?:e|ed|ing|ation)|integrat(?:e|ed|ing|ion)|"
    r"migrat(?:e|ed|ing|ion)|approv(?:e|ed|ing|al)|fil(?:e|ed|ing)|rout(?:e|ed|ing)|"
    r"triag(?:e|ed|ing)|classif(?:y|ied|ying|ication)|generat(?:e|ed|ing|ion)|complet(?:e|ed|ing|ion))"
)
_TIMING_EXPRESSION_TEXT = (
    rf"(?:(?:in|within)\s+(?:(?:under|less\s+than|at\s+most)\s+)?|"
    rf"(?:under|less\s+than|at\s+most)\s+|takes?\s+){_TIME_VALUE_TEXT}"
)
_TASK_TIMING = re.compile(rf"\b{_TASK_TEXT}\b.{{0,80}}\b{_TIMING_EXPRESSION_TEXT}", re.I)
_APPROVAL_POLICY = re.compile(
    r"\b(?:requires?|must|needs?\s+to)\b.{0,60}\bapproval\b|\bapproval\b.{0,30}\bis\s+required\b",
    re.I,
)
_CONFIGURATION_METRIC = re.compile(
    r"\b(?:(?:confidence|accuracy|latency)\s+(?:floor|ceiling|threshold|tolerance)|"
    r"match\s+tolerance|approval\s+(?:floor|threshold)|configured\s+reference\s+tolerance)\b",
    re.I,
)
_RESULT_ASSERTION = re.compile(r"\b(?:measured|observed|achieved|actual|result(?:ed)?|reported)\b", re.I)
_CONTROL_PROMISE = re.compile(r"\b(?:retention|backup|RPO|RTO)\b", re.I)
_POLICY_BOUNDARY = re.compile(
    r"\b(?:does\s+not\s+(?:set|establish|create|grant|deny)|sets?\s+no\s+fixed|"
    r"no\s+fixed|only\s+as\s+stated|governed\s+by|depends?\s+on|"
    r"not\s+(?:service[- ]level|historical\s+availability)\s+evidence)\b",
    re.I,
)
_UPTIME_OR_CREDIT_COMMITMENT = re.compile(
    r"\b(?:uptime\s+(?:target|guarantee|commitment)|(?:guarantee|target|commit)\w*[^.;]{0,50}\buptime|"
    r"service\s+credits?[^.;]{0,60}\b(?:eligible|receive|provide|request|equal|apply|applied)|"
    r"(?:eligible|receive|provide|request)\w*[^.;]{0,50}\bservice\s+credits?)\b",
    re.I,
)
_SUPPORT_RESPONSE_COMMITMENT = re.compile(
    r"\b(?:support|we)\b[^.;]{0,80}\b(?:respond|acknowledge|resolve)\w*\b[^.;]{0,40}"
    r"\bwithin\s+(?:\d+|one|two|three)\s+(?:business\s+)?(?:minutes?|hours?|days?|weeks?)\b",
    re.I,
)
_REFUND_COMMITMENT = re.compile(
    r"\b(?:refunds?|credits?)\b[^.;]{0,70}\b(?:within\s+\d+\s+(?:business\s+)?days?|"
    r"pro[- ]?rated|non[- ]?refundable|refunded\s+in\s+full|will\s+(?:be\s+)?(?:issue|process|receive))\b",
    re.I,
)
_RESIDENCY_ASSERTION = re.compile(
    r"\b(?:customer|personal|account|application)?\s*data\b[^.;]{0,70}"
    r"\b(?:is|are|will\s+be)\s+(?:stored|hosted|kept|located|resident)\b[^.;]{0,45}"
    r"\b(?:in|within)\s+[A-Z][A-Za-z -]{1,30}\b|"
    r"\bdata\s+residency\s*(?::|is)\s*(?:IN|EU|US|India|Singapore|Europe|United\s+States)\b",
)
_DECEPTIVE_INSTRUCTION = re.compile(
    r"\bnever\s+(?:reveal|disclose|mention)\b[^.;]{0,50}\b(?:AI|agent|automated|automation)\b|"
    r"\b(?:emails?|messages?)\b[^.;]{0,35}\b(?:go|goes|are\s+sent|send)\s+(?:out\s+)?as\s+"
    r"(?:the\s+)?(?:founder|CEO|executive|employee)\b|"
    r"\b(?:pretend|pose|impersonate)\b[^.;]{0,30}\b(?:founder|CEO|executive|employee|human)\b|"
    r"\bsign\s+off\s+as\b[^.;]{0,60}\b(?:founder|CEO|executive|employee)\b",
    re.I,
)
_DECEPTIVE_PROHIBITION = re.compile(
    r"\b(?:do\s+not|must\s+not|shall\s+not)\s+(?:impersonate|pretend|pose)\b",
    re.I,
)
_QUANTIFIED_COVERAGE = re.compile(r"\b100\s*%\s+of\b", re.I)
_INVENTORY = re.compile(
    rf"\b{_NUMBER_TEXT}\s+(?:pre[- ]?built\s+)?(?:agents?|connectors?|tools?|templates?|integrations?)\b",
    re.I,
)
_NO_ASSERTION = re.compile(
    r"\b(?:(?:do|does|did|will|would)\s+not|cannot|can't)\s+"
    r"(?:claim|promise|guarantee|assert|represent|report|state|prove|demonstrate)\b|"
    r"\bno\s+(?:public\s+)?(?:performance|outcome|availability|rating|certification)\s+claim\b|"
    r"\bnot\s+(?:a\s+)?(?:claim|certification|evidence|proof)\s+that\b",
    re.I,
)
_OUTCOME_DENIAL = re.compile(
    r"\bnot\s+(?:a\s+)?(?:measured|observed|actual|reported|guaranteed)\s+"
    r"(?:customer\s+)?(?:results?|outcomes?|savings?|improvements?|reductions?)\b|"
    r"\bno\s+(?:improvement|increase|reduction|savings?)\s+(?:is|are)\s+"
    r"(?:assumed|claimed|promised|guaranteed)\b|"
    r"\bnot\s+(?:a\s+)?[^.;]{0,80}\b(?:savings?|ROI|ROAS)\s+"
    r"(?:projection|claim|promise|guarantee|result)\b",
    re.I,
)
_CERTIFICATION_DENIAL = re.compile(
    r"\bnot\s+(?!only\b)(?:(?:currently|yet)\s+)?(?:a\s+)?"
    r"(?:(?:SOC\s*2|ISO\s*27001|WORM)\s+)?(?:certified|accredited|compliant|certification|accreditation|compliance)\b|"
    r"\bnot\s+(?!only\b)(?:\w+\s+){0,3}(?:certified|accredited)\b|"
    r"\b(?:certification|accreditation|SOC\s*2|ISO\s*27001)\s+(?:is\s+)?"
    r"(?:pending|not\s+(?:established|complete|verified))\b|"
    r"\bno\s+(?:(?:SOC\s*2|ISO\s*27001)\s+)?(?:certification|accreditation|compliance)\s+"
    r"(?:claim|status|evidence)\b|"
    r"\b(?:do|does|did)\s+not(?:\s+\w+){0,3}\s+"
    r"(?:confer|establish|constitute|prove|demonstrate|guarantee)\b[^.;]{0,100}"
    r"\b(?:certification|certified|compliance|compliant)\b|"
    r"\bwithout\s+(?:asserting|claiming|representing|treating)\b[^.;]{0,100}"
    r"\b(?:certification|certified|compliance|compliant)\b|"
    r"\bmakes?\s+no\s+(?:certification|compliance)(?:\s+or\s+[\w-]+)?\s+claim\b|"
    r"\bcertification\s+requires\b[^.;]{0,80}\b(?:auditor|third[- ]party)\s+evidence\b",
    re.I,
)
_CERTIFICATION_EDUCATIONAL = re.compile(
    r"\b(?:educational|overview|guide|reference|discussion|controls?\s+(?:and\s+evidence\s+)?"
    r"to\s+evaluate|questions?\s+to\s+evaluate)\b",
    re.I,
)
_CERTIFICATION_ASSERTION = re.compile(
    r"\b(?:certified|accredited|compliant|compliance[- ]ready|certification\s+status)\b",
    re.I,
)
_AVAILABILITY_DENIAL = re.compile(
    r"\bnot\s+(?!only\b)(?:(?:currently|yet)\s+)?(?:production[- ](?:ready|proven|tested)|"
    r"deploy[- ]ready|enterprise[- ]ready|generally\s+available|available\s+now|GA)\b|"
    r"\b(?:availability|production\s+readiness)\s+(?:is\s+)?(?:pending|not\s+established)\b|"
    r"\bnot\s+(?:configured|verified|certified)(?:\s+or\s+(?:configured|verified|certified))*\s+"
    r"native\s+connectors?\b",
    re.I,
)
_CLAUSE_BREAK = re.compile(r"(?<!\d)[.;](?!\d)|\s+[—–]\s+|\b(?:but|however|yet)\b", re.I)
_STRUCTURED_FIELD = re.compile(r"[\"']?(?:value|metric|result|change)[\"']?\s*[:=]", re.I)
_STRUCTURED_NUMBER_FIELD = re.compile(
    rf"[\"']?(?:value|metric|result|change)[\"']?\s*[:=]\s*(?:\{{\s*)?[\"'`]?\s*[+-]?\s*"
    rf"(?:{_NUMBER_TEXT}|zero\b|no\b|without\b)",
    re.I,
)


def _checked_at(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must include a timezone")
    return current


def discover_public_surfaces(root: str | Path, globs: Sequence[str] = DEFAULT_SURFACE_GLOBS) -> list[Path]:
    base = Path(root).resolve()
    found: set[Path] = set()
    for pattern in globs:
        for path in base.glob(pattern):
            resolved = path.resolve()
            if path.is_file() and resolved.is_relative_to(base):
                found.add(resolved)
    return sorted(found, key=lambda path: path.relative_to(base).as_posix())


def _visible_text(raw: str) -> str:
    if _SOURCE_COMMENT.search(raw) or _NON_VISIBLE_SOURCE_FIELD.search(raw):
        return ""
    literals = " ".join(_HUMAN_LITERAL.findall(raw))
    text = _COMMENT.sub(" ", raw)
    text = _LINK.sub(r"\1", text)
    text = _TAG.sub(" ", text)
    text = re.sub(r"[`*_#|{}();]", " ", text)
    return html.unescape(f"{literals} {text}")


def _normalized(raw: str) -> str:
    return " ".join(_visible_text(raw).casefold().split())


def _clauses(text: str) -> list[str]:
    return [clause.strip() for clause in _CLAUSE_BREAK.split(text) if len(clause.strip()) >= 5]


def _is_commercial_claim(text: str) -> bool:
    if _COMMERCIAL_DENIAL.search(text) or _LICENSE_FREE.search(text):
        return False
    if text.rstrip().endswith("?") and not _CURRENCY.search(text):
        return False
    price = bool(_CURRENCY.search(text) and _COMMERCIAL_CONTEXT.search(text))
    entitlement = bool(_COMMERCIAL_ENTITLEMENT_CLAIM.search(text))
    return bool(
        price
        or entitlement
        or _COMMERCIAL_TRIAL_OR_DISCOUNT.search(text)
        or _COMMERCIAL_SERVICE.search(text)
        or _COMMERCIAL_ACQUISITION.search(text)
    )


def _claim_shape_for_clause(text: str) -> str | None:
    no_assertion = bool(_NO_ASSERTION.search(text))
    policy_boundary = bool(_POLICY_BOUNDARY.search(text))
    if _DECEPTIVE_INSTRUCTION.search(text) and not _DECEPTIVE_PROHIBITION.search(text):
        return "deceptive_instruction"
    if not policy_boundary:
        if _RESIDENCY_ASSERTION.search(text):
            return "availability"
        if _REFUND_COMMITMENT.search(text):
            return "commercial"
        if _UPTIME_OR_CREDIT_COMMITMENT.search(text) or _SUPPORT_RESPONSE_COMMITMENT.search(text):
            return "performance"
    if _is_commercial_claim(text) and not no_assertion:
        return "commercial"
    educational_certification_topic = bool(
        _CERTIFICATION_EDUCATIONAL.search(text) and not _CERTIFICATION_ASSERTION.search(text)
    )
    if _CLAIM_CERTIFICATION.search(text) and not (
        no_assertion or _CERTIFICATION_DENIAL.search(text) or educational_certification_topic
    ):
        return "certification"
    if _RATING.search(text) and not no_assertion:
        return "rating"
    if _CLAIM_AVAILABILITY.search(text) and not (no_assertion or _AVAILABILITY_DENIAL.search(text)):
        return "availability"
    if _INVENTORY.search(text) and not no_assertion:
        return "inventory"

    configuration_value = bool(_CONFIGURATION_METRIC.search(text)) and not _RESULT_ASSERTION.search(text)
    performance_measure = bool(_HARD_MEASURE.search(text) and _CLAIM_PERFORMANCE.search(text))
    performance_shape = (
        performance_measure
        or bool(_THROUGHPUT.search(text))
        or bool(_ZERO_OUTCOME.search(text))
        or bool(_COMPARATIVE_TIME.search(text))
        or bool(_TASK_TIMING.search(text) and not _APPROVAL_POLICY.search(text))
        or bool(_QUANTIFIED_COVERAGE.search(text))
        or bool(_HARD_MEASURE.search(text) and _CONTROL_PROMISE.search(text))
    )
    if performance_shape and not (no_assertion or configuration_value):
        return "performance"

    monetary_outcome = bool(_CURRENCY.search(text) and _MONETARY_OUTCOME.search(text))
    measured_outcome = bool(_OUTCOME_NOUN.search(text) and _HARD_MEASURE.search(text))
    if (monetary_outcome or measured_outcome or _DIRECTIONAL_OUTCOME.search(text)) and not (
        no_assertion or _OUTCOME_DENIAL.search(text)
    ):
        return "outcome"
    return None


def _claim_shape(raw: str) -> str | None:
    text = _visible_text(raw)
    if len(text.strip()) < 5:
        return None
    for clause in _clauses(text):
        kind = _claim_shape_for_clause(clause)
        if kind is not None:
            return kind
    return None


def _structured_claim_window(lines: Sequence[str], index: int) -> str | None:
    line = lines[index]
    if not (_STRUCTURED_FIELD.search(line) and _STRUCTURED_NUMBER_FIELD.search(line)):
        return None
    if re.search(r"}\s*[,;]?\s*$", line):
        return line
    window = [line]
    for following in lines[index + 1 : min(len(lines), index + 4)]:
        window.append(following)
        if re.match(r"^\s*}\s*[,;]?\s*$", following):
            break
    return " ".join(window)


def _structured_claim_shape(lines: Sequence[str], index: int) -> str | None:
    window = _structured_claim_window(lines, index)
    if window is None:
        return None
    kind = _claim_shape(window)
    if kind is not None:
        return kind
    text = _visible_text(window)
    if _NO_ASSERTION.search(text):
        return None
    if _CONFIGURATION_METRIC.search(text) and not _RESULT_ASSERTION.search(text):
        return None
    if _CLAIM_PERFORMANCE.search(text):
        return "performance"
    if _OUTCOME_NOUN.search(text) or _DIRECTIONAL_OUTCOME.search(text):
        return "outcome"
    if _INVENTORY.search(text):
        return "inventory"
    return None


def _approved_span(
    lines: Sequence[str], start: int, end: int, approved_texts: Sequence[str]
) -> tuple[int, int, str] | None:
    candidates: list[tuple[int, int, int, str]] = []
    approved = [(text, _normalized(text)) for text in approved_texts]
    for left in range(start, end):
        for right in range(left + 1, end + 1):
            normalized = _normalized(" ".join(lines[left:right]))
            for text, expected in approved:
                if expected and expected == normalized:
                    candidates.append((right - left, left, right, text))
    if not candidates:
        return None
    _, left, right, text = min(candidates, key=lambda item: (item[0], item[1], item[2], item[3]))
    return left, right, text


def _approved_residual(raw: str, approved_text: str) -> str | None:
    visible = " ".join(_visible_text(raw).split())
    expected = " ".join(approved_text.split())
    if not expected:
        return None
    match = re.search(re.escape(expected).replace(r"\ ", r"\s+"), visible, re.I)
    if match is None:
        return None
    return f"{visible[: match.start()]} {visible[match.end() :]}"


def _service(registry: ClaimRegistryService | ClaimRegistryDocument | str | Path) -> ClaimRegistryService:
    if isinstance(registry, ClaimRegistryService):
        return registry
    if isinstance(registry, ClaimRegistryDocument):
        return ClaimRegistryService(registry)
    return ClaimRegistryService(load_claim_registry(registry))


def _surface_paths(root: Path, paths: Sequence[str | Path] | None) -> tuple[list[Path], list[ValidationIssue]]:
    if paths is None:
        return discover_public_surfaces(root), []
    found: set[Path] = set()
    issues: list[ValidationIssue] = []
    for requested in paths:
        candidate = Path(requested)
        resolved = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if not resolved.is_relative_to(root):
            issues.append(
                ValidationIssue(
                    code="surface_outside_root", message="surface escapes repository root", surface=str(requested)
                )
            )
        elif not resolved.is_file():
            issues.append(
                ValidationIssue(code="surface_missing", message="surface does not exist", surface=str(requested))
            )
        else:
            found.add(resolved)
    return sorted(found, key=lambda path: path.relative_to(root).as_posix()), issues


def _dedupe(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    unique: dict[tuple[object, ...], ValidationIssue] = {}
    for issue in issues:
        key = (
            issue.code,
            issue.claim_id,
            issue.capability_id,
            issue.evidence_id,
            issue.surface,
            issue.line,
            issue.message,
        )
        unique[key] = issue
    return sorted(
        unique.values(), key=lambda issue: (issue.surface or "", issue.line or 0, issue.code, issue.claim_id or "")
    )


def scan_surfaces(
    root: str | Path,
    registry: ClaimRegistryService | ClaimRegistryDocument | str | Path,
    *,
    paths: Sequence[str | Path] | None = None,
    now: datetime | None = None,
) -> ValidationReport:
    base = Path(root).resolve()
    checked_at = _checked_at(now)
    service = _service(registry)
    issues = list(service.validate(now=checked_at).issues)
    surfaces, path_issues = _surface_paths(base, paths)
    issues.extend(path_issues)
    for path in surfaces:
        surface = path.relative_to(base).as_posix()
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeError:
            issues.append(
                ValidationIssue(code="surface_not_utf8", message="surface is not valid UTF-8", surface=surface)
            )
            continue
        marker_lines = raw.splitlines()
        # Remove complete HTML/JSX comments before line-by-line claim-shape
        # analysis.  Keep the exact newline count so marker bindings and
        # diagnostics retain their original source line numbers.
        lines = _COMMENT.sub(
            lambda match: "\n" * match.group(0).count("\n"),
            raw,
        ).splitlines()
        fenced = False
        ignored: set[int] = set()
        markers: list[tuple[int, str]] = []
        for index, line in enumerate(marker_lines):
            if path.suffix.lower() == ".md" and line.strip().startswith("```"):
                fenced = not fenced
                ignored.add(index)
                continue
            if fenced:
                ignored.add(index)
                continue
            markers.extend((index, claim_id) for claim_id in _MARKER.findall(line))
        bound: set[int] = set()
        for marker_index, claim_id in markers:
            end = min(len(lines), marker_index + 9)
            next_markers = [index for index, _ in markers if marker_index < index < end]
            if next_markers:
                end = min(next_markers)
            bound.add(marker_index)
            claim = service.claims.get(claim_id)
            if claim is None:
                issues.append(
                    ValidationIssue(
                        code="claim_not_registered",
                        message="claim marker is not registered",
                        claim_id=claim_id,
                        surface=surface,
                        line=marker_index + 1,
                    )
                )
                continue
            for issue in service.authorize_claim(claim_id, surface=surface, now=checked_at).issues:
                if issue.code in {"claim_surface_not_permitted", "hidden_claim_on_public_surface"}:
                    issues.append(issue.model_copy(update={"line": marker_index + 1}))
            approved_span = _approved_span(lines, marker_index, end, claim.approved_text)
            if approved_span is None:
                issues.append(
                    ValidationIssue(
                        code="claim_text_not_approved",
                        message="marker is not followed by registry-approved text",
                        claim_id=claim_id,
                        surface=surface,
                        line=marker_index + 1,
                    )
                )
                claim_window = " ".join(lines[marker_index:end])
                unbound_kind = _claim_shape(claim_window)
                if unbound_kind is not None:
                    issues.append(
                        ValidationIssue(
                            code="unbound_public_claim",
                            message=f"{unbound_kind} claim requires a claim-id marker and registry record",
                            surface=surface,
                            line=marker_index + 1,
                        )
                    )
            else:
                left, right, approved_text = approved_span
                bound.update(range(left, right))
                claim_window = " ".join(lines[left:right])
                residual = _approved_residual(claim_window, approved_text)
                residual_kind = _claim_shape(residual) if residual is not None else None
                if residual_kind is not None:
                    issues.append(
                        ValidationIssue(
                            code="unbound_public_claim",
                            message=f"{residual_kind} claim requires a claim-id marker and registry record",
                            surface=surface,
                            line=left + 1,
                        )
                    )
            if claim.treatment is ClaimTreatment.ILLUSTRATIVE and not _ILLUSTRATIVE_LABEL.search(
                _visible_text(claim_window)
            ):
                issues.append(
                    ValidationIssue(
                        code="illustrative_claim_not_labeled",
                        message="illustrative claim must be visibly labeled",
                        claim_id=claim_id,
                        surface=surface,
                        line=marker_index + 1,
                    )
                )
        structured_bound: set[int] = set()
        for index, line in enumerate(lines):
            if index in ignored or index in bound or index in structured_bound or _MARKER.search(line):
                continue
            structured_kind = _structured_claim_shape(lines, index)
            if structured_kind is not None:
                for following_index in range(index + 1, min(len(lines), index + 4)):
                    structured_bound.add(following_index)
                    if re.match(r"^\s*}\s*[,;]?\s*$", lines[following_index]):
                        break
            kind = _claim_shape(line) or structured_kind
            if kind:
                issues.append(
                    ValidationIssue(
                        code="unbound_public_claim",
                        message=f"{kind} claim requires a claim-id marker and registry record",
                        surface=surface,
                        line=index + 1,
                    )
                )
    return ValidationReport(
        registry_version=service.document.registry_version, checked_at=checked_at, issues=_dedupe(issues)
    )
