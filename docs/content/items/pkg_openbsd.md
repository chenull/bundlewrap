# OpenBSD package items

Handles packages installed by `pkg_add` on OpenBSD systems.

    pkg_openbsd = {
        "foo": {
            "installed": True,  # default
        },
        "bar": {
            "installed": True,
            "version": "1.0",
        },
        "baz": {
            "installed": False,
        },
    }

<br><br>

# Attribute reference

See also: [The list of generic builtin item attributes](../repo/items.py.md#builtin-item-attributes)

<hr>

## installed

`True` when the package is expected to be present on the system; `False` if it should be purged.

<hr>

## version

Optional version string. Required for packages that offer multiple variants (like nginx or sudo). Ignored when `installed` is `False`.
