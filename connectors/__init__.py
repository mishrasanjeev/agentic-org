"""Connector layer — 42 typed adapters, auto-loaded for registry population."""

# Finance connectors (10)
from connectors.finance import oracle_fusion  # noqa: F401
from connectors.finance import sap  # noqa: F401
from connectors.finance import gstn  # noqa: F401
from connectors.finance import banking_aa  # noqa: F401
from connectors.finance import pinelabs_plural  # noqa: F401
from connectors.finance import zoho_books  # noqa: F401
from connectors.finance import tally  # noqa: F401
from connectors.finance import income_tax_india  # noqa: F401
from connectors.finance import stripe  # noqa: F401
from connectors.finance import quickbooks  # noqa: F401

# HR connectors (8)
from connectors.hr import darwinbox  # noqa: F401
from connectors.hr import greenhouse  # noqa: F401
from connectors.hr import keka  # noqa: F401
from connectors.hr import okta  # noqa: F401
from connectors.hr import linkedin_talent  # noqa: F401
from connectors.hr import epfo  # noqa: F401
from connectors.hr import docusign  # noqa: F401
from connectors.hr import zoom  # noqa: F401

# Marketing connectors (9)
from connectors.marketing import hubspot  # noqa: F401
from connectors.marketing import salesforce  # noqa: F401
from connectors.marketing import google_ads  # noqa: F401
from connectors.marketing import meta_ads  # noqa: F401
from connectors.marketing import linkedin_ads  # noqa: F401
from connectors.marketing import ahrefs  # noqa: F401
from connectors.marketing import mixpanel  # noqa: F401
from connectors.marketing import buffer  # noqa: F401
from connectors.marketing import brandwatch  # noqa: F401

# Ops connectors (7)
from connectors.ops import jira  # noqa: F401
from connectors.ops import confluence  # noqa: F401
from connectors.ops import zendesk  # noqa: F401
from connectors.ops import servicenow  # noqa: F401
from connectors.ops import pagerduty  # noqa: F401
from connectors.ops import mca_portal  # noqa: F401
from connectors.ops import sanctions_api  # noqa: F401

# Comms connectors (8)
from connectors.comms import slack  # noqa: F401
from connectors.comms import sendgrid  # noqa: F401
from connectors.comms import twilio  # noqa: F401
from connectors.comms import whatsapp  # noqa: F401
from connectors.comms import google_calendar  # noqa: F401
from connectors.comms import s3  # noqa: F401
from connectors.comms import github_connector  # noqa: F401
from connectors.comms import langsmith_connector  # noqa: F401
