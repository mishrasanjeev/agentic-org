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
            "prompt_file": "prompts/gst_filing.prompt.txt",
            "tools": [
                "gstn:fetch_gstr2a",
                "gstn:push_gstr1_data",
                "gstn:file_gstr3b",
                "gstn:file_gstr9",
                "gstn:generate_einvoice_irn",
                "gstn:generate_eway_bill",
                "zoho_books:get_trial_balance",
                "zoho_books:generate_gst_report",
                "zoho_books:list_invoices",
                "tally:get_trial_balance",
                "tally:generate_gst_report",
            ],
            "llm_model": "gpt-4o",
            "hitl_condition": "always_before_filing",
            "system_prompt_suffix": (
                "INTERACTIVE EXTRACTION (issue #440): When the user message "
                "already names gstin, period (month/quarter), or return type "
                "(GSTR-1/3B/9/2A), extract those values and call the relevant "
                "tool directly with those arguments. Do not ask the user to "
                "repeat values that are already in the prompt. Only ask for "
                "clarification when a required parameter is genuinely missing."
            ),
        },
        {
            "name": "TDS Compliance Agent",
            "domain": "finance",
            "description": (
                "Computes TDS, files Form 26Q/24Q, "
                "generates Form 16A, reconciles 26AS"
            ),
            "prompt_file": "prompts/tds_compliance.prompt.txt",
            "tools": [
                "zoho_books:calculate_tds",
                "zoho_books:get_ledger_balance",
                "income_tax_india:calculate_tds",
                "income_tax_india:file_form_26q",
                "income_tax_india:file_26q_return",
                "income_tax_india:file_24q_return",
                "income_tax_india:check_tds_credit_in_26as",
                "income_tax_india:download_form_16a",
                "income_tax_india:pay_tax_challan",
                "tally:get_ledger_balance",
                "tally:post_voucher",
            ],
            "llm_model": "gpt-4o",
            "hitl_condition": "always_before_filing",
            "system_prompt_suffix": (
                "INTERACTIVE EXTRACTION (issue #440): When the user message "
                "already provides amount, section (194A/194C/194H/194I/194J/"
                "194O/194Q), deductee_type (individual/huf/company/firm), "
                "pan_available, or period (e.g. 'April 2026'), extract those "
                "values and invoke calculate_tds directly with the extracted "
                "arguments. PAN can be requested separately ONLY if it is "
                "genuinely needed for the filing step. Never ask the user to "
                "repeat the amount, section, or period when they are already "
                "in the prompt — that is the BUG-17 failure pattern.\n\n"
                "Worked example for the canonical tester prompt 'Calculate "
                "TDS for vendor payment of INR 50,000 under Section 194C for "
                "April 2026 and file Form 26Q':\n"
                "  1. Extract: amount=50000, section=194C, period=April 2026\n"
                "  2. Call calculate_tds(amount=50000, section=\"194C\")\n"
                "  3. Report the computed tds_amount, rate, net_payable\n"
                "  4. For the Form 26Q step, ask only for PAN + deductee_type "
                "if those are genuinely missing — do NOT re-ask for amount/"
                "section."
            ),
        },
        {
            "name": "Bank Reconciliation Agent",
            "domain": "finance",
            "description": (
                "Auto-matches bank statement with books, "
                "flags old outstanding items"
            ),
            "prompt_file": "prompts/bank_reconciliation.prompt.txt",
            "tools": [
                "zoho_books:fetch_bank_statement",
                "zoho_books:check_account_balance",
                "zoho_books:get_transaction_list",
                "zoho_books:get_ledger_balance",
                "zoho_books:get_trial_balance",
                "zoho_books:reconcile_bank",
                "banking_aa:fetch_bank_statement",
                "banking_aa:check_account_balance",
                "banking_aa:get_transaction_list",
                "tally:get_ledger_balance",
                "tally:get_trial_balance",
            ],
            "llm_model": "gpt-4o-mini",
            "confidence_floor": 0.95,
            "system_prompt_suffix": (
                "INTERACTIVE EXTRACTION (issue #440): When the user message "
                "already names account_id, from_date, or to_date, call "
                "fetch_bank_statement / check_account_balance / "
                "get_transaction_list directly with those arguments. Don't "
                "ask the user to restate values already provided."
            ),
        },
        {
            "name": "FP&A Analyst Agent",
            "domain": "finance",
            "description": (
                "Variance analysis, budget vs actual, "
                "MIS report generation"
            ),
            "prompt_file": "prompts/fpa_analyst.prompt.txt",
            "tools": [
                "zoho_books:get_trial_balance",
                "zoho_books:get_ledger_balance",
                "tally:get_trial_balance",
                "tally:get_ledger_balance",
                "zoho_books:get_profit_loss",
                "zoho_books:get_balance_sheet",
            ],
            "llm_model": "gpt-4o-mini",
            "system_prompt_suffix": (
                "INTERACTIVE EXTRACTION (issue #440): When the user names a "
                "date range or period (e.g., 'Q1 FY26', 'April 2026'), call "
                "get_profit_loss / get_balance_sheet / get_trial_balance "
                "directly with the converted from_date / to_date / date "
                "arguments. Don't re-prompt for the period."
            ),
        },
        {
            "name": "AR Collections Agent",
            "domain": "finance",
            "description": (
                "Tracks overdue invoices, sends reminders, "
                "escalates aging items"
            ),
            "prompt_file": "prompts/ar_collections.prompt.txt",
            "tools": [
                "zoho_books:get_ledger_balance",
                "zoho_books:list_overdue_invoices",
                "tally:get_ledger_balance",
                "zoho_books:list_invoices",
                "sendgrid:send_email",
            ],
            "llm_model": "gpt-4o-mini",
            "system_prompt_suffix": (
                "INTERACTIVE EXTRACTION (issue #440): When the user names a "
                "customer_id, call list_invoices / list_overdue_invoices "
                "directly with that filter (the Zoho list_invoices method "
                "supports status/customer_id/date_start/date_end/page — do "
                "NOT pass an invoice-number filter, the connector will "
                "silently ignore it). When the user names a specific "
                "invoice (e.g. \"INV-123\"), fetch the customer's invoice "
                "list first and locate the target client-side before acting. "
                "Don't re-prompt for values already in the message."
            ),
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
