#!/usr/bin/env python3
"""Generate all 42 connector implementations."""
import os, textwrap
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def w(p, c):
    full = os.path.join(BASE, p)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(c).lstrip("\n"))
    print(f"  {p}")

def gen_connector(path, name, category, auth_type, base_url, rpm, tools, schema="Default"):
    cls = "".join(w.capitalize() for w in name.split("_")) + "Connector"
    tool_regs = "\n".join(f'        self._tool_registry["{t}"] = self.{t}' for t in tools)
    tool_methods = "\n".join(f'''
    async def {t}(self, **params):
        """Execute {t} on {name}."""
        return await self._post("/{t.replace('_', '/')}", params)
''' for t in tools)

    w(path, f'''
    """{name.replace("_", " ").title()} connector — {category}."""
    from __future__ import annotations
    from typing import Any
    from connectors.framework.base_connector import BaseConnector

    class {cls}(BaseConnector):
        name = "{name}"
        category = "{category}"
        auth_type = "{auth_type}"
        base_url = "{base_url}"
        rate_limit_rpm = {rpm}

        def _register_tools(self):
{tool_regs}

        async def _authenticate(self):
            self._auth_headers = {{"Authorization": "Bearer <token>"}}
{tool_methods}
    ''')

CONNECTORS = [
    # HR
    ("connectors/hr/darwinbox.py", "darwinbox", "hr", "api_key_oauth2", "https://org.darwinbox.in/api", 200,
     ["get_employee","create_employee","run_payroll","get_attendance","post_leave","get_org_chart","update_performance","terminate_employee","transfer_employee","get_payslip"]),
    ("connectors/hr/greenhouse.py", "greenhouse", "hr", "api_key", "https://harvest.greenhouse.io/v1", 100,
     ["post_job","get_applications","move_stage","schedule_interview","send_offer","reject_candidate","get_scorecard","bulk_import_candidates"]),
    ("connectors/hr/keka.py", "keka", "hr", "api_key", "https://api.keka.com/v1", 100,
     ["get_employee","run_payroll","get_leave_balance","post_reimbursement","get_tds_workings","get_attendance_summary"]),
    ("connectors/hr/okta.py", "okta", "hr", "scim_oauth2", "https://org.okta.com/api/v1", 300,
     ["provision_user","deactivate_user","assign_group","remove_group","get_access_log","reset_mfa","list_active_sessions","suspend_user"]),
    ("connectors/hr/linkedin_talent.py", "linkedin_talent", "hr", "oauth2", "https://api.linkedin.com/v2", 50,
     ["post_job","search_candidates","send_inmail","get_applicants","get_analytics","get_job_insights"]),
    ("connectors/hr/epfo.py", "epfo", "hr", "dsc", "https://unifiedportal-emp.epfindia.gov.in/api", 10,
     ["file_ecr","get_uan","check_claim_status","download_passbook","generate_trrn","verify_member"]),
    ("connectors/hr/docusign.py", "docusign", "hr", "jwt", "https://na4.docusign.net/restapi/v2.1", 100,
     ["send_envelope","void_envelope","get_status","extract_completed_fields","download_signed_doc","create_template"]),
    ("connectors/hr/zoom.py", "zoom", "hr", "oauth2", "https://api.zoom.us/v2", 100,
     ["create_meeting","get_recording","cancel_meeting","get_attendance_report","add_panelist","get_transcript"]),
    # Finance
    ("connectors/finance/oracle_fusion.py", "oracle_fusion", "finance", "rest_soap", "https://org.oraclecloud.com/fscmRestApi/resources", 500,
     ["post_journal_entry","get_gl_balance","create_ap_invoice","approve_payment","get_budget","run_reconciliation","create_po","get_cash_flow","run_period_close","get_trial_balance"]),
    ("connectors/finance/sap.py", "sap", "finance", "odata_oauth2", "https://org.s4hana.cloud/sap/opu/odata/sap", 300,
     ["post_fi_document","get_account_balance","create_purchase_order","post_goods_receipt","run_payment_run","get_vendor_master","get_cost_center_data"]),
    ("connectors/finance/gstn.py", "gstn", "finance", "gsp_dsc", "https://gsp.adaequare.com/gsp/authenticate", 50,
     ["fetch_gstr2a","push_gstr1_data","file_gstr3b","file_gstr9","generate_eway_bill","generate_einvoice_irn","check_filing_status","get_compliance_notice"]),
    ("connectors/finance/banking_aa.py", "banking_aa", "finance", "aa_oauth2", "https://aa.finvu.in/api/v1", 100,
     ["fetch_bank_statement","initiate_neft","initiate_rtgs","check_account_balance","add_beneficiary","get_transaction_list","cancel_payment"]),
    ("connectors/finance/pinelabs_plural.py", "pinelabs_plural", "finance", "api_key", "https://api.pluralonline.com/api/v1", 200,
     ["create_payout","create_payment_link","initiate_refund","get_settlement_report","manage_subscription","get_payout_analytics"]),
    ("connectors/finance/zoho_books.py", "zoho_books", "finance", "oauth2", "https://books.zoho.in/api/v3", 100,
     ["create_invoice","record_expense","reconcile_bank_statement","generate_financial_report","get_balance_sheet","manage_chart_of_accounts"]),
    ("connectors/finance/tally.py", "tally", "finance", "tdl_rest", "http://localhost:9000/tally", 60,
     ["post_voucher","get_ledger_balance","generate_gst_report","export_tally_xml_data","get_trial_balance","get_stock_summary"]),
    ("connectors/finance/income_tax_india.py", "income_tax_india", "finance", "dsc", "https://www.incometax.gov.in/iec/foportal/api", 10,
     ["file_26q_return","file_24q_return","check_tds_credit_in_26as","download_form_16a","file_itr","get_compliance_notice","pay_tax_challan"]),
    ("connectors/finance/stripe.py", "stripe", "finance", "api_key", "https://api.stripe.com/v1", 300,
     ["create_charge","manage_subscription_lifecycle","create_payout","get_account_balance","manage_dispute","generate_financial_report"]),
    ("connectors/finance/quickbooks.py", "quickbooks", "finance", "oauth2", "https://quickbooks.api.intuit.com/v3", 100,
     ["create_invoice","record_payment","run_payroll_summary","generate_financial_report","sync_bank_transactions","get_pl_report"]),
    # Ops
    ("connectors/ops/jira.py", "jira", "ops", "oauth2", "https://org.atlassian.net/rest/api/3", 300,
     ["create_issue","update_issue","transition_issue","get_sprint_data","bulk_update","get_project_metrics","create_dashboard_widget"]),
    ("connectors/ops/confluence.py", "confluence", "ops", "oauth2", "https://org.atlassian.net/wiki/rest/api", 200,
     ["create_page","update_page_content","search_content_fulltext","publish_from_template","manage_space_permissions","get_page_tree"]),
    ("connectors/ops/zendesk.py", "zendesk", "ops", "api_token", "https://org.zendesk.com/api/v2", 200,
     ["create_ticket","update_ticket","apply_macro","get_csat_score","escalate_to_group","merge_tickets","get_sla_breach_status","get_ticket_history"]),
    ("connectors/ops/servicenow.py", "servicenow", "ops", "rest_oauth2", "https://org.service-now.com/api/now", 100,
     ["create_incident","submit_change_request","update_cmdb_ci","check_sla_status","fulfil_service_catalog_request","get_kb_article"]),
    ("connectors/ops/pagerduty.py", "pagerduty", "ops", "api_key", "https://api.pagerduty.com", 100,
     ["create_incident","trigger_alert_with_context","manage_on_call_schedule","run_automated_runbook","generate_postmortem_doc","acknowledge_incident"]),
    ("connectors/ops/mca_portal.py", "mca_portal", "ops", "dsc", "https://www.mca.gov.in/mcafoportal/api", 10,
     ["file_annual_return","complete_director_kyc","fetch_company_master_data","file_charge_satisfaction"]),
    ("connectors/ops/sanctions_api.py", "sanctions_api", "ops", "api_key", "https://api.sanctions.io/v2", 500,
     ["screen_entity_name","screen_transaction_parties","get_screening_alert","run_batch_screen","generate_screening_report"]),
    # Marketing
    ("connectors/marketing/hubspot.py", "hubspot", "marketing", "oauth2", "https://api.hubapi.com", 200,
     ["create_contact","send_marketing_email","create_deal","enrol_in_sequence","get_campaign_analytics","run_ab_test","segment_contact_list"]),
    ("connectors/marketing/salesforce.py", "salesforce", "marketing", "oauth2_soap", "https://org.my.salesforce.com/services/data/v60.0", 300,
     ["create_lead","update_opportunity_stage","score_contact","get_pipeline_report","run_custom_report","create_follow_up_task"]),
    ("connectors/marketing/google_ads.py", "google_ads", "marketing", "oauth2", "https://googleads.googleapis.com/v17", 200,
     ["get_campaign_performance_metrics","adjust_campaign_budget","pause_underperforming_adset","create_remarketing_audience","get_search_term_report"]),
    ("connectors/marketing/meta_ads.py", "meta_ads", "marketing", "oauth2", "https://graph.facebook.com/v21.0", 200,
     ["get_campaign_performance","reallocate_ad_budget","create_lookalike_audience","pause_ad_set","get_reach_and_frequency_data"]),
    ("connectors/marketing/linkedin_ads.py", "linkedin_ads", "marketing", "oauth2", "https://api.linkedin.com/v2", 100,
     ["create_sponsored_content_campaign","get_campaign_impressions","create_lead_gen_form","define_account_audience_targeting"]),
    ("connectors/marketing/ahrefs.py", "ahrefs", "marketing", "api_token", "https://api.ahrefs.com/v3", 100,
     ["get_keyword_ranking_history","identify_content_gaps_vs_competitor","get_backlink_profile","get_domain_rating","export_crawl_issues"]),
    ("connectors/marketing/mixpanel.py", "mixpanel", "marketing", "api_key", "https://mixpanel.com/api/2.0", 100,
     ["get_funnel_conversion_data","get_retention_cohort","query_event_data","create_user_cohort","export_raw_event_data"]),
    ("connectors/marketing/buffer.py", "buffer", "marketing", "oauth2", "https://api.bufferapp.com/1", 60,
     ["schedule_social_post","get_post_analytics","manage_publishing_queue","approve_draft_post"]),
    ("connectors/marketing/brandwatch.py", "brandwatch", "marketing", "oauth2", "https://api.brandwatch.com/projects", 60,
     ["get_brand_mentions","analyze_mention_sentiment","get_share_of_voice","set_volume_spike_alert","export_mention_report"]),
    # Comms
    ("connectors/comms/slack.py", "slack", "comms", "bolt_bot_token", "https://slack.com/api", 100,
     ["send_message","create_channel","post_formatted_alert","upload_file","set_channel_reminder","search_message_history"]),
    ("connectors/comms/sendgrid.py", "sendgrid", "comms", "api_key", "https://api.sendgrid.com/v3", 100,
     ["send_transactional_email","create_email_template","get_delivery_statistics","manage_suppression_list","validate_email_address"]),
    ("connectors/comms/twilio.py", "twilio", "comms", "api_key_secret", "https://api.twilio.com/2010-04-01", 100,
     ["make_outbound_call","send_sms","send_whatsapp_message","get_call_recording_url","trigger_tts_call_with_script"]),
    ("connectors/comms/whatsapp.py", "whatsapp", "comms", "meta_business", "https://graph.facebook.com/v21.0", 100,
     ["send_approved_template_message","send_interactive_message","send_media_message","get_delivery_status","manage_opt_out"]),
    ("connectors/comms/google_calendar.py", "google_calendar", "comms", "oauth2", "https://www.googleapis.com/calendar/v3", 100,
     ["create_calendar_event","check_participant_availability","book_meeting_room","cancel_event","find_optimal_meeting_slot"]),
    ("connectors/comms/s3.py", "object_storage", "comms", "gcs_service_account", "https://storage.googleapis.com", 1000,
     ["upload_document","download_document","list_bucket_objects","generate_presigned_download_url","delete_object","copy_object"]),
    ("connectors/comms/github_connector.py", "github", "comms", "pat_oauth2", "https://api.github.com", 100,
     ["create_pull_request","list_repository_issues","trigger_github_action_workflow","get_repository_statistics","create_release"]),
    ("connectors/comms/langsmith_connector.py", "langsmith", "comms", "api_key", "https://api.smith.langchain.com", 100,
     ["log_agent_trace","get_run_performance_stats","evaluate_output_quality","compare_prompt_versions","export_evaluation_dataset"]),
]

# __init__ files
for subdir in ["hr", "finance", "ops", "marketing", "comms"]:
    w(f"connectors/{subdir}/__init__.py", f'"""Connectors — {subdir}."""\n')

# Registry
w("connectors/registry.py", '''
"""Connector registry — register and discover connectors."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class ConnectorRegistry:
    _connectors: dict[str, type[BaseConnector]] = {}

    @classmethod
    def register(cls, connector_cls: type[BaseConnector]) -> None:
        cls._connectors[connector_cls.name] = connector_cls

    @classmethod
    def get(cls, name: str) -> type[BaseConnector] | None:
        return cls._connectors.get(name)

    @classmethod
    def all_names(cls) -> list[str]:
        return list(cls._connectors.keys())

    @classmethod
    def by_category(cls, category: str) -> list[type[BaseConnector]]:
        return [c for c in cls._connectors.values() if c.category == category]
''')

for args in CONNECTORS:
    gen_connector(*args)

print(f"[OK] {len(CONNECTORS)} connectors generated")

if __name__ == "__main__":
    print("Generating connectors...")
