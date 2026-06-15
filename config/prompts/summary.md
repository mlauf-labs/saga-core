---
# Summary generation — custom instructions
#
# Add instructions below the closing --- to guide how the LLM writes the
# document title and summary. Leave the area below empty to use the default
# summarisation behaviour.
#
# The SAGA_PROMPT_SUMMARY environment variable can supply an inline value;
# SAGA_PROMPT_SUMMARY_FILE can point to an alternative file path.
#
# WHAT TO INCLUDE
# ---------------
# - Preferred language or tone (formal, concise, detailed)
# - Fields or topics that must always be mentioned in the summary
# - Naming conventions for titles (e.g. always include the sender's name)
# - Maximum or minimum length constraints beyond the default 1–3 sentences
#
# EXAMPLES
# --------
#   Always mention the sender or issuing organisation in the summary.
#
#   For invoices, always include the invoice number and total amount in the
#   summary, e.g. "Invoice INV-2026-042 from Acme Corp, total €1,234.56."
#
#   Titles must follow the pattern: "<Type> – <Party> – <Date>",
#   e.g. "Invoice – Acme Corp – March 2026".
---
