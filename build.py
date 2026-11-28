#!/usr/bin/env python3
"""Build an Addon Manager catalog: FreeCAD's official catalog plus the addons in addons.json.

    python3 build.py                  build dist/ (the catalog zip, its hash and index.html)
    python3 build.py --readme         rewrite the addon list in README.md from addons.json
    python3 build.py --check-readme   fail if README.md's addon list is out of date

Needs Python 3.9 or newer and git, nothing else.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import html
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "addons.json"
README = ROOT / "README.md"
CACHE_JSON = "addon_catalog_cache.json"
CACHE_ZIP = "addon_catalog_cache.zip"
PACKAGE_NS = "https://wiki.freecad.org/Package_Metadata"
README_START = "<!-- addons:start -->"
README_END = "<!-- addons:end -->"
# A fixed timestamp inside the zip, so an unchanged catalog keeps the same hash and the Addon
# Manager does not download it again.
ZIP_TIME = (1980, 1, 1, 0, 0, 0)

ET.register_namespace("", PACKAGE_NS)


class BuildError(Exception):
    pass


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def load_config() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def listed_addons(config: dict):
    """Yield (section, addon_id, spec) for every addon in addons.json, in order."""
    seen = set()
    for section in config["sections"]:
        for addon_id, spec in section["addons"].items():
            if addon_id in seen:
                raise BuildError(f"{addon_id} is listed twice in addons.json")
            seen.add(addon_id)
            yield section, addon_id, spec


# ---------------------------------------------------------------------------------------------
# Downloads and git


def fetch(url: str, attempts: int = 4) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "freecad-addons-catalog"})
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except OSError as e:
            if attempt == attempts:
                raise BuildError(f"could not download {url}: {e}") from e
            time.sleep(10 * attempt)
    raise AssertionError("unreachable")


def load_official_catalog(url: str) -> dict:
    # The hash is checked so a half-written upload on the official server never gets merged.
    # A mismatch can also mean the catalog changed between the two downloads, so try again.
    for _ in range(3):
        data = fetch(url)
        expected = fetch(url + ".sha256").decode("utf-8").strip()
        if hashlib.sha256(data).hexdigest() == expected:
            break
        time.sleep(30)
    else:
        raise BuildError(f"{url} does not match its .sha256")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        catalog = json.loads(z.read(CACHE_JSON))
    if not isinstance(catalog, dict) or len(catalog) < 100:
        raise BuildError(f"{url} does not look like the official catalog")
    return catalog


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        if check:
            raise BuildError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return None
    return result.stdout.strip()


# ---------------------------------------------------------------------------------------------
# package.xml


def parse_package(xml_text: str) -> tuple[ET.Element, str]:
    root = ET.fromstring(xml_text)
    ns = root.tag[1:].partition("}")[0] if root.tag.startswith("{") else ""
    return root, ns


def q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}" if ns else tag


def text_of(root: ET.Element, ns: str, tag: str) -> str:
    return " ".join((root.findtext(q(ns, tag)) or "").split())


def icon_path(root: ET.Element, ns: str) -> str | None:
    """Find the icon the same way the Addon Manager does: the package's own icon, otherwise the
    first content item's, inside that item's subdirectory."""
    icon = text_of(root, ns, "icon")
    if icon:
        return icon
    content = root.find(q(ns, "content"))
    for item in content if content is not None else []:
        icon = text_of(item, ns, "icon")
        if icon:
            subdirectory = text_of(item, ns, "subdirectory")
            if subdirectory and subdirectory not in (".", "./"):
                return subdirectory.rstrip("/") + "/" + icon
            return icon
    return None


def adjust_package_xml(
    xml_text: str,
    tags: list[str],
    name_suffix: str = "",
    version_date: datetime.date | None = None,
) -> str:
    """Add the catalog's search tags and the section's name suffix. This copy is only what the
    Addon Manager shows: the addon installs with its own files, package.xml included."""
    root, ns = parse_package(xml_text)
    name = root.find(q(ns, "name"))
    if name_suffix and name is not None and name.text:
        if not name.text.strip().endswith(name_suffix.strip()):
            name.text = name.text.strip() + name_suffix
    existing = {(t.text or "").strip().lower() for t in root.findall(q(ns, "tag"))}
    for tag in tags:
        if tag.lower() not in existing:
            ET.SubElement(root, q(ns, "tag")).text = tag
    if version_date is not None:
        if root.find(q(ns, "version")) is None:
            d = version_date
            ET.SubElement(root, q(ns, "version")).text = f"{d.year}.{d.month}.{d.day}"
        if root.find(q(ns, "date")) is None:
            ET.SubElement(root, q(ns, "date")).text = version_date.isoformat()
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# ---------------------------------------------------------------------------------------------
# Catalog entries


def zip_url(repository: str, git_ref: str) -> str:
    if "gitlab" in repository or "salsa.debian.org" in repository or "framagit" in repository:
        name = repository.rstrip("/").rsplit("/", 1)[-1]
        return f"{repository}/-/archive/{git_ref}/{name}-{git_ref}.zip"
    return f"{repository}/archive/{git_ref}.zip"  # GitHub and Codeberg


def build_entry(addon_id: str, spec: dict, section: dict, config: dict, workdir: Path) -> dict:
    """Clone one addon and describe it the way FreeCAD's own catalog generator does."""
    repository, git_ref = spec["repository"], spec["git_ref"]
    clone = workdir / addon_id
    print(f"{addon_id}: cloning {repository} {git_ref}")
    git("clone", "--quiet", "--depth", "1", "--branch", git_ref, "--single-branch",
        repository, str(clone))
    commit_time = datetime.datetime.fromisoformat(
        git("log", "-1", "--format=%cd", "--date=iso-strict", cwd=clone)
    )

    own_package = clone / "package.xml"
    if own_package.is_file():
        if "package_xml" in spec:
            warn(f"{addon_id} now has its own package.xml, so {spec['package_xml']} is unused")
        xml_text = own_package.read_text(encoding="utf-8")
        version_date = None
    elif "package_xml" in spec:
        xml_text = (ROOT / spec["package_xml"]).read_text(encoding="utf-8")
        version_date = commit_time.date()
    else:
        warn(f"{addon_id} has no package.xml: the Addon Manager will show only its name")
        xml_text = None

    relative_icon = spec.get("icon")
    if xml_text is not None:
        root, ns = parse_package(xml_text)
        relative_icon = relative_icon or icon_path(root, ns)
        tags = [config["tag"]] + ([section["tag"]] if section.get("tag") else [])
        xml_text = adjust_package_xml(xml_text, tags, section.get("name_suffix", ""), version_date)

    icon_data = ""
    if relative_icon:
        icon_file = (clone / relative_icon).resolve()
        if not icon_file.is_relative_to(clone.resolve()) or not icon_file.is_file():
            warn(f"{addon_id}: icon {relative_icon} not found")
        else:
            icon_data = base64.b64encode(icon_file.read_bytes()).decode("ascii")

    return {
        "freecad_min": spec.get("freecad_min"),
        "freecad_max": spec.get("freecad_max"),
        "repository": repository,
        "git_ref": git_ref,
        "zip_url": zip_url(repository, git_ref),
        "note": None,
        "branch_display_name": spec.get("branch_display_name", git_ref),
        "metadata": {
            "package_xml": xml_text or "",
            "requirements_txt": read_optional(clone / "requirements.txt"),
            "metadata_txt": read_optional(clone / "metadata.txt"),
            "icon_data": icon_data,
        },
        "last_update_time": commit_time.isoformat(),
        "curated": True,
        "sparse_cache": False,
        "relative_cache_path": "",
        "git_hash": git("rev-parse", "HEAD", cwd=clone),
        "git_tag": git("describe", "--tags", "--exact-match", cwd=clone, check=False),
    }


