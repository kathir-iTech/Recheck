# Recheck

**Release testing for QNX controllers that retry after a restart.**

A packaging controller requests one carton advance. The station advances, but the caller may lose the reply when a controller exits. Retrying the same command can advance the carton again. Recheck restarts the processes at a known checkpoint and makes the release decision from a separate station readback.

## Review the result

Open the recorded workbench, select either release outcome, and click **Replay checkpoints**. You can download the evidence for the exact run shown in the demo. Viewing that run requires only a browser. [Recorded workbench](https://kathir-itech.github.io/Recheck/) · [Release notes and captions](https://github.com/kathir-iTech/Recheck/releases/tag/v0.1.0)

| Same FORTE controller and command | Before first write | After first write | After station restart | After retry | Release |
| --- | ---: | ---: | ---: | ---: | --- |
| In-memory command history | 0 | 1 | 1 | 2 | Blocked |
| Persistent command history | 0 | 1 | 1 | 1 | Passed |

Both trials retain the simulated effect. The second trial also commits the command ID in the same SQLite transaction. An independent Modbus/TCP client reads the count through a separate connection before and after the station is replaced. The report includes both host station PIDs, both QNX process IDs, and the guest image SHA-256.

## Run on your machine

The verified setup uses a Windows host with QEMU x86-64, Python 3.14, the pinned PyModbus dependency, and the Linux-host QNX SDP 8.0 toolchain in WSL. The x86-64 target and 8250 serial driver must be installed. Build the guest images locally with your licensed SDK. Cloning and compiling inside the Linux filesystem avoids crossing the WSL filesystem boundary during the build. [Python dependency](requirements.txt)

For the run shown in the demo, build the **FORTE station-restart test** below. The native QNX suite is a separate, smaller test of lost replies and retries.

### Build the native QNX reference test

From a Linux or WSL SDK shell with the QNX x86-64 target and 8250 serial driver installed:

~~~bash
. /path/to/qnx800/qnxsdp-env.sh
bash build.sh recovery
~~~

If your SDK license is stored in a separate directory, pass that directory through the QNX SDK configuration or set QNX_RETRY_CONFIGURATION. Copy the resulting industrial-recovery.ifs beside serve.py on Windows.

~~~powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe serve.py --open
~~~

If QEMU is outside `PATH`, set `RECOVERY_QEMU` to the full path to `qemu-system-x86_64.exe`. In the browser, use **Run recovery test** under **Reference suite and CI policy**. Inspect the blind retry and its correction. The suite also tests an omitted retry and an unavailable observer.

### Build the FORTE station-restart test

In the same SDK shell, install CMake 3.30 or later, Ninja, Git, and Python 3. Run from the Recheck checkout.

~~~bash
recheck="$PWD"
git clone --branch 3.3.0 --single-branch https://github.com/eclipse-4diac/4diac-forte.git ../4diac-forte
git -C ../4diac-forte checkout --detach 6db22560feff98fc113ca7af2100e2c6dbc4a6e8
git -C ../4diac-forte apply --ignore-space-change "$recheck/forte-qnx-posix.patch"
cmake -S ../4diac-forte -B ../4diac-qnx-build -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$recheck/forte-qnx-toolchain.cmake" \
  -DCMAKE_BUILD_TYPE=Release -DFORTE_ARCHITECTURE=Posix -DBUILD_SHARED_LIBS=ON \
  -DFORTE_SUPPORT_BOOT_FILE=ON -DFORTE_RTTI_AND_EXCEPTIONS=OFF
cmake --build ../4diac-qnx-build
bash build-forte-proof.sh ../4diac-forte ../4diac-qnx-build
~~~

Copy `forte-protected.ifs` beside `serve.py` in the Windows checkout. Set up the Python environment using the PowerShell commands above. Open the workbench on port 8775 and click **Test station restart**. Both trials use that one image. Copy `forte-blind.ifs` as well to use **Run FORTE comparison**, which compares the two retry registers with the station left running. Under the test conditions, allow up to 90 seconds per guest trial before the runner reports a timeout.

## Run the release gate in CI

~~~powershell
.venv/Scripts/python.exe recovery.py --station-restart --output station-release.json
~~~

The command uses the same runner as the browser. Exit `0` means the persistent-history policy produced one advance. Exit `1` means the selected policy produced the wrong count. Exit `2` means the check could not establish a verdict. Pass `--station-policy volatile` to exercise the expected rejection. The in-memory control must reproduce the duplicate before a release decision can be verified.

## Where QNX runs

- [recovery_probe.c](recovery_probe.c) uses native QNX message passing for the reference test. It exercises MsgSend, MsgReceive, and MsgReply and records the caller's ESRCH when the controller exits before replying.
- [forte-supervisor.c](forte-supervisor.c) starts and replaces FORTE processes inside the QNX guest. [forte-workflow.py](forte-workflow.py) connects the upstream E_SPLIT and E_CTU example to our output block.
- [forte.py](forte.py) runs the local emulator and station replacement. [station_store.py](station_store.py) implements the two SQLite policies for the simulated effect.

The test adapter deliberately exits FORTE after an acknowledged write. During station replacement, the bridge holds the reply until a separate connection verifies the restored count. The Modbus frames travel via the guest serial bridge to a local PyModbus station. Physical actuator behavior, hardware timing, and operation through a power loss are outside the validation.

## License and dependencies

Recheck source is available under the [MIT license](LICENSE). Eclipse 4diac FORTE is built separately from its [EPL-2.0 upstream source](https://github.com/eclipse-4diac/4diac-forte/tree/3.3.0). The FORTE patch carries the upstream license. PyModbus is installed separately. Use QNX SDP 8.0 and its generated guest images under the applicable QNX terms.
