"""MCA (Ministry of Corporate Affairs) company search script.

Automates the MCA V3 portal to search for company details by CIN or name.

This is a stub implementation — actual selectors will be calibrated
against the live MCA portal during QA.
"""

from __future__ import annotations

from typing import Any

SCRIPT_META = {
    "name": "mca_company_search",
    "description": "Search company details on MCA V3 portal",
    "category": "compliance",
    "required_params": ["search_query"],
}


async def run(page: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Search MCA portal for company information.

    Parameters
    ----------
    page : playwright.async_api.Page
        Playwright page instance.
    params : dict
        Required: ``search_query`` (CIN or company name).
        Optional: ``search_type`` (``cin`` or ``name``, default ``name``).

    Returns
    -------
    dict
        ``{found: bool, company: dict, details: dict}``
    """
    search_query = params.get("search_query", "")
    search_type = params.get("search_type", "name")

    if not search_query:
        return {
            "found": False,
            "company": {},
            "details": {"error": "Missing required param: search_query"},
        }

    # --- Step 1: Navigate to MCA company search ---
    await page.goto("https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do", wait_until="networkidle")

    # --- Step 2: Fill search form ---
    # Stub: actual selectors depend on portal DOM
    # if search_type == "cin":
    #     await page.fill("#CIN", search_query)
    # else:
    #     await page.fill("#companyName", search_query)
    # await page.click("#submitButton")
    # await page.wait_for_load_state("networkidle")

    # --- Step 3: Extract results ---
    # Stub: actual extraction depends on result page DOM
    # company_name = await page.text_content("#companyName")
    # cin = await page.text_content("#CIN")

    return {
        "found": False,
        "company": {},
        "details": {
            "status": "stub",
            "message": (
                "MCA company search script is a stub. "
                "Actual portal selectors will be calibrated during QA. "
                "CAPTCHA handling may require OCR integration or HITL."
            ),
            "search_query": search_query,
            "search_type": search_type,
        },
    }
