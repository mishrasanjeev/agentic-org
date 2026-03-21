"""Agent layer — auto-load all 25 agent modules so @AgentRegistry.register fires."""

# Finance agents
from core.agents.finance import ap_processor  # noqa: F401
from core.agents.finance import ar_collections  # noqa: F401
from core.agents.finance import close_agent  # noqa: F401
from core.agents.finance import fpa_agent  # noqa: F401
from core.agents.finance import recon_agent  # noqa: F401
from core.agents.finance import tax_compliance  # noqa: F401

# HR agents
from core.agents.hr import talent_acquisition  # noqa: F401
from core.agents.hr import onboarding  # noqa: F401
from core.agents.hr import payroll_engine  # noqa: F401
from core.agents.hr import performance_coach  # noqa: F401
from core.agents.hr import ld_coordinator  # noqa: F401
from core.agents.hr import offboarding  # noqa: F401

# Marketing agents
from core.agents.marketing import brand_monitor  # noqa: F401
from core.agents.marketing import campaign_pilot  # noqa: F401
from core.agents.marketing import content_factory  # noqa: F401
from core.agents.marketing import crm_intelligence  # noqa: F401
from core.agents.marketing import seo_strategist  # noqa: F401

# Ops agents
from core.agents.ops import compliance_guard  # noqa: F401
from core.agents.ops import contract_intelligence  # noqa: F401
from core.agents.ops import it_operations  # noqa: F401
from core.agents.ops import support_triage  # noqa: F401
from core.agents.ops import vendor_manager  # noqa: F401

# Backoffice agents
from core.agents.backoffice import facilities_agent  # noqa: F401
from core.agents.backoffice import legal_ops  # noqa: F401
from core.agents.backoffice import risk_sentinel  # noqa: F401
