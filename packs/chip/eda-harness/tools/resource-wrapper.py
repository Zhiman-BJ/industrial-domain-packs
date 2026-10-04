#!/usr/bin/env python3
"""Apply a task's build budget to project-owned make/Verilator commands."""

import os
import sys
from pathlib import Path


def bounded_arguments(tool, args, jobs):
    flags = {"-j", "--jobs"} if tool == "make" else {"-j", "--build-jobs"}
    result, index = [], 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            result += args[index:]
            break
        if arg in flags:
            index += 1
            if index < len(args) and args[index].isdigit():
                index += 1
            result += ["-j", str(jobs)]
            continue
        if arg.startswith("-j") and arg[2:].isdigit():
            result += ["-j", str(min(jobs, int(arg[2:]) or jobs))]
        elif any(arg.startswith(flag + "=") for flag in flags if flag.startswith("--")):
            result += ["-j", str(jobs)]
        elif tool == "make" and arg.startswith("MAKEFLAGS="):
            result.append("MAKEFLAGS=-j" + str(jobs))
        elif tool == "make" and arg.startswith("NUM_CORES=") and "EDA_CPU_LIMIT" in os.environ:
            result.append("NUM_CORES=" + os.environ["EDA_CPU_LIMIT"])
        else:
            result.append(arg)
        index += 1
    if tool == "make" or any(a in args for a in ("--build", "--binary")):
        position = result.index("--") if "--" in result else len(result)
        result[position:position] = ["-j", str(jobs)]
    return result


def main():
    tool = Path(sys.argv[0]).name
    # Keep the upstream Perl driver's basename: it locates sibling binaries from it.
    actual = {"make": "/usr/bin/make", "verilator": "/usr/local/share/verilator/bin/verilator"}[tool]
    args = sys.argv[1:]
    if "EDA_BUILD_JOBS" in os.environ:
        try:
            jobs = int(os.environ["EDA_BUILD_JOBS"])
            if jobs < 1:
                raise ValueError()
        except ValueError:
            print("EDA_BUILD_JOBS must be a positive integer", file=sys.stderr)
            return 64
        args = bounded_arguments(tool, args, jobs)
        os.environ["MAKEFLAGS"] = "-j" + str(jobs)
    os.execv(actual, [actual, *args])


if __name__ == "__main__":
    sys.exit(main())
