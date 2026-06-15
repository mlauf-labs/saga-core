---
# Archive description
#
# This file is injected into every LLM prompt as context so the AI generates
# folder names, doc-type labels, and document titles that match the actual
# purpose of this archive.
#
# HOW TO USE
# ----------
# Write your archive description in plain text BELOW this front-matter block
# (after the closing --- line). Leave the area below empty to use generic
# LLM behaviour — the AI will still work, just without archive-specific context.
#
# The SAGA_STORE_DESCRIPTION environment variable always takes priority
# over this file.  SAGA_STORE_DESCRIPTION_FILE can point to an alternative
# file path.
#
# WHAT TO INCLUDE
# ---------------
# A good description covers four points:
#
#   1. WHAT    — purpose and owner of the archive
#   2. WHO     — names of people or teams involved (used in folder names)
#   3. STORED  — typical document categories that should appear
#   4. NOT     — explicit exclusions (prevents the LLM borrowing patterns from
#                unrelated domains, e.g. HR folders in a family archive)
#
# EXAMPLE — family archive:
#
#   This is the personal document archive of the Miller family.
#   Family members: Father Peter (born 1975), Mother Anna (born 1978),
#   Daughter Lena (born 2008), Son Tom (born 2012).
#
#   Documents stored here include: invoices, tax records, insurance policies
#   and claims, correspondence with authorities, school and education records,
#   medical documents, vehicle papers, rental agreements, warranties, and
#   purchase receipts.
#
#   The folder structure should be organised by life domain (e.g. Finance,
#   Health, Authorities, School, Vehicles, Home), then by person or category,
#   then by year.
#
#   This is a PRIVATE household, not a company. There are no business trips,
#   no HR department, no projects, no employees, and no business correspondence.
#
# EXAMPLE — HR department:
#
#   This is the document archive of the HR department at Mustermann GmbH,
#   responsible for approximately 120 employees across the Munich and Berlin
#   offices.
#
#   Documents stored here include: personnel files, employment contracts,
#   payslips, job applications, references, training certificates, sick notes,
#   parental leave requests, and works agreements.
#
#   The folder structure should be organised by process (Recruiting, Onboarding,
#   Payroll, Training, Offboarding) or by employee ID.
#
#   No financial or accounting documents belong here (those go to Accounting),
#   and no customer contracts (those go to Sales).
---
