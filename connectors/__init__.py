"""Connector layer — 42 typed adapters, auto-loaded and registered."""

from connectors.registry import ConnectorRegistry
from connectors.framework.base_connector import BaseConnector

# Import all connector modules so their classes are loaded into memory.
# The _auto_register() call at the bottom registers them all with ConnectorRegistry.

# Finance connectors (10)
from connectors.finance import oracle_fusion as _  # noqa: F401, F811
from connectors.finance import sap as _  # noqa: F401, F811
from connectors.finance import gstn as _  # noqa: F401, F811
from connectors.finance import banking_aa as _  # noqa: F401, F811
from connectors.finance import pinelabs_plural as _  # noqa: F401, F811
from connectors.finance import zoho_books as _  # noqa: F401, F811
from connectors.finance import tally as _  # noqa: F401, F811
from connectors.finance import income_tax_india as _  # noqa: F401, F811
from connectors.finance import stripe as _  # noqa: F401, F811
from connectors.finance import quickbooks as _  # noqa: F401, F811

# HR connectors (8)
from connectors.hr import darwinbox as _  # noqa: F401, F811
from connectors.hr import greenhouse as _  # noqa: F401, F811
from connectors.hr import keka as _  # noqa: F401, F811
from connectors.hr import okta as _  # noqa: F401, F811
from connectors.hr import linkedin_talent as _  # noqa: F401, F811
from connectors.hr import epfo as _  # noqa: F401, F811
from connectors.hr import docusign as _  # noqa: F401, F811
from connectors.hr import zoom as _  # noqa: F401, F811

# Marketing connectors (9)
from connectors.marketing import hubspot as _  # noqa: F401, F811
from connectors.marketing import salesforce as _  # noqa: F401, F811
from connectors.marketing import google_ads as _  # noqa: F401, F811
from connectors.marketing import meta_ads as _  # noqa: F401, F811
from connectors.marketing import linkedin_ads as _  # noqa: F401, F811
from connectors.marketing import ahrefs as _  # noqa: F401, F811
from connectors.marketing import mixpanel as _  # noqa: F401, F811
from connectors.marketing import buffer as _  # noqa: F401, F811
from connectors.marketing import brandwatch as _  # noqa: F401, F811

# Ops connectors (7)
from connectors.ops import jira as _  # noqa: F401, F811
from connectors.ops import confluence as _  # noqa: F401, F811
from connectors.ops import zendesk as _  # noqa: F401, F811
from connectors.ops import servicenow as _  # noqa: F401, F811
from connectors.ops import pagerduty as _  # noqa: F401, F811
from connectors.ops import mca_portal as _  # noqa: F401, F811
from connectors.ops import sanctions_api as _  # noqa: F401, F811

# Comms connectors (8)
from connectors.comms import slack as _  # noqa: F401, F811
from connectors.comms import sendgrid as _  # noqa: F401, F811
from connectors.comms import twilio as _  # noqa: F401, F811
from connectors.comms import whatsapp as _  # noqa: F401, F811
from connectors.comms import google_calendar as _  # noqa: F401, F811
from connectors.comms import s3 as _  # noqa: F401, F811
from connectors.comms import github_connector as _  # noqa: F401, F811
from connectors.comms import langsmith_connector as _  # noqa: F401, F811


def _auto_register() -> None:
    """Register all loaded BaseConnector subclasses with the ConnectorRegistry."""
    for cls in BaseConnector.__subclasses__():
        if cls.name and cls.name not in ConnectorRegistry._connectors:
            ConnectorRegistry.register(cls)


_auto_register()
