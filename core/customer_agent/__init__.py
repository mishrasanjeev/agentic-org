# SPDX-License-Identifier: Apache-2.0
"""The bank's own agent for the bank's own customers.

Off by default (``AGENTICORG_CUSTOMER_AGENT_ENABLED``). On, a bank's channel
opens a customer session with a signed assertion (``sessions``), every turn
runs the conversational services under an autonomy ladder the customer and
the bank set per task (``autonomy``), every recommendation and action carries
a "why this" explanation (``explanations``), prepared actions wait for one
tap or a step-up (``turns``), and the customer sees and revokes every mandate
from one place (``permissions``).
"""
