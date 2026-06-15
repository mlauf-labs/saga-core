---
# Document type classification — custom instructions
#
# Add instructions below the closing --- to guide how the LLM classifies
# documents into doc-types. Leave the area below empty to use the default
# classification behaviour.
#
# The SAGA_PROMPT_DOCTYPE environment variable can supply an inline value;
# SAGA_PROMPT_DOCTYPE_FILE can point to an alternative file path.
#
# WHAT TO INCLUDE
# ---------------
# - Preferred doc-type names or naming conventions for your domain
# - Types that should always be reused vs. when new types are acceptable
# - Any domain-specific distinctions the LLM should make
#
# EXAMPLES
# --------
#   Always use "angebot" (not "offer" or "quotation") for supplier quotes.
#
#   Distinguish between "rechnung_eingehend" (incoming invoice from a supplier)
#   and "rechnung_ausgehend" (outgoing invoice to a customer).
#
#   Documents from the tax authority (Finanzamt) should always be classified
#   as "steuerbescheid", never as "letter" or "official_notice".
---
