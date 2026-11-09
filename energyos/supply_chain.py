"""CycloneDX SBOM ingestion, vulnerability applicability and fleet exposure mapping."""
from __future__ import annotations

import json
import re
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
MAX_SBOM_BYTES = 2_000_000


def version_key(v: str) -> tuple:
    """Natural version ordering that copes with suffixes such as OpenSSL '1.1.1k'."""
    return tuple(int(p) if p.isdigit() else p for p in re.findall(r"\d+|[a-zA-Z]+", v))


def parse_cyclonedx(raw: bytes) -> dict:
    if len(raw) > MAX_SBOM_BYTES:
        raise ValueError("SBOM too large")
    doc = json.loads(raw)
    if doc.get("bomFormat") != "CycloneDX":
        raise ValueError("Not a CycloneDX document")
    root = doc.get("metadata", {}).get("component", {})
    comps = []
    for c in doc.get("components", [])[:5000]:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        funcs = [p["value"] for p in c.get("properties", []) if p.get("name") == "energyos:function" and p.get("value")]
        comps.append({"name": str(c["name"])[:200], "version": str(c.get("version", ""))[:64],
                      "purl": c.get("purl"), "bom_ref": c.get("bom-ref"),
                      "functions": ",".join(funcs).split(",") if funcs else []})
    return {"spec_version": doc.get("specVersion"), "serial": doc.get("serialNumber"),
            "firmware_name": root.get("name"), "firmware_version": root.get("version"),
            "supplier": doc.get("metadata", {}).get("supplier", {}).get("name"),
            "components": comps, "dependencies": doc.get("dependencies", [])}


class SupplyChain:
    def __init__(self):
        self.sboms: dict[tuple[str, str], dict] = {}  # (model, version) -> parsed
        self.vulns = json.loads((FIXTURES / "vulnerabilities.json").read_text())
        # human (OEM / analyst) applicability decisions: (vuln, model, fw) -> {status, by, note}
        self.decisions: dict[tuple[str, str, str], dict] = {}
        for f in sorted((FIXTURES / "sboms").glob("*.json")):
            self.add_sbom(f.read_bytes(), f.name)

    def add_sbom(self, raw: bytes, filename: str) -> dict:
        p = parse_cyclonedx(raw)
        p["filename"] = filename
        self.sboms[(p["firmware_name"], p["firmware_version"])] = p
        return p

    def applicability(self, model: str, fw: str) -> list[dict]:
        sbom = self.sboms.get((model, fw))
        out = []
        for v in self.vulns:
            if sbom is None:
                continue
            for c in sbom["components"]:
                if c["name"] != v["component"]:
                    continue
                if not c["version"]:
                    status, method = "under_investigation", "name match, version unknown"
                elif version_key(c["version"]) < version_key(v["fixed_in"]):
                    status, method = "potentially_affected", f"version {c['version']} < fixed {v['fixed_in']}"
                else:
                    status, method = "not_affected", f"version {c['version']} >= fixed {v['fixed_in']}"
                d = self.decisions.get((v["id"], model, fw))
                if d:
                    status, method = d["status"], f"human decision by {d['by']}: {d['note']}"
                out.append({"vulnerability": v["id"], "title": v["title"], "hypothetical": v["hypothetical"],
                            "cvss": v["cvss"], "component": f"{c['name']}@{c['version']}", "status": status,
                            "method": method, "functions": v["functions"], "firmware": f"{model} {fw}"})
        return out

    def exposure(self, devices) -> list[dict]:
        rows = []
        for v in self.vulns:
            affected = []
            for d in devices:
                for a in self.applicability(d.model, d.firmware):
                    if a["vulnerability"] == v["id"] and a["status"] in ("potentially_affected", "confirmed_affected"):
                        affected.append({"device_id": d.id, "installation_id": d.installation_id,
                                         "firmware": f"{d.model} {d.firmware}", "status": a["status"]})
            rows.append({**v, "affected_devices": affected})
        return rows
