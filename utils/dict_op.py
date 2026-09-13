def upper_keys(d):
    if isinstance(d, dict):
        return {k.upper(): upper_keys(v) for k, v in d.items()}
    elif isinstance(d, list):
        return [upper_keys(i) for i in d]
    else:
        return d

def lower_keys(d):
    if isinstance(d, dict):
        return {k.lower(): lower_keys(v) for k, v in d.items()}
    elif isinstance(d, list):
        return [lower_keys(i) for i in d]
    else:
        return d