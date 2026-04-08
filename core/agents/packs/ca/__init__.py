"""CA Firm Industry Pack — pre-configured agents and workflows for Chartered Accountants."""

from typing import Any

CA_PACK: dict[str, Any] = {
    "id": "ca-firm",
    "name": "Chartered Accountant Firm Pack",
    "description": (
        "Complete automation suite for CA firms managing multiple clients: "
        "GST filing, TDS compliance, bank reconciliation, month-end close, "
        "and audit trail."
    ),
    "version": "1.0.0",
    "pricing": {"inr_monthly_per_client": 4999, "usd_monthly_per_client": 59},
    "agents": [
        {
            "name": "GST Filing Agent",
            "domain": "finance",
            "description": (
                "Automates GSTR-1/3B/9 preparation, "
                "2A reconciliation, and DSC-signed filing"
            ),
            "tools": [
                "gstn:fetch_gstr2a",
                "gstn:push_gstr1_data",
                "gstn:file_gstr3b",
                "gstn:file_gstr9",
                "gstn:generate_einvoice_irn",
                "gstn:generate_eway_bill",
                "tally:get_trial_balance",
                "tally:generate_gst_report",
            ],
            "llm_model": "gpt-4o",
            "hitl_condition": "always_before_filing",
        },
        {
            "name": "TDS Compliance Agent",
            "domain": "finance",
            "description": (
                "Computes TDS, files Form 26Q/24Q, "
                "generates Form 16A, reconciles 26AS"
            ),
            "tools": [
                "income_tax:file_26q_return",
                "income_tax:file_24q_return",
                "income_tax:check_tds_credit_in_26as",
                "income_tax:download_form_16a",
                "income_tax:pay_tax_challan",
                "tally:get_ledger_balance",
                "tally:post_voucher",
            ],
            "llm_model": "gpt-4o",
            "hitl_condition": "always_before_filing",
        },
        {
            "name": "Bank Reconciliation Agent",
            "domain": "finance",
            "description": (
                "Auto-matches bank statement with books, "
                "flags old outstanding items"
            ),
            "tools": [
                "banking_aa:fetch_bank_statement",
                "banking_aa:check_account_balance",
                "banking_aa:get_transaction_list",
                "tally:get_ledger_balance",
                "tally:get_trial_balance",
            ],
            "llm_model": "gpt-4o-mini",
            "confidence_floor": 0.95,
        },
        {
            "name": "FP&A Analyst Agent",
            "domain": "finance",
            "description": (
                "Variance analysis, budget vs actual, "
                "MIS report generation"
            ),
            "tools": [
                "tally:get_trial_balance",
                "tally:get_ledger_balance",
                "zoho_books:get_profit_loss",
                "zoho_books:get_balance_sheet",
            ],
            "llm_model": "gpt-4o-mini",
        },
        {
            "name": "AR Collections Agent",
            "domain": "finance",
            "description": (
                "Tracks overdue invoices, sends reminders, "
                "escalates aging items"
            ),
            "tools": [
                "tally:get_ledger_balance",
                "zoho_books:list_invoices",
                "email:send",
            ],
            "llm_model": "gpt-4o-mini",
        },
    ],
    "workflows": [
        "gstr_filing_monthly",
        "tds_quarterly_filing",
        "bank_recon_daily",
        "month_end_close",
        "tax_calendar",
    ],
}
