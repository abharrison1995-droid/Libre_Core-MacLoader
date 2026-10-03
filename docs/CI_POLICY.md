# CI policy

Every Ubuntu and Windows / Python 3.11 and 3.14 matrix cell must pass functional tests and mypy. Wheel and source distribution smoke checks run outside the checkout on both hosts.

Ubuntu / Python 3.11 is the canonical branch-coverage gate, with the existing minimum of 79%. Every matrix cell measures and uploads coverage; host-specific branches make identical coverage percentages inappropriate across hosts. A passing secondary cell cannot replace a failing canonical gate. Coverage reductions on secondary cells must be reviewed using their uploaded reports.

Tests of portable contracts, injected command runners, synthetic acquisition and fail-closed safety run on both hosts. Only assertions requiring actual POSIX process-group APIs, POSIX permission bits, Linux privilege APIs or directory fsync semantics are host-gated, with a precise reason. Windows process-tree cleanup is tested explicitly. Linux removable discovery tests inject the existing platform parameter so their parsing and safety contracts remain covered on Windows.

Synthetic tools use executable scripts appropriate to the running host. These fixtures test integrity and acquisition contracts, and do not qualify production toolchains or physical writers.

Physical testing requires a green hosted commit plus the campaign readiness gates. Local Linux results do not establish Windows success or physical acceptance.
