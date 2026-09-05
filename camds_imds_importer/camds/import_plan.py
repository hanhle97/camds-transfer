"""Validate an entire parsed tree before allocating any CAMDS IDs."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field


def numeric(value, label, *, positive=False):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label}: numeric value required")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{label}: invalid value {value}")
    return result


def proportion(node):
    modes = [bool(node.get("is_rest")), node.get("percentage") is not None,
             node.get("percentage_min") is not None or node.get("percentage_max") is not None]
    if sum(modes) != 1:
        raise ValueError(f"{node['name']}: specify exactly one of Fixed, Range, Rest")
    if modes[0]:
        return ("rest",)
    if modes[1]:
        value = numeric(node["percentage"], node["name"])
        if value > 100:
            raise ValueError("Percentage must not exceed 100")
        return ("fixed", value)
    low = numeric(node.get("percentage_min"), node["name"])
    high = numeric(node.get("percentage_max"), node["name"])
    if not low <= high <= 100:
        raise ValueError("Invalid percentage interval")
    return ("range", low, high)


@dataclass
class ImportRequest:
    root: dict
    # Exact user-selected CAMDS ID/version per Material UID; never infer from IMDS ID.
    material_refs: dict[str, tuple[str, str]] = field(default_factory=dict)

    def snapshot(self):
        return copy.deepcopy(self)

    def validate(self):
        errors = []
        uids = set()
        material_uids = set()
        component_names = set()
        if self.root.get("node_type") not in ("COMPONENT", "MATERIAL"):
            raise ValueError("Import root must be Component or Material")

        def visit(node, parent=None):
            uid, kind, name = node.get("uid"), node.get("node_type"), node.get("name", "")
            if not uid or uid in uids:
                raise ValueError("Missing or repeated node UID")
            uids.add(uid)
            if not name.strip() or len(name) > 100:
                raise ValueError(f"{uid}: name required, maximum 100 characters")
            if kind not in ("COMPONENT", "MATERIAL", "SUBSTANCE"):
                raise ValueError(f"{name}: {kind} import is not yet supported")
            if node.get("application_id") or node.get("application_text"):
                raise ValueError(f"{name}: application mapping requires manual review")
            if kind == "COMPONENT":
                if name in component_names:
                    raise ValueError(f"{name}: repeated Component names cannot yet be selected unambiguously")
                component_names.add(name)
                numeric(node.get("weight_g"), name + " mass", positive=True)
                if parent:
                    quantity = numeric(node.get("quantity"), name + " quantity", positive=True)
                    if not quantity.is_integer():
                        raise ValueError(f"{name}: Component quantity must be an integer")
                if not node.get("children"):
                    raise ValueError(f"{name}: Component has no children")
            if kind == "MATERIAL":
                material_uids.add(uid)
                if parent:
                    numeric(node.get("weight_g"), name + " reference mass", positive=True)
                if uid in self.material_refs:
                    ref = self.material_refs[uid]
                    if len(ref) != 2 or not re.fullmatch(r"CA_\d+_\d+", ref[0]) or not re.fullmatch(r"\d+(?:\.\d+)?", ref[1]):
                        raise ValueError(f"{name}: exact CAMDS ID and version required")
                    return  # Existing Material composition is not overwritten.
                if node.get("classification") != "1.1.1":
                    raise ValueError(f"{name}: new Material only supports 1.1.1; map an existing CAMDS Material instead")
                children = node.get("children", [])
                if not children:
                    raise ValueError(f"{name}: new Material requires substances")
                if any(c.get("node_type") != "SUBSTANCE" for c in children):
                    raise ValueError(f"{name}: nested Materials are not yet supported")
                cas = [c.get("cas_number") for c in children]
                if len(cas) != len(set(cas)):
                    raise ValueError(f"{name}: duplicate CAS entries must be merged before import")
                modes = [proportion(c) for c in children]
                rests = sum(m[0] == "rest" for m in modes)
                low = sum(m[1] for m in modes if m[0] != "rest")
                high = sum(m[-1] for m in modes if m[0] != "rest")
                if rests > 1 or (rests and high > 100) or (not rests and not low - 0.001 <= 100 <= high + 0.001):
                    raise ValueError(f"{name}: composition does not balance to 100%")
            if kind == "SUBSTANCE":
                if not re.fullmatch(r"\d{2,7}-\d{2}-\d", node.get("cas_number") or ""):
                    raise ValueError(f"{name}: real CAS required; system/joker is unsupported")
                if node.get("children"):
                    raise ValueError(f"{name}: Substance cannot contain child nodes")
                proportion(node)
            if node.get("application_id") or node.get("application_text"):
                raise ValueError(f"{name}: application mapping requires manual review")
            number = node.get("material_number") if kind == "MATERIAL" else node.get("part_number")
            if number and len(number) > 50:
                raise ValueError(f"{name}: number exceeds 50 characters")
            for child in node.get("children", []):
                if kind == "COMPONENT" and child.get("node_type") not in ("COMPONENT", "MATERIAL"):
                    raise ValueError(f"{name}: only Component/Material children supported")
                visit(child, node)
            if kind == "COMPONENT":
                child_names = [c["name"] for c in node["children"]]
                if len(child_names) != len(set(child_names)):
                    raise ValueError(f"{name}: duplicate sibling names require disambiguation before import")
                total = sum(numeric(c.get("weight_g"), c["name"] + " mass") *
                            (numeric(c.get("quantity"), c["name"] + " quantity") if c["node_type"] == "COMPONENT" else 1)
                            for c in node["children"])
                expected = float(node["weight_g"])
                if not math.isclose(total, expected, rel_tol=0.001, abs_tol=0.000001):
                    raise ValueError(f"{name}: children total {total:g} g differs from {expected:g} g; review mass/quantity semantics")
        try:
            visit(self.root)
            if set(self.material_refs) - material_uids:
                raise ValueError("Material mapping contains unknown node UIDs")
            if self.root["uid"] in self.material_refs:
                raise ValueError("A Material root import must create a new Material, not map an existing root")
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))
        if errors:
            raise ValueError("Import blocked before CAMDS changes: " + "; ".join(errors))

    @property
    def fingerprint(self):
        data = json.dumps({"root": self.root, "refs": self.material_refs}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(data.encode()).hexdigest()

    def materials(self):
        result = []
        def visit(n):
            if n["node_type"] == "MATERIAL":
                result.append(n)
            else:
                for c in n.get("children", []):
                    visit(c)
        visit(self.root)
        return result
