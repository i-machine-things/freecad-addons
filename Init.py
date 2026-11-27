# Installing this repository as an addon points FreeCAD's Addon Manager at this catalog: FreeCAD's
# official catalog plus the addons in addons.json. FreeCAD runs this file at every startup. It sets
# the catalog address that the Addon Manager reads, unless another non-official catalog is already
# set there. Uninstalling the addon leaves the address set: README.md says how to go back.


def _use_catalog():
    import FreeCAD

    catalog = "https://i-machine-things.github.io/freecad-addons/addon_catalog_cache.zip"
    official = "https://addons.freecad.org/addon_catalog_cache.zip"
    prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Addons")
    current = prefs.GetString("addon_catalog_cache_url", "")
    if current == catalog:
        return
    if current not in ("", official):
        FreeCAD.Console.PrintWarning(
            f"i-machine-things addons: the Addon Manager is set to another catalog, {current}, "
            "so it was left alone.\n"
        )
        return
    prefs.SetString("addon_catalog_cache_url", catalog)
    FreeCAD.Console.PrintMessage(f"i-machine-things addons: the Addon Manager now uses {catalog}\n")


_use_catalog()
del _use_catalog