def mark_official_entry(entries: list, section: dict, config: dict) -> None:
    tags = [config["tag"]] + ([section["tag"]] if section.get("tag") else [])
    for entry in entries:
        metadata = entry.get("metadata") or {}
        if metadata.get("package_xml"):
            metadata["package_xml"] = adjust_package_xml(
                metadata["package_xml"], tags, section.get("name_suffix", "")
            )


def summarize(addon_id: str, section: dict, spec: dict, entry: dict) -> dict:
    metadata = entry.get("metadata") or {}
    name, version, description = spec.get("name", addon_id), "", ""
    if metadata.get("package_xml"):
        root, ns = parse_package(metadata["package_xml"])
        name = text_of(root, ns, "name") or name
        version = text_of(root, ns, "version")
        description = text_of(root, ns, "description")
    icon = metadata.get("icon_data") or ""
    icon_type = "image/png" if icon.startswith("iVBOR") else "image/svg+xml"
    return {
        "id": addon_id,
        "section": section["id"],
        "name": name,
        "version": version,
        "description": description,
        "repository": entry.get("repository") or "",
        "branch": entry.get("branch_display_name") or entry.get("git_ref") or "",
        "updated": (entry.get("last_update_time") or "")[:10],
        "icon": f"data:{icon_type};base64,{icon}" if icon else "",
    }


