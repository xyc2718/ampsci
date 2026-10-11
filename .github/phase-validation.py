"""Reproduce both phase defects and check their independent fixes on clean dev."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time

variant = sys.argv[1]
source = Path(sys.argv[2]).resolve()
logs = Path("validation-logs").resolve()
logs.mkdir()
results = []


def run(name, command, expected=(0,)):
    started = time.monotonic()
    path = logs / (name + ".log")
    with path.open("w") as f:
        child = subprocess.Popen(command, cwd=source, stdout=f, stderr=subprocess.STDOUT)
        deadline = started + 120
        while child.poll() is None:
            if time.monotonic() >= deadline:
                record = dict(phase=name, utc=datetime.now(timezone.utc).isoformat(),
                              elapsed_s=time.monotonic()-started, pid=child.pid,
                              output_bytes=path.stat().st_size,
                              tail=path.read_text(errors="replace")[-1200:])
                with (logs / "monitor.jsonl").open("a") as m:
                    m.write(json.dumps(record) + "\n")
                print(json.dumps(record), flush=True)
                deadline = time.monotonic() + 3600
            time.sleep(2)
    result = dict(name=name, command=command, returncode=child.returncode,
                  elapsed_s=time.monotonic()-started)
    results.append(result)
    print(json.dumps(result), flush=True)
    print(path.read_text(errors="replace")[-8000:], flush=True)
    assert child.returncode in expected, result
    return path.read_text(errors="replace")


try:
    makefile = (source / "doc/Makefile").read_text()
    makefile = makefile.replace("MODE ?= release", "MODE ?= dev")
    makefile = makefile.replace("OPT ?= -O3", "OPT ?= -O2")
    makefile = makefile.replace("DEFAULT_MFLAGS = -j6", "DEFAULT_MFLAGS = -j2")
    (source / "Makefile").write_text(makefile)
    run("compiler", ["g++", "--version"])
    run("gsl", ["gsl-config", "--version"])
    if variant == "baseline":
        for sha, relative in [
            ("853d7e0ab5b0163d6e616af28ec639cecbec4d9f", "src/MBPT/StructureRad.tests.cpp"),
            ("ffaecb0c140eb01cb74dacd3352ff6d7625be591", "src/MBPT/Sigma2.tests.cpp"),
        ]:
            run("fetch-"+Path(relative).stem, ["git", "fetch", "--no-tags", "origin", sha])
            data = subprocess.check_output(["git", "show", sha+":"+relative], cwd=source)
            (source / relative).write_bytes(data)
    run("build", ["make", "-j2", "tests"])
    if variant == "baseline":
        for tag in ["SRPhase", "Sigma2Phase"]:
            text = run("regression-"+tag, ["./tests", "["+tag+"]"], tuple(range(1, 256)))
            assert "failed" in text and "No test cases matched" not in text
        command = ["./tests", "~[SRPhase]~[Sigma2Phase]", "--order", "lex", "--durations", "yes"]
    else:
        tag = "SRPhase" if variant == "sr-phase" else "Sigma2Phase"
        run("regression-"+tag, ["./tests", "["+tag+"]", "-s"])
        command = ["./tests", "--order", "lex", "--durations", "yes"]
    text = run("full-suite", command)
    assert "All tests passed" in text
    status = "passed"
except Exception:
    status = "failed"
    raise
finally:
    record = dict(status=status, variant=variant, results=results,
                  head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
                  finished_utc=datetime.now(timezone.utc).isoformat())
    if (source / "tests").exists():
        record["binary_sha256"] = hashlib.sha256((source / "tests").read_bytes()).hexdigest()
    (logs / "validation.json").write_text(json.dumps(record, indent=2)+"\n")
