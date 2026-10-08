# SPDX-License-Identifier: Apache-2.0
"""Role-based workbenches: what each role sees and may do, decided by the backend.

``definitions``: the workbenches (review officer, relationship manager,
investigator, supervisor) and their tabs, with the roles each tab needs and
which tabs are sensitive. ``access``: the workbenches and tabs a caller gets
from their platform role and their assignments, and the counts behind each
tab. ``assignments``: per-user workbench assignments an administrator keeps.
"""
