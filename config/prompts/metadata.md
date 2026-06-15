---
# Metadata extraction — custom instructions
#
# Add instructions below the closing --- to guide how the LLM extracts
# metadata values from documents. Leave the area below empty to use the
# default extraction behaviour.
#
# The SAGA_PROMPT_METADATA environment variable can supply an inline value;
# SAGA_PROMPT_METADATA_FILE can point to an alternative file path.
#
# NOTE: Extracted metadata keys are always English snake_case (e.g.
# "invoice_number", "iban") regardless of this setting, because they are used
# as search and filter terms that must stay consistent across all documents.
#
# WHAT TO INCLUDE
# ---------------
# - Additional value types or identifiers specific to your domain
# - Rules for normalising values (e.g. date format, currency)
# - Fields that should always be extracted even if confidence is low
# - Fields that should be skipped (to reduce noise)
#
# EXAMPLES
# --------
#   Always extract the "Kassenzeichen" (payment reference) from German tax
#   authority letters, even if the confidence is below 0.8.
#
#   Extract the vehicle registration plate ("kennzeichen") from all vehicle-
#   related documents.
#
#   Do not extract individual line-item prices from invoices — only the total
#   amount and the VAT amount matter.
---
