# Install the WatcheRobot Python SDK

This guide separates two different workflows:

- **Install from source** to test an unpublished PR, branch, or fixed commit,
  or to contribute to the SDK.
- **Install a release** when an Application developer needs a version already
  published to PyPI.

Use a dedicated Conda environment for either workflow; do not install into
`base`. The SDK supports Python 3.10–3.12. Python 3.11 is recommended for the
most predictable third-party dependency compatibility.

## Option 1: install from source

### 1. Create an isolated environment

```powershell
conda create -n watcherobot-source python=3.11 -y
conda activate watcherobot-source
python -m pip install --upgrade pip
```

The separate `watcherobot-source` name prevents an unpublished checkout from
being confused with a normal release installation.

### 2. Check out the exact source

```powershell
git clone https://github.com/orulink-ai/WatcheRobot_python_sdk.git
cd WatcheRobot_python_sdk
git fetch origin
```

To test a PR or branch, select its explicit remote branch:

```powershell
git switch --track origin/BRANCH_NAME
```

For an immutable acceptance snapshot, pin the checkout to a commit:

```powershell
git switch --detach COMMIT_SHA
```

Use `git status` and `git log -1 --oneline` to verify the selected source.
Cloning alone does not prove that the requested unpublished change is present.

### 3. Install the checkout

For SDK and CLI usage only:

```powershell
python -m pip install -e .
```

For SDK contribution, tests, and type checking:

```powershell
python -m pip install -e ".[test]"
```

An editable installation points directly to the current checkout. Python
source edits normally take effect without reinstalling. Reinstall when project
metadata or dependencies change.

### 4. Verify command ownership

```powershell
python -c "import sys, watcherobot; print(sys.executable); print(watcherobot.__file__)"
where.exe watcherobot
watcherobot --version
watcherobot --help
```

On macOS/Linux, use:

```bash
which watcherobot
```

Python and the CLI should come from `watcherobot-source`, while the module path
should point to the selected checkout. Before a dedicated version change is
made, an unpublished checkout may still report the previous release version;
verify the fixed commit, module path, and new behavior instead.

### 5. Validate an SDK contribution

```powershell
python -m pytest
python -m mypy src/watcherobot
```

Application developers do not need the `[test]` dependencies or this step.

## Option 2: install a published release

Once a version is published to PyPI, normal Application developers do not need
to clone this repository.

### 1. Create an isolated environment

```powershell
conda create -n watcherobot python=3.11 -y
conda activate watcherobot
python -m pip install --upgrade pip
```

### 2. Install from PyPI

Install the current release:

```powershell
python -m pip install watcherobot
```

Install a specific published version:

```powershell
python -m pip install "watcherobot==RELEASED_VERSION"
```

Upgrade later with:

```powershell
python -m pip install --upgrade watcherobot
```

### 3. Verify the installation

```powershell
python -c "import sys, watcherobot; print(sys.executable); print(watcherobot.__file__)"
where.exe watcherobot
watcherobot --version
watcherobot --help
```

On macOS/Linux, use:

```bash
which watcherobot
```

The first `where.exe watcherobot` result should be inside the active Conda
environment. If several executables exist, activate `watcherobot` before
changing or removing any other environment.

## TestPyPI prerelease acceptance

TestPyPI does not mirror all runtime dependencies. Install watcherobot from
TestPyPI while allowing dependencies to resolve from PyPI; do not use only
`-i https://test.pypi.org/simple/`:

```powershell
python -m pip install --pre --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ "watcherobot==PRE_RELEASE_VERSION"
```

This is a release-acceptance path, not the normal production installation.

## Next steps

```powershell
watcherobot robot setup
watcherobot robot status
watcherobot app init hello_robot
cd hello_robot
watcherobot app run
```

The generated Hello World plays `happy`, flashes the light when supported, and
then continuously showcases shuffled demo behaviors. Press `Ctrl+C` to stop it.

If the robot is already on the same Wi-Fi, do not reset its network. Open the
robot's `"Python SDK"` app and use `watcherobot robot pair <code>` instead.

## Runtime version after an upgrade

Before starting or reusing the Runtime, the CLI verifies both the control
protocol and SDK version. After a source or package upgrade, it automatically
stops an older SDK-owned Daemon and starts the matching one from the active
environment. This prevents old/new Application manifest errors such as
`unknown fields: supported_host_platforms`.

Confirm the foreground and background versions with:

```powershell
watcherobot --version
watcherobot daemon status
```

The `runtime.sdk_version` in `daemon status` should match the current CLI. If a
release from before automatic recovery already shows this error, run
`watcherobot daemon stop` once and then run the Application again.
