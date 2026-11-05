# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
# ruff: noqa: E501 - one cited figure per line reads better than wrapped strings
"""Seeded loaded hourly rates per role, each with its source (owner decision 2026-11-02).

United States (USD): the national median hourly wage from the BLS Occupational Employment and Wage
Statistics, May 2025 (bls.gov/oes, read through the BLS Public Data API, data type 08), times 1.43:
wages are 70.0% of total compensation for private industry workers in the BLS Employer Costs for
Employee Compensation, June 2026 (bls.gov/news.release/ecec.nr0.htm), and 1 / 0.700 = 1.43.

India (INR): the average monthly wage/salary earnings of regular wage/salaried employees by occupation
division from the MoSPI Periodic Labour Force Survey, Annual Report 2025 (January to December 2025),
Appendix A Table 51 (all India, rural and urban, all persons), divided by 208 hours (48 hours a week,
the Occupational Safety, Health and Working Conditions Code, 2020, sections 25 and 26, x 52 / 12) and
times 1.17 (employer EPF contribution 12% under the EPF and MP Act, 1952, plus gratuity of 15/26 of a
month's wage a year, 4.81%, under the Code on Social Security, 2020, section 53). 208 hours is the
statutory maximum, so the hourly rate is conservative; EPF is on basic wages, so 1.17 slightly overstates.

Every figure is a default a tenant administrator reviews and confirms or replaces.
"""

from __future__ import annotations

from decimal import Decimal

from core.insights.value import SeedRate

US_SOURCE = "BLS OEWS May 2025 national median hourly wage x 1.43 (BLS ECEC June 2026, private industry: wages 70.0% of compensation)"
IN_SOURCE = "MoSPI PLFS Annual Report 2025 (Jan-Dec 2025) Table 51 monthly earnings / 208 h x 1.17 (EPF 12% + gratuity 4.81%)"

US_RATES: dict[str, SeedRate] = {
    "finance_associate": SeedRate(Decimal("34.83"), f"{US_SOURCE}: Bookkeeping, Accounting, and Auditing Clerks (SOC 43-3031), median $24.36/h"),
    "accountant": SeedRate(Decimal("57.53"), f"{US_SOURCE}: Accountants and Auditors (SOC 13-2011), median $40.23/h"),
    "financial_analyst": SeedRate(Decimal("70.64"), f"{US_SOURCE}: Financial and Investment Analysts (SOC 13-2051), median $49.40/h"),
    "hr_associate": SeedRate(Decimal("52.21"), f"{US_SOURCE}: Human Resources Specialists (SOC 13-1071), median $36.51/h"),
    "recruiter": SeedRate(Decimal("52.21"), f"{US_SOURCE}: Human Resources Specialists (SOC 13-1071), median $36.51/h"),
    "marketing_associate": SeedRate(Decimal("54.15"), f"{US_SOURCE}: Market Research Analysts and Marketing Specialists (SOC 13-1161), median $37.87/h"),
    "marketing_analyst": SeedRate(Decimal("54.15"), f"{US_SOURCE}: Market Research Analysts and Marketing Specialists (SOC 13-1161), median $37.87/h"),
    "sales_associate": SeedRate(Decimal("48.12"), f"{US_SOURCE}: Sales Representatives of Services (SOC 41-3091), median $33.65/h"),
    "legal_associate": SeedRate(Decimal("43.24"), f"{US_SOURCE}: Paralegals and Legal Assistants (SOC 23-2011), median $30.24/h"),
    "compliance_associate": SeedRate(Decimal("55.50"), f"{US_SOURCE}: Compliance Officers (SOC 13-1041), median $38.81/h"),
    "operations_associate": SeedRate(Decimal("41.01"), f"{US_SOURCE}: Production, Planning, and Expediting Clerks (SOC 43-5061), median $28.68/h"),
    "it_engineer": SeedRate(Decimal("68.15"), f"{US_SOURCE}: Network and Computer Systems Administrators (SOC 15-1244), median $47.66/h"),
    "support_associate": SeedRate(Decimal("30.79"), f"{US_SOURCE}: Customer Service Representatives (SOC 43-4051), median $21.53/h"),
    "reviewer": SeedRate(Decimal("72.72"), f"{US_SOURCE}: General and Operations Managers (SOC 11-1021), median $50.85/h"),
}

IN_RATES: dict[str, SeedRate] = {
    "accountant": SeedRate(Decimal("216.37"), f"{IN_SOURCE}: NCO-2015 division 2, Professionals, Rs 38,466/month"),
    "financial_analyst": SeedRate(Decimal("216.37"), f"{IN_SOURCE}: NCO-2015 division 2, Professionals, Rs 38,466/month"),
    "legal_associate": SeedRate(Decimal("216.37"), f"{IN_SOURCE}: NCO-2015 division 2, Professionals, Rs 38,466/month"),
    "compliance_associate": SeedRate(Decimal("216.37"), f"{IN_SOURCE}: NCO-2015 division 2, Professionals, Rs 38,466/month"),
    "it_engineer": SeedRate(Decimal("216.37"), f"{IN_SOURCE}: NCO-2015 division 2, Professionals, Rs 38,466/month"),
    "reviewer": SeedRate(Decimal("283.54"), f"{IN_SOURCE}: NCO-2015 division 1, Managers, Rs 50,408/month"),
    "finance_associate": SeedRate(Decimal("136.63"), f"{IN_SOURCE}: NCO-2015 division 4, Clerical support workers, Rs 24,289/month"),
    "operations_associate": SeedRate(Decimal("136.63"), f"{IN_SOURCE}: NCO-2015 division 4, Clerical support workers, Rs 24,289/month"),
    "hr_associate": SeedRate(Decimal("147.23"), f"{IN_SOURCE}: NCO-2015 division 3, Technicians and associate professionals, Rs 26,175/month"),
    "recruiter": SeedRate(Decimal("147.23"), f"{IN_SOURCE}: NCO-2015 division 3, Technicians and associate professionals, Rs 26,175/month"),
    "marketing_associate": SeedRate(Decimal("147.23"), f"{IN_SOURCE}: NCO-2015 division 3, Technicians and associate professionals, Rs 26,175/month"),
    "marketing_analyst": SeedRate(Decimal("147.23"), f"{IN_SOURCE}: NCO-2015 division 3, Technicians and associate professionals, Rs 26,175/month"),
    "sales_associate": SeedRate(Decimal("87.42"), f"{IN_SOURCE}: NCO-2015 division 5, Service and sales workers, Rs 15,541/month"),
    "support_associate": SeedRate(Decimal("87.42"), f"{IN_SOURCE}: NCO-2015 division 5, Service and sales workers, Rs 15,541/month"),
}
