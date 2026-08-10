"""
sku.py — SKU normalization for sales-sync.

VENDORED COPY of the project-root `sku_utils.py`. sales-sync must stay
self-contained so the Lambda bundle has no dependency outside its folder. Keep
this in sync with the root file if that normalizer changes.

Single source of truth for SKU normalization.

Standard format: ES-NNN-LLL
  - Always uppercase
  - Always starts with ES-
  - Segments separated by single dashes
  - No spaces, asterisks, or other special characters
  - No (PDF) / (PP) suffixes — those belong in the `physical` column

Examples:
  '407IRO'           → 'ES-407-IRO'   (old Access format)
  'ES-407-IRO'       → 'ES-407-IRO'   (already correct)
  'ES 554 SOL'       → 'ES-554-SOL'   (spaces → dashes)
  'ES-542-*ROY'      → 'ES-542-ROY'   (remove invalid chars)
  'ES-548-CHF(PP)'   → 'ES-548-CHF'   (strip physical suffix)
  'ES-548-CHF(PDF)'  → 'ES-548-CHF'   (strip physical suffix)
  'es-407-iro'       → 'ES-407-IRO'   (lowercase → uppercase)
"""

import re


def normalize_sku(raw: str) -> str | None:
    """Return a normalized SKU string, or None if input is empty."""
    if not raw or not raw.strip():
        return None

    s = raw.strip().upper()

    # Everything the store appends to denote format/variant lives in or after a
    # parenthetical group, so strip from the first "(" to the end. Handles the
    # simple '(PP)'/'(PDF)' case plus compound and trailing-junk variants:
    #   'ES-548-CHF(PP)'      -> 'ES-548-CHF'
    #   'ES-504-UPA(PP, FPP)' -> 'ES-504-UPA'
    #   'ES-418-BFZ(PDF)-1'   -> 'ES-418-BFZ'
    s = re.sub(r'\s*\(.*$', '', s)

    # legacy dash-suffix form without parens, e.g. 'ES-548-CHF-PP'
    s = re.sub(r'-(PDF|PP)$', '', s)

    # trailing numeric disambiguator the store/import appends, e.g. '...-1'.
    # A real SKU segment ends in letters, never a bare number, so this is safe.
    s = re.sub(r'-\d+$', '', s)

    # spaces and underscores → dashes
    s = re.sub(r'[\s_]+', '-', s)

    # remove any character that isn't alphanumeric or a dash
    s = re.sub(r'[^A-Z0-9\-]', '', s)

    # collapse multiple consecutive dashes into one
    s = re.sub(r'-{2,}', '-', s)

    # strip any leading or trailing dashes
    s = s.strip('-')

    # old Access format: digits immediately followed by letters with no dash
    # e.g. '407IRO' → '407-IRO',  'ES-407IRO' → 'ES-407-IRO'
    s = re.sub(r'(?<=[0-9])(?=[A-Z])', '-', s)

    # ensure ES- prefix
    if not s.startswith('ES-'):
        s = 'ES-' + s

    return s


def is_valid_sku(sku: str) -> bool:
    """Return True if the SKU is already in standard form (no normalization needed)."""
    if not sku:
        return False
    return bool(re.match(r'^ES-[A-Z0-9]+-[A-Z0-9]', sku)) and sku == normalize_sku(sku)


def detect_physical(raw: str) -> bool | None:
    """
    Infer the physical flag from a raw SKU suffix.
      (PP)  → True   (paper pattern)
      (PDF) → False  (digital download)
      none  → None   (caller decides the default)
    """
    if not raw:
        return None
    raw = raw.strip().upper()
    # Read the format tag inside any parenthetical, even when trailing junk
    # follows it (e.g. '(PDF)-1', '(PP, FPP)'). Check PDF first so a compound
    # tag that names both resolves to digital only when PDF is present.
    if re.search(r'\(PDF', raw):
        return False
    if re.search(r'\(PP', raw):
        return True
    return None
