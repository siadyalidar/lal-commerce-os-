SKU_ALIASES = {
    'RO122-8L': 'RO-122-8L',
    'SH-10"TF-BG': 'SH-10TF-BG',
    'SH-10TF-BG-': 'SH-10TF-BG',
}


def normalize_sku(sku):
    if not sku:
        return sku
    return SKU_ALIASES.get(sku, sku)
