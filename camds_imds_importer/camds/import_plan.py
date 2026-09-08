"""Validate an entire parsed tree before allocating any CAMDS IDs."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field

from .application_mapping import ApplicationMapping
from .material_classifications import describe, needs_choice


CAS_PATTERN = re.compile(r"\d{2,7}-\d{2}-\d")


def real_cas(node) -> str | None:
    """The IMDS "system" placeholder marks a system group, not a CAS number."""
    cas = (node.get("cas_number") or "").strip()
    return cas if CAS_PATTERN.fullmatch(cas) else None


# IMDS prints the Description column 132 characters wide and cuts whatever does
# not fit, mid-word: "...in combination with antimony com". Two unrelated
# reports carry names of exactly this length and none carries a longer one, so
# the cut is the format's, not one report's accident. A name of exactly this
# length is therefore not necessarily the substance's whole name, and must not
# be compared to a catalogue entry as though it were.
IMDS_NAME_LIMIT = 132


def truncated(name: str | None) -> bool:
    return len(str(name or "")) == IMDS_NAME_LIMIT


def substance_key(node) -> str:
    """Identity of a substance within one material.

    System groups all share the "system" placeholder while being different
    declarations ("Pigment portion, not to declare" is not "Misc., not to
    declare"), so identity falls back to the name and never to the placeholder.
    """
    return real_cas(node) or "name:" + (node.get("name") or "").strip().casefold()


def merge_duplicate_substances(material) -> list[str]:
    """Combine repeated substances of one material by adding their portions."""
    groups: dict[str, list] = {}
    for child in material.get("children", []):
        groups.setdefault(substance_key(child), []).append(child)
    merged, notes = [], []
    for group in groups.values():
        first = group[0]
        if len(group) > 1:
            _combine(first, group)
            notes.append(f"{material.get('name')}: merged {len(group)} entries of "
                         f"{first.get('name')} into one portion")
        merged.append(first)
    material["children"] = merged
    return notes


def _combine(target, group) -> None:
    if any(item.get("is_rest") for item in group):
        # Rest absorbs: the remainder is still the remainder.
        target["is_rest"] = True
        target["percentage"] = target["percentage_min"] = target["percentage_max"] = None
        return
    lows = highs = 0.0
    for item in group:
        if item.get("percentage") is not None:
            lows += float(item["percentage"])
            highs += float(item["percentage"])
        else:
            lows += float(item.get("percentage_min") or 0.0)
            highs += float(item.get("percentage_max") or item.get("percentage_min") or 0.0)
    if any(item.get("percentage") is None for item in group):
        target["percentage"] = None
        target["percentage_min"], target["percentage_max"] = lows, min(highs, 100.0)
    else:
        target["percentage"] = min(lows, 100.0)
        target["percentage_min"] = target["percentage_max"] = None


# Operators triaging a large report can raise this to see the whole list.
MAX_REPORTED_ERRORS = int(os.getenv("CAMDS_MAX_REPORTED_ERRORS", "50"))


@dataclass(frozen=True, slots=True)
class PlannedStep:
    """One CAMDS mutation the importer will perform, in execution order."""

    uid: str
    kind: str
    action: str
    name: str
    path: tuple[str, ...] = ()


def numeric(value, label, *, positive=False):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label}: numeric value required")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{label}: invalid value {value}")
    return result


def proportion(node):
    # IMDS prints "Rest 7.98": Rest is the portion type and the number is the
    # value it resolves to, not an independent Fixed percentage. CAMDS Rest
    # takes no value, so the number is informational and kept in the parsed data.
    if node.get("is_rest"):
        return ("rest",)
    modes = [node.get("percentage") is not None,
             node.get("percentage_min") is not None or node.get("percentage_max") is not None]
    if sum(modes) != 1:
        raise ValueError(f"{node['name']}: specify exactly one of Fixed, Range, Rest")
    if modes[0]:
        value = numeric(node["percentage"], node["name"])
        if value > 100:
            raise ValueError("Percentage must not exceed 100")
        return ("fixed", value)
    low = numeric(node.get("percentage_min"), node["name"])
    high = numeric(node.get("percentage_max"), node["name"])
    if not low <= high <= 100:
        raise ValueError("Invalid percentage interval")
    return ("range", low, high)


def children_mass(node) -> float | None:
    """What a Component's contents add up to, in grams, or None if unknowable.

    A child Component declares a mass per item and appears `quantity` times; a
    Semicomponent and a Material each count once. One child with no mass makes
    the total unknowable rather than smaller.
    """
    total = 0.0
    for child in node.get("children") or []:
        mass = child.get("weight_g")
        count = child.get("quantity") if child.get("node_type") == "COMPONENT" else 1
        if mass is None or count is None:
            return None
        try:
            total += float(mass) * float(count)
        except (TypeError, ValueError):
            return None
    return total


@dataclass
class ImportRequest:
    root: dict
    # Exact user-selected CAMDS ID/version per Material UID; never infer from IMDS ID.
    material_refs: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Classification the operator picked per Material UID, where IMDS printed
    # none. CAMDS asks for one when creating a Material and the importer will
    # not guess it, so without this the whole import stops for want of a class
    # on two label-paper rows.
    classifications: dict[str, str] = field(default_factory=dict)
    # Filled by snapshot(): repeated substances combined before any CAMDS work.
    merges: list[str] = field(default_factory=list)
    # Filled by validate(): accepted as declared, but the operator is told.
    warnings: list[str] = field(default_factory=list)
    # Filled by snapshot(): values IMDS left blank that were worked out here.
    derived: list[str] = field(default_factory=list)
    # Filled by snapshot(): classifications the operator picked for this run.
    chosen: list[str] = field(default_factory=list)

    def snapshot(self):
        clone = copy.deepcopy(self)
        clone.chosen = clone._apply_classifications()
        clone.merges = clone._normalise()
        clone.derived = clone._derive_masses()
        return clone

    def _apply_classifications(self) -> list[str]:
        """Write the operator's classification onto the Materials that lacked one.

        Only where the report gave nothing usable: a class IMDS did state is the
        supplier's statement about their own material, and a stale choice must
        not be able to overwrite it. Applied to the tree itself, so everything
        downstream - the validation, the wizard, the run fingerprint - sees one
        classification per Material and cannot disagree about where it came from.
        """
        notes: list[str] = []

        def visit(node):
            if node.get("node_type") == "MATERIAL":
                code = self.classifications.get(node.get("uid"))
                if code and needs_choice(node.get("classification")):
                    node["classification"] = code
                    notes.append(f"{node.get('name')}: IMDS printed no classification, "
                                 f"so {code}{': ' + describe(code) if describe(code) else ''} was chosen")
            for child in node.get("children", []):
                visit(child)

        visit(self.root)
        return notes

    def _derive_masses(self) -> list[str]:
        """Fill in a Component mass IMDS left blank from what it contains.

        IMDS prints no weight for some assembled rows - a solder paste applied
        to a joint, for instance - while every row around it carries one. The
        mass of a Component is what its contents weigh, so it is worked out
        rather than demanded from the operator, and said out loud: this is the
        one number in the tree that the report did not state.

        Depth first, so a Component whose own child was just filled in can be
        filled in too. A declared mass is never replaced, and a child with no
        mass leaves the parent blank rather than understating it.
        """
        notes: list[str] = []

        def visit(node):
            for child in node.get("children") or []:
                visit(child)
            if (node.get("node_type") != "COMPONENT" or node.get("weight_g") is not None
                    or not node.get("children")):
                return
            total = children_mass(node)
            if total:
                node["weight_g"] = total
                notes.append(f"{node.get('name')}: IMDS printed no mass, so the {total:g} g "
                             "its contents weigh was used")

        visit(self.root)
        return notes

    def _normalise(self) -> list[str]:
        notes: list[str] = []
        def visit(node):
            if node.get("node_type") == "MATERIAL" and node.get("uid") not in self.material_refs:
                if all(c.get("node_type") == "SUBSTANCE" for c in node.get("children", [])):
                    notes.extend(merge_duplicate_substances(node))
            for child in node.get("children", []):
                visit(child)
        visit(self.root)
        return notes

    def validate(self, mapping=None):
        errors = []
        warnings = []
        uids = set()
        mapping = mapping if mapping is not None else ApplicationMapping()
        material_uids = set()
        if self.root.get("node_type") not in ("COMPONENT", "MATERIAL"):
            raise ValueError("Import root must be Component or Material")

        def warn(message):
            """Reported to the operator, but not a reason to refuse the import."""
            if message not in warnings:
                warnings.append(message)

        def fail(message):
            # Report every blocker at once: fixing a 1500-node mapping one
            # error per Validate press is not workable.
            if message not in errors:
                errors.append(message)

        def measure(value, label, *, positive=False):
            try:
                return numeric(value, label, positive=positive)
            except (ValueError, TypeError) as exc:
                fail(str(exc))
                return None

        def visit(node, parent=None):
            uid, kind = node.get("uid"), node.get("node_type")
            name = node.get("name") or ""
            if not uid or uid in uids:
                fail("Missing or repeated node UID")
                return
            uids.add(uid)
            label = name.strip() or uid
            if not name.strip():
                fail(f"{label}: name required")
            elif len(name) > 100 and kind != "SUBSTANCE":
                # 100 is a maxlength read from the browser form's DOM and never
                # tested against the server, and the JSON API does not go
                # through that form. Reported rather than refused, by decision:
                # a name CAMDS truncates fails read-back, which compares the
                # saved name to this one exactly, and the browser backend still
                # refuses it before any id is allocated.
                warn(f"{label}: name is {len(name)} characters; the CAMDS form accepts 100. "
                     "Imported over the API as declared, and read back to catch truncation")
            if kind not in ("COMPONENT", "SEMICOMPONENT", "MATERIAL", "SUBSTANCE"):
                fail(f"{label}: {kind} import is not yet supported")
                return
            application = node.get("application_text") or node.get("application_id")
            if application and kind != "SUBSTANCE":
                # Only the per-substance rows of a Material Application tab have
                # been observed, so there is no control to write this into. The
                # standing rule for an application that cannot be placed is to
                # leave it unset and say so: an unset application is visibly
                # missing, a guessed one is a false regulatory statement.
                warn(f"{label}: a {kind}-level application "
                     f"{node.get('application_text') or node.get('application_id')!r} has no "
                     "discovered CAMDS control; left unset and reported")
            elif application and not mapping.resolve(name, node.get("application_text")):
                # An application is a regulatory statement. It is matched against
                # the options CAMDS offers for that substance; anything that does
                # not match exactly is left unset and reported, never guessed.
                warn(f"{label}: application {node.get('application_text')!r} is applied only if the "
                     "wording matches an option CAMDS offers; otherwise it is left unset")
            if kind == "COMPONENT":
                measure(node.get("weight_g"), name + " mass", positive=True)
                if parent:
                    quantity = measure(node.get("quantity"), name + " quantity", positive=True)
                    if quantity is not None and not quantity.is_integer():
                        fail(f"{label}: Component quantity must be an integer")
                if not node.get("children"):
                    fail(f"{label}: Component has no children")
            if kind == "SEMICOMPONENT":
                if parent and parent.get("node_type") == "SEMICOMPONENT":
                    # A Semicomponent inside a Semicomponent is declared by
                    # portion, exactly as a Material there is: the report gives
                    # "Contact Bimetal | 20291057 | Rest 98.74" and no mass.
                    try:
                        proportion(node)
                    except (ValueError, TypeError, KeyError) as exc:
                        fail(str(exc))
                else:
                    # CAMDS shows no Quantity field for a Semicomponent under a
                    # Component, so its mass counts once.
                    measure(node.get("weight_g"), name + " mass", positive=True)
                if not node.get("children"):
                    fail(f"{label}: Semicomponent has no children")
            if kind == "MATERIAL":
                material_uids.add(uid)
                in_semicomponent = bool(parent) and parent.get("node_type") == "SEMICOMPONENT"
                if in_semicomponent:
                    # CAMDS asks a Material under a Semicomponent for a Proportion,
                    # not a Mass. See SEMICOMPONENT_APPLICATION_DISCOVERY.md.
                    try:
                        proportion(node)
                    except (ValueError, TypeError, KeyError) as exc:
                        fail(str(exc))
                elif parent:
                    measure(node.get("weight_g"), name + " reference mass", positive=True)
                if uid in self.material_refs:
                    ref = self.material_refs[uid]
                    if len(ref) != 2 or not re.fullmatch(r"CA_\d+_\d+", ref[0]) or not re.fullmatch(r"\d+(?:\.\d+)?", ref[1]):
                        fail(f"{label}: exact CAMDS ID and version required")
                    return  # Existing Material composition is not overwritten.
                raw = node.get("classification")
                if needs_choice(raw):
                    fail(f"{label}: classification {raw or 'missing'!r} - choose one in the "
                         "Classification column, or map an existing CAMDS Material instead")
                    return
                children = node.get("children", [])
                if not children:
                    fail(f"{label}: new Material requires substances")
                elif any(c.get("node_type") != "SUBSTANCE" for c in children):
                    fail(f"{label}: nested Materials are not yet supported")
                else:
                    keys = [substance_key(c) for c in children]
                    if len(keys) != len(set(keys)):
                        fail(f"{label}: repeated substances were not combined before import")
                    try:
                        modes = [proportion(c) for c in children]
                    except (ValueError, TypeError, KeyError) as exc:
                        fail(str(exc))
                        modes = None
                    if modes:
                        rests = sum(m[0] == "rest" for m in modes)
                        low = sum(m[1] for m in modes if m[0] != "rest")
                        high = sum(m[-1] for m in modes if m[0] != "rest")
                        if rests > 1:
                            fail(f"{label}: a composition can declare Rest only once")
                        elif (rests and high > 100) or (not rests and not low - 0.001 <= 100 <= high + 0.001):
                            warn(f"{label}: composition totals {low:g}-{high:g}% instead of 100%; "
                                 "imported as declared")
            if kind == "SUBSTANCE":
                # Not every substance has a CAS; system groups never do. Such a
                # substance is looked up by name instead.
                if not real_cas(node) and not name.strip():
                    fail(f"{label}: a substance needs either a CAS number or a name to look up")
                elif not real_cas(node) and truncated(name):
                    # Said before the run rather than discovered 24 minutes into
                    # one: this is the name the catalogue lookup has to work
                    # with, and there is no CAS to fall back on.
                    warn(f"{label}: IMDS cut this name at {IMDS_NAME_LIMIT} characters, so the "
                         "report does not carry the whole of it. Looked up on the part that was "
                         "printed; if that does not identify one entry, answer it in "
                         "config/substance_mapping.json")
                elif not real_cas(node) and len(name) > 50:
                    # Also a DOM limit, on the search box. The API search takes
                    # the name as JSON. If CAMDS does cut it short the search
                    # returns no exact match, and an unresolved substance still
                    # stops the run, so nothing is imported on a guess.
                    warn(f"{label}: no CAS, and the name is {len(name)} characters; the CAMDS "
                         "search box accepts 50. Looked up over the API by the full name")
                if node.get("children"):
                    fail(f"{label}: Substance cannot contain child nodes")
                try:
                    proportion(node)
                except (ValueError, TypeError, KeyError) as exc:
                    fail(str(exc))
            number = node.get("material_number") if kind == "MATERIAL" else node.get("part_number")
            if number and len(number) > 50:
                warn(f"{label}: number is {len(number)} characters; the CAMDS form accepts 50. "
                     "Imported over the API as declared, and read back to catch truncation")
            allowed = {"COMPONENT": ("COMPONENT", "SEMICOMPONENT", "MATERIAL"),
                       # A Semicomponent holds Materials and nested Semicomponents.
                       "SEMICOMPONENT": ("SEMICOMPONENT", "MATERIAL")}.get(kind)
            for child in node.get("children", []):
                if allowed and child.get("node_type") not in allowed:
                    fail(f"{label}: a {kind} accepts only {'/'.join(allowed)} children")
                visit(child, node)
            if kind == "SEMICOMPONENT" and node.get("children"):
                try:
                    modes = [proportion(c) for c in node["children"]]
                except (ValueError, TypeError, KeyError):
                    modes = None
                if modes:
                    rests = sum(m[0] == "rest" for m in modes)
                    low = sum(m[1] for m in modes if m[0] != "rest")
                    high = sum(m[-1] for m in modes if m[0] != "rest")
                    if rests > 1:
                        fail(f"{label}: a Semicomponent can declare Rest only once")
                    elif (rests and high > 100) or (not rests and not low - 0.001 <= 100 <= high + 0.001):
                        warn(f"{label}: contents total {low:g}-{high:g}% instead of 100%; imported as declared")
            if kind == "COMPONENT" and node.get("children"):
                # Repeated names need no disambiguation: the importer addresses
                # tree nodes by document order, the order in which it added them.
                for c in node["children"]:
                    measure(c.get("weight_g"), (c.get("name") or "?") + " mass")
                    if c.get("node_type") == "COMPONENT":
                        measure(c.get("quantity"), (c.get("name") or "?") + " quantity")
                total = children_mass(node)
                expected = node.get("weight_g")
                if total is not None and expected is not None:
                    expected = float(expected)
                    if not math.isclose(total, expected, rel_tol=0.001, abs_tol=0.000001):
                        share = abs(total - expected) / expected * 100 if expected else float("inf")
                        warn(f"{label}: children total {total:g} g against {expected:g} g declared "
                             f"({share:.2f}% apart); imported as declared, CAMDS shows its own deviation")

        try:
            visit(self.root)
            if set(self.material_refs) - material_uids:
                fail("Material mapping contains unknown node UIDs")
            if self.root["uid"] in self.material_refs:
                fail("A Material root import must create a new Material, not map an existing root")
        except (TypeError, KeyError, RecursionError) as exc:
            fail(str(exc))
        self.warnings = warnings
        if errors:
            limit = MAX_REPORTED_ERRORS
            shown, extra = errors[:limit], len(errors) - limit
            summary = "Import blocked before CAMDS changes"
            if len(errors) > 1:
                summary += f" ({len(errors)} problems)"
            if extra > 0:
                shown.append(f"... and {extra} more")
            # One problem per line: messages contain their own punctuation, so a
            # "; " separator split them and produced orphaned fragments.
            raise ValueError(summary + ":" + chr(10) + chr(10).join(shown))
        return warnings

    @property
    def fingerprint(self):
        data = json.dumps({"root": self.root, "refs": self.material_refs}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(data.encode()).hexdigest()

    def node_applications(self):
        """Applications declared on something other than a Substance.

        CAMDS offers no control for these, so they are never written. They are
        listed here so a run reports each one rather than dropping it quietly.

        A Material mapped to an existing MDS is left out, the same way validate()
        leaves it out: its composition is not ours to write, so nothing about it
        was left unset by this run.
        """
        found = []

        def visit(n):
            if n.get("node_type") != "SUBSTANCE" and (n.get("application_text")
                                                      or n.get("application_id")):
                found.append(n)
            if n.get("node_type") == "MATERIAL" and n.get("uid") in self.material_refs:
                return
            for c in n.get("children", []):
                visit(c)

        visit(self.root)
        return found

    def substance_lookups(self):
        """One entry per distinct catalogue lookup this import will perform.

        5764 Substance nodes in the real tree resolve to about 200 searches: the
        same CAS or the same name appears again and again. Checking those few is
        what makes it possible to find out, in minutes and without creating
        anything, whether a run of several hours would stop on one of them.
        """
        seen, found = set(), []
        for material in self.materials():
            if material.get("uid") in self.material_refs:
                continue  # its composition is not ours to write
            for node in material.get("children", []):
                if node.get("node_type") != "SUBSTANCE":
                    continue
                cas = real_cas(node)
                key = ("cas", cas) if cas else ("name", str(node.get("name") or "").strip())
                if key not in seen:
                    seen.add(key)
                    found.append(node)
        return found

    def without(self, uids) -> list[str]:
        """Take Materials out of the tree, and say where each one was.

        A Material the catalogue cannot compose is not imported half-built; it
        is left out whole, so what reaches CAMDS is true as far as it goes.
        Everything after this - the step count, the paths, the child counts the
        read-back checks - is worked out from the tree, so removing the node is
        all that has to happen, and nothing downstream needs to know.

        A Component or Semicomponent that held nothing else goes with it: an
        empty one describes nothing, and CAMDS refuses a Semicomponent with no
        children anyway. Its position is reported too - a missing part of the
        tree that nobody was told about is worse than one that is.
        """
        removed: list[str] = []

        def visit(node, path):
            kept = []
            for child in node.get("children") or []:
                here = path + (str(child.get("name") or ""),)
                if child.get("node_type") == "MATERIAL" and child.get("uid") in uids:
                    removed.append(f"{child.get('name')}: not imported, at {' / '.join(here)}")
                    continue
                had = bool(child.get("children"))
                visit(child, here)
                if had and not child.get("children"):
                    removed.append(
                        f"{child.get('name')}: {str(child.get('node_type')).title()} left with "
                        f"nothing in it, so it was not imported either, at {' / '.join(here)}")
                    continue
                kept.append(child)
            node["children"] = kept

        if self.root.get("node_type") == "MATERIAL" and self.root.get("uid") in uids:
            raise ValueError(f"{self.root.get('name')} is the whole import, so skipping it "
                             "would leave nothing to do")
        visit(self.root, (str(self.root.get("name") or ""),))
        if self.root.get("node_type") != "MATERIAL" and not self.root.get("children"):
            raise ValueError(
                "Skipping those Materials would leave nothing to import: "
                + "; ".join(removed))
        return removed

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

    def plan(self) -> list[PlannedStep]:
        """Ordered steps run() will execute; the source of truth for progress totals."""
        steps: list[PlannedStep] = []
        for mat in self.materials():
            if mat["uid"] in self.material_refs:
                steps.append(PlannedStep(mat["uid"], "MATERIAL", "reuse_material", mat["name"]))
                continue
            steps.append(PlannedStep(mat["uid"], "MATERIAL", "create_material", mat["name"]))
            # Rest is attached last, mirroring run().
            for substance in sorted(mat["children"], key=lambda n: bool(n.get("is_rest"))):
                steps.append(PlannedStep(substance["uid"], "SUBSTANCE", "add_substance",
                                         substance["name"], (mat["name"],)))
        root = self.root
        if root["node_type"] == "MATERIAL":
            return steps
        steps.append(PlannedStep(root["uid"], "COMPONENT", "create_root", root["name"]))

        def walk(node, path):
            for child in node["children"]:
                kind = child["node_type"]
                if kind in ("COMPONENT", "SEMICOMPONENT"):
                    action = "add_component" if kind == "COMPONENT" else "add_semicomponent"
                    steps.append(PlannedStep(child["uid"], kind, action, child["name"], tuple(path)))
                    walk(child, path + [child["name"]])
                else:
                    steps.append(PlannedStep(child["uid"], "MATERIAL", "attach_material", child["name"], tuple(path)))

        walk(root, [root["name"]])
        return steps
