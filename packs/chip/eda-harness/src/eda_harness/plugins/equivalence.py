"""Bounded native equivalence proofs with explicit functional cell/memory models."""
import re

from eda_harness.plugins.tools import Prepared, quoted


def normalized(p, sources, ref):
    # Functional library definitions are deliberately not read as blackboxes.
    files = " ".join(quoted(ref(r)) for r in [*p.cell_models, *sources])
    return [
        *[f"read_liberty -ignore_miss_func {quoted(ref(r))}" for r in p.cell_liberties],
        f"read_verilog -sv {files}",
        f"prep -top {p.top} -flatten",
        "async2sync",
        "memory_map",
        "opt_clean",
        "dffunmap",
        "check -assert",
    ]


def prepare(p, ref, script):
    gold, gate = normalized(p, p.gold_sources, ref), normalized(p, p.gate_sources, ref)
    if p.engine == "eqy":
        config = "\n".join([
            "[options]", "insbuf off", "",
            "[gold]", *gold, "", "[gate]", *gate, "",
            # Merge all matched nets: independent memory cutpoints can hide write changes.
            f"[collect {p.top}]", "group *", "",
            "[strategy bounded_sat]", "use sat", f"depth {p.depth}",
            f"timeout {p.partition_timeout_seconds}", "",
            "[strategy bounded_smt]", "use sby", f"engine smtbmc --unroll {p.solver}",
            f"depth {p.depth}", f"timeout {p.partition_timeout_seconds}", "",
        ])
        cfg = script("equivalence.eqy", config)
        # EQY is single-worker here, even if the runtime allows multiple compiler jobs.
        return Prepared([["eqy", "-j", "1", "-d", "equivalence", cfg]],
                        {"report.equivalence": "equivalence/logfile.txt"})
    body = "\n".join([
        *gold, "design -stash gold",
        *gate, "design -stash gate",
        f"design -copy-from gold -as gold {p.top}",
        f"design -copy-from gate -as gate {p.top}",
        "equiv_make gold gate equiv",
        "hierarchy -top equiv",
        f"equiv_simple -seq {p.depth}",
        f"equiv_induct -seq {p.depth}",
        "equiv_status -assert", "",
    ])
    return Prepared([["yosys", "-ql", "equivalence.log", "-s", script("equivalence.ys", body)]],
                    {"report.equivalence": "equivalence.log"})


def observe(p, text):
    if p.engine == "eqy":
        proven = re.findall(r"(?:Proved|Successfully proved) equivalence of partition\s+'([^']+)'", text)
        passed = bool(proven) and bool(re.search(r"Successfully proved (?:equivalence!|designs equivalent)", text))
        # A partition failure remains failure even if a partial/concatenated log contains a success.
        failed = bool(re.search(r"Failed to prove|Unproven partitions|Failed to partition|ERROR:", text))
        return passed and not failed, f"EQY: {len(set(proven))} proven partitions; complete proof required", {"proven_partitions": len(set(proven))}
    counts = re.findall(r"Of those cells (\d+) are proven and (\d+) are unproven\.", text)
    if not counts:
        raise ValueError("Missing native equivalence cell counts")
    proven, unproven = map(int, counts[-1])
    passed = proven > 0 and unproven == 0 and "Equivalence successfully proven!" in text
    return passed, f"Yosys equivalence: {proven} proven, {unproven} unproven", {"proven": proven, "unproven": unproven}