def build(out_dir: Path) -> None:
    config = load_config()
    catalog = load_official_catalog(config["official_catalog"])

    # Each official entry names a zip stored next to the official catalog file. The Addon Manager
    # looks for it next to whichever catalog it loaded, which would be this site, so clear it:
    # the Addon Manager then downloads from the addon's own git host, as for a custom repository.
    for entries in catalog.values():
        for entry in entries:
            entry["relative_cache_path"] = ""

    for addon_id in config.get("hide", {}):
        if catalog.pop(addon_id, None) is None:
            warn(f"{addon_id} is in hide but not in the official catalog")

    summaries = []
    with tempfile.TemporaryDirectory() as tmp:
        for section, addon_id, spec in listed_addons(config):
            if spec.get("official"):
                if addon_id not in catalog:
                    raise BuildError(f"{addon_id} is not in the official catalog")
                mark_official_entry(catalog[addon_id], section, config)
            else:
                if addon_id in catalog:
                    raise BuildError(
                        f"{addon_id} is already in the official catalog: list it with "
                        '"official": true, or give it another ID'
                    )
                catalog[addon_id] = [build_entry(addon_id, spec, section, config, Path(tmp))]
            summaries.append(summarize(addon_id, section, spec, catalog[addon_id][0]))

    payload = json.dumps(catalog, indent="  ").encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo(CACHE_JSON, date_time=ZIP_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        z.writestr(info, payload)
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / CACHE_ZIP).write_bytes(data)
    # No trailing newline: the Addon Manager compares the downloaded text as it is.
    (out_dir / f"{CACHE_ZIP}.sha256").write_text(digest, encoding="utf-8")
    (out_dir / "index.html").write_text(render_index(config, summaries, len(catalog)), "utf-8")
    print(f"wrote {out_dir / CACHE_ZIP}: {len(catalog)} addons, sha256 {digest}")


# ---------------------------------------------------------------------------------------------
# README.md and index.html


def freecad_versions(spec: dict) -> str:
    """The FreeCAD versions an entry is limited to, for the README ("" for no limit)."""
    low, high = spec.get("freecad_min"), spec.get("freecad_max")
    if low and high:
        return f"{low} to {high}"
    if low:
        return f"{low} and later"
    return f"up to {high}" if high else ""


def readme_block(config: dict) -> str:
    lines = [README_START, ""]
    for section in config["sections"]:
        lines += [
            f"### {section['title']}",
            "",
            "| Addon | Installs as | Source | FreeCAD |",
            "|---|---|---|---|",
        ]
        for addon_id, spec in section["addons"].items():
            name = spec.get("name", addon_id)
            if spec.get("official"):
                source = "FreeCAD's official catalog"
            else:
                owner_repo = spec["repository"].rstrip("/").split("/", 3)[-1]
                source = f"[{owner_repo}]({spec['repository']}) `{spec['git_ref']}`"
            lines.append(f"| {name} | `Mod/{addon_id}` | {source} | {freecad_versions(spec)} |")
        lines.append("")
    hidden = config.get("hide", {})
    if hidden:
        lines += ["### Left out of the official catalog", ""]
        lines += [f"- **{addon_id}**: {reason}" for addon_id, reason in hidden.items()]
        lines.append("")
    lines.append(README_END)
    return "\n".join(lines)


def update_readme(check: bool) -> int:
    text = README.read_text(encoding="utf-8")
    start, end = text.find(README_START), text.find(README_END)
    if start < 0 or end < start:
        raise BuildError(f"README.md needs the {README_START} and {README_END} markers")
    updated = text[:start] + readme_block(load_config()) + text[end + len(README_END):]
    if updated == text:
        return 0
    if check:
        print("README.md is out of date: run python3 build.py --readme", file=sys.stderr)
        return 1
    README.write_text(updated, encoding="utf-8")
    print("updated README.md")
    return 0


