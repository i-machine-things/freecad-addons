# FreeCAD addons

My FreeCAD addons in one place, installable from FreeCAD's Addon Manager. This is an Addon Manager catalog: FreeCAD's official catalog plus the addons listed below. Point FreeCAD at it and the Addon Manager lists all of them, with descriptions, versions and icons, and installs and updates them like any official addon.

Live list: <https://i-machine-things.github.io/freecad-addons/>

## Use it

Add this repository to the Addon Manager once, and it lists every addon below:

1. In the Addon Manager's settings, under **Custom repositories**, add `https://github.com/i-machine-things/freecad-addons` with branch `main`.
2. Close the settings. The Addon Manager now lists **i-machine-things addons** (FreeCAD 1.1 shows it as **freecad-addons**). Install it.
3. Restart FreeCAD.

Installing it points the Addon Manager at this catalog: its `Init.py` sets the catalog address when FreeCAD starts. The Addon Manager's settings have no field for that address.

Or set the address yourself instead: open the Python console (View, Panels, Python console), paste this line and restart FreeCAD:

```python
FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Addons").SetString("addon_catalog_cache_url", "https://i-machine-things.github.io/freecad-addons/addon_catalog_cache.zip")
```

To go back to only the official catalog, uninstall **i-machine-things addons** if you installed it, then paste this line and restart FreeCAD:

```python
FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Addons").RemString("addon_catalog_cache_url")
```

Works with the Addon Manager in FreeCAD 1.1 and 26.3. In the Addon Manager, search for **i-machine-things** to show only the addons below, or **wip** for the work in progress. Work in progress also has "(WIP)" after its name.

If one of these addons is set up as a custom repository in the Addon Manager's settings, remove it there. A custom repository takes priority over the catalog entry with the same name.

## Addons

An addon with versions in the FreeCAD column only shows in the Addon Manager of those FreeCAD versions.

<!-- addons:start -->

### Released

| Addon | Installs as | Source | FreeCAD |
|---|---|---|---|
| estimateWB (fork) | `Mod/estimateWB` | [i-machine-things/estimateWB](https://github.com/i-machine-things/estimateWB) `custom` |  |
| In-process Stock | `Mod/freecad-inprocess-stock` | [i-machine-things/freecad-inprocess-stock](https://github.com/i-machine-things/freecad-inprocess-stock) `main` | 26.3.0 and later |

### Work in progress

| Addon | Installs as | Source | FreeCAD |
|---|---|---|---|
| Weld | `Mod/Weld` | [i-machine-things/freecad-weld](https://github.com/i-machine-things/freecad-weld) `master` |  |
| DXF Export Units | `Mod/DXFExportUnits` | [i-machine-things/DXFExportUnits](https://github.com/i-machine-things/DXFExportUnits) `main` | 1.1.3 and later |

### Not mine, but I use them

| Addon | Installs as | Source | FreeCAD |
|---|---|---|---|
| Pimp My FreeCAD | `Mod/PimpMyFreeCAD` | [sedatkrn/PimpMyFreeCAD](https://github.com/sedatkrn/PimpMyFreeCAD) `main` |  |
| LiveSectionAnalyzer | `Mod/LiveSectionAnalyzer` | FreeCAD's official catalog |  |
| Lathe CAM | `Mod/FreeCAD-Lathe-Workbench` | [Maurone76/FreeCAD-Lathe-Workbench](https://github.com/Maurone76/FreeCAD-Lathe-Workbench) `main` |  |

### Left out of the official catalog

- **Estimate**: The original of estimateWB. Both install the same freecad.estimateWB Python package, so FreeCAD can only load one of them.

<!-- addons:end -->

## How it works

FreeCAD's Addon Manager reads one catalog file. The setting above points it here instead of at addons.freecad.org. The Addon Manager downloads `addon_catalog_cache.zip` again whenever the hash in `addon_catalog_cache.zip.sha256` changes.

Every hour, [the publish workflow](.github/workflows/publish.yml) runs `build.py`, which:

1. Downloads the official catalog and checks it against its hash.
2. Removes the addons under `hide` in `addons.json`.
3. Clones each addon listed in `addons.json` at its branch. From each clone it reads the same files FreeCAD's own catalog generator reads: `package.xml`, the icon, `requirements.txt` and `metadata.txt`. It also reads the time of the last commit, which is how the Addon Manager spots updates.
4. Adds the search tags, and "(WIP)" to the work in progress names. These changes are only in the catalog's copy of `package.xml`: each addon still installs with its own files.
5. Publishes the catalog zip, its hash and the index page to GitHub Pages, if the catalog changed.

Official addons normally download from a copy stored next to the official catalog. The Addon Manager looks for that copy next to whichever catalog it loaded, so this catalog clears that path, and the Addon Manager downloads official addons from their own git hosts instead.

If a build fails, nothing is published, and the Addon Manager keeps the last catalog it downloaded. That includes the official addons, which then stop getting updates until the build is fixed.

## Adding or moving an addon

Edit `addons.json`, run `python3 build.py --readme` to update the list above, and push. Each addon is keyed by its install folder name, which must not already be in the official catalog. Its fields:

- `repository` and `git_ref` (a branch or tag): where it installs from.
- `name`: its name in this README. The Addon Manager uses the name in its `package.xml`.
- `official: true`: list an addon from the official catalog in a section, instead of giving a repository.
- `package_xml`: a file in `metadata/`, for an addon with no `package.xml` of its own. The Addon Manager shows it before install, but it is never installed. The version comes from the date of the last commit.
- `icon`: an icon path in the addon's repository, when its `package.xml` names none.
- `freecad_min` and `freecad_max`: hide the addon from other FreeCAD versions.

`python3 build.py` builds the catalog into `dist/` locally. It needs Python 3.9 or newer and git.

## Publishing

- **Switching publishing on or off:** nothing publishes unless the repository variable `PUBLISH` is `true`. Set it to anything else to stop publishing.
- **Rehearsing:** a manual run of the workflow (Actions, Publish catalog, Run workflow) is a rehearsal unless you untick **rehearse**. A rehearsal builds the catalog and keeps it as the run's `catalog` artifact, so you can try it before publishing.
- **Keeping the hourly runs alive:** GitHub turns off scheduled workflows in a repository with no activity for 60 days. Any push turns them back on, or so does re-enabling the workflow on the Actions tab.
