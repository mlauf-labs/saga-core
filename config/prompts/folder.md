---
# Folder placement — custom instructions
#
# Add instructions below the closing --- to guide how the LLM sorts documents
# into the archive folder structure. Leave the area below empty to use the
# default folder placement behaviour.
#
# The SAGA_PROMPT_FOLDER environment variable can supply an inline value;
# SAGA_PROMPT_FOLDER_FILE can point to an alternative file path.
#
# WHAT TO INCLUDE
# ---------------
# - Required top-level folder domains and their purpose
# - Naming conventions (language, format, capitalisation)
# - Maximum allowed folder depth
# - Rules for specific document types or senders
# - Year-based sub-folder rules (year folders are already the default)
#
# EXAMPLES
# --------
#   Top-level folders must be in German: Finanzen, Gesundheit, Behörden,
#   Versicherungen, Fahrzeuge, Wohnen, Schule, Diverses.
#
#   Never create folders deeper than 4 levels.
#
#   All documents from Acme Corp go under Kunden/Acme regardless of type.
#
#   Insurance documents should be organised by insurer name, not by category:
#   Versicherungen/TK, Versicherungen/ADAC, Versicherungen/Allianz.
---