def render_index(config: dict, summaries: list[dict], total: int) -> str:
    e = html.escape
    url = config["site"].rstrip("/") + "/" + CACHE_ZIP
    command = (
        'FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Addons")'
        f'.SetString("addon_catalog_cache_url", "{url}")'
    )
    undo = (
        'FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Addons")'
        '.RemString("addon_catalog_cache_url")'
    )
    parts = []
    for section in config["sections"]:
        cards = []
        for s in (s for s in summaries if s["section"] == section["id"]):
            icon = f'<img src="{e(s["icon"])}" alt="">' if s["icon"] else '<span class="noicon"></span>'
            facts = [f"<code>Mod/{e(s['id'])}</code>"]
            if s["version"]:
                facts.append(f"v{e(s['version'])}")
            if s["updated"]:
                facts.append(f"updated {e(s['updated'])}")
            source = (
                f'<a href="{e(s["repository"])}">{e(s["repository"].split("/", 3)[-1])}</a>'
                f" <code>{e(s['branch'])}</code>"
            )
            cards.append(
                f'<li class="card">{icon}<div><h3>{e(s["name"])}</h3>'
                f'<p>{e(s["description"]) or "<i>No description</i>"}</p>'
                f'<p class="facts">{" · ".join(facts)}</p><p class="facts">{source}</p></div></li>'
            )
        parts.append(f'<section><h2>{e(section["title"])}</h2><ul>{"".join(cards)}</ul></section>')
    hidden = "".join(
        f"<li><b>{e(k)}</b>: {e(v)}</li>" for k, v in config.get("hide", {}).items()
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FreeCAD addons</title>
<style>
:root {{ --bg:#f7f7f5; --card:#fff; --ink:#1d1d1b; --muted:#5f5f5a; --line:#deded8; --accent:#2f6f9f; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#1b1c1e; --card:#242629; --ink:#e8e8e4; --muted:#a3a39d; --line:#36383c; --accent:#7fb2dc; color-scheme: dark; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui, sans-serif; }}
main {{ max-width:56rem; margin:0 auto; padding:2rem 1rem 3rem; }}
h1 {{ font-size:1.6rem; margin:0 0 .25rem; }}
h2 {{ font-size:1.15rem; margin:2rem 0 .75rem; padding-bottom:.3rem; border-bottom:1px solid var(--line); }}
h3 {{ font-size:1rem; margin:0; }}
p {{ margin:.25rem 0; }}
a {{ color:var(--accent); }}
.lead {{ color:var(--muted); }}
ul {{ list-style:none; margin:0; padding:0; display:grid; gap:.75rem; }}
.card {{ display:flex; gap:1rem; background:var(--card); border:1px solid var(--line); border-radius:8px; padding:1rem; }}
.card img, .noicon {{ width:48px; height:48px; flex:none; object-fit:contain; }}
.noicon {{ border-radius:8px; background:var(--line); }}
.facts {{ color:var(--muted); font-size:.875rem; overflow-wrap:anywhere; }}
pre {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:.75rem; font-size:.8rem; white-space:pre-wrap; overflow-wrap:anywhere; }}
.setup ol {{ padding-left:1.25rem; }}
.hidden li {{ list-style:disc; margin-left:1.25rem; }}
</style>
</head>
<body>
<main>
<h1>FreeCAD addons</h1>
<p class="lead">An Addon Manager catalog: FreeCAD's official catalog plus the addons below, {total} addons in all. Search for <b>{e(config["tag"])}</b> in the Addon Manager to show only these, or <b>wip</b> for the work in progress.</p>
<div class="setup">
<h2>Use it</h2>
<ol>
<li>Open FreeCAD's Python console (View, Panels, Python console) and paste:
<pre>{e(command)}</pre></li>
<li>Restart FreeCAD and open the Addon Manager.</li>
</ol>
<p>To go back to only the official catalog:</p>
<pre>{e(undo)}</pre>
</div>
{"".join(parts)}
<section class="hidden"><h2>Left out of the official catalog</h2><ul>{hidden}</ul></section>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder")
    parser.add_argument("--readme", action="store_true", help="rewrite README.md's addon list")
    parser.add_argument("--check-readme", action="store_true", help="check README.md's addon list")
    args = parser.parse_args()
    try:
        if args.readme or args.check_readme:
            return update_readme(check=args.check_readme)
        build(args.out)
    except BuildError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
