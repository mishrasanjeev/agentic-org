"""Agent layer — auto-load all 25 agent modules so @AgentRegistry.register fires."""

# Finance agents
# Backoffice agents
from core.agents.backoffice import (
    facilities_agent,  # noqa: F401
    legal_ops,  # noqa: F401
    risk_sentinel,  # noqa: F401
)
from core.agents.finance import (
    ap_processor,  # noqa: F401
    ar_collections,  # noqa: F401
    close_agent,  # noqa: F401
    fpa_agent,  # noqa: F401
    recon_agent,  # noqa: F401
    tax_compliance,  # noqa: F401
)

# HR agents
from core.agents.hr import (
    ld_coordinator,  # noqa: F401
    offboarding,  # noqa: F401
    onboarding,  # noqa: F401
    payroll_engine,  # noqa: F401
    performance_coach,  # noqa: F401
    talent_acquisition,  # noqa: F401
)

# Marketing agents
from core.agents.marketing import (
    brand_monitor,  # noqa: F401
    campaign_pilot,  # noqa: F401
    content_factory,  # noqa: F401
    crm_intelligence,  # noqa: F401
    seo_strategist,  # noqa: F401
)

# Ops agents
from core.agents.ops import (
    compliance_guard,  # noqa: F401
    contract_intelligence,  # noqa: F401
    it_operations,  # noqa: F401
    support_deflector,  # noqa: F401
    support_triage,  # noqa: F401
    vendor_manager,  # noqa: F401
)
