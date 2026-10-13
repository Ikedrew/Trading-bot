# Stage A end-to-end qualification — preparation checkpoint

Status: NOT QUALIFIED; VM compatibility and raw offline evidence pending.
No coordinator, Python process, benchmark or test was executed during preparation.

## Existing measured evidence

Operator-returned connected synthetic benchmark, 180 shadow trades:

| Scenario | Wall seconds | Body GETs | Actual evaluator |
|---|---:|---:|---|
| COLD | 15.0703 | 10 | E3 |
| UNCHANGED | 1.5861 | 0 | None; 70 governed results replayed |
| INCREMENTAL | 4.7799 | 1 | E3 |

The operator reports the benchmark regression passed. These single-run timings
do not qualify the full registry, production scale, or production speed.

## Windows Home decision

Laptop: Windows 11 Home x64, Lenovo 82K2, HypervisorPresent=True.
Do not enable or install Windows Sandbox. The earlier Sandbox preparation
prototype is disabled at entry; its guest bootstrap is not the selected pathway.

The least-cost candidate is a disposable VirtualBox VM with a Linux guest,
avoiding a Windows edition upgrade and guest Windows license cost. This is a
candidate, not a verified boot on this laptop. HypervisorPresent does not prove
available RAM or successful boot. Keep host security features enabled; do not
disable VBS/Hyper-V to chase speed. If VirtualBox cannot boot with the existing
security configuration, try a supported alternate desktop hypervisor or a
separate disposable offline machine. Do not use production EC2.

Oracle documents independent network settings for all eight adapters; NAT and
host-only modes still expose network paths:
https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/networkingdetails.html
Shared folders expose host files even without networking:
https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/guestadditions.html

## Enforceable VM boundary

Before repository code or evidence enters the guest:

1. Provision a clean guest and dependencies, then power it off.
2. Remove all eight virtual NICs. Disable clipboard, drag/drop, shared folders,
   USB passthrough, remote display and host device passthrough. No AWS credentials,
   production checkout, host home, historical originals or host disks may be
   mapped. NAT and host-only networking are insufficient.
3. Use a disposable cloned guest disk and a read-only input ISO containing only
   an allowlisted Stage A source export, required authority policies and copied
   verified offline objects. Never attach a physical disk or historical originals
   as writable storage.
4. Use a separate small guest output disk. Run unprivileged, with two CPUs,
   bounded native threads and explicit memory/time/object/record limits.
5. Capture hypervisor configuration plus guest interface/mount checks before
   imports. Refuse execution if NICs, host shares, writable input mounts or
   production paths exist. A flag file or monkeypatch cannot establish isolation.
6. Export results after shutdown through a copied result disk. Preserve original
   evidence. Linux/virtualized timings are not directly comparable to Windows.

Manual read-only compatibility check from Stage A on the laptop:

    powershell.exe -NoProfile -File .\tools\check_qualification_environment.ps1

No installation, VM configuration mutation or launch is automated by this check.
A smoke boot and configuration evidence are needed before research commands.

## Genuine orchestration and blockers

The intended API is run_continuous_research_cycle() in research_loop.py, called
inside the isolated guest with explicit local source/state/path arguments.
Do not launch the coordinator CLI/subprocess. Preserve the real registry,
eligibility, unsupported states, bridge and projection code.

Import risks remain: _discover_runners imports registered modules; evaluation
identity construction also loads runners; find_spec can import parents.
Snapshot-only guards apply later and do not constrain native libraries, arbitrary
sockets or processes at import. Stage 4 paths derive from repository ROOT,
independent of cwd. SDK credential discovery must be contained by the VM.

The historical transfer has manifests, question results/cycle, scientific state,
registry and bridge receipts, but NO RAW EVIDENCE OBJECT BODIES. It supports the
read-only Lab but cannot support representative reevaluation. Do not synthesize
the missing population or fetch production S3.

Required input: a separately transferred verified offline object bundle preserving
original source authority, dataset roster, object metadata/identities, body
SHA-256, byte sizes and counts; a small authentic incremental bundle; and policies.
Preserve adapter authority strings rather than replacing them with a local class
name. Missing bodies/identity mismatches must fail closed.

Remaining implementation after VM and evidence availability: an authenticated
offline adapter; guest-only runner around the genuine API; bounded native-stage
and per-question instrumentation; cold/unchanged/incremental/recovery scenarios;
immutable/corrupt-artifact acceptance tests. Imports follow isolation preflight.
Report stage overlap and every unavailable evaluator honestly.

No runnable full-coordinator qualification is claimed at this checkpoint.
Research Lab proceeds independently. Add measured results after operator runs.
