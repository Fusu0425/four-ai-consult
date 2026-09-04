# Windows packaging

Build the tested portable application from PowerShell:

```powershell
py -3.12 -m venv .venv-packaging
.\.venv-packaging\Scripts\python.exe -m pip install -r requirements-build.txt
.\packaging\build.ps1
```

Release builds require a clean official Python 3.12 environment named
`.venv-packaging`. The build script refuses to fall back to the development
environment and sanitizes `PATH` before PyInstaller runs. This prevents unrelated
native libraries from entering the app. The script also removes incompatible ICU
files injected by the Codex host runtime and verifies that none remain.

The build creates both `dist\FourAIConsult` and the shareable
`dist\FourAIConsult-0.7.7-portable.zip`. Run
`dist\FourAIConsult\FourAIConsult.exe` to smoke-test it. Test users can extract
the archive and launch the same executable without installing Python.

Before building, collect and review matching notices with
`.\.venv-packaging\Scripts\python.exe -m tools.collect_licenses licenses --qt-docs`.
The release includes MIT and third-party notices, a Chinese quick-start HTML page, a short pilot guide and
`release-info.json`. `tools.release_audit` gates both the directory and ZIP for
runtime/private data, validates ZIP CRC and writes a SHA256 sidecar. The build
gets its version from the Python package and restores its temporary test environment.

For isolated smoke tests set `FOUR_AI_DATA_DIR` to an absolute new directory.
The app stores `settings.ini` there too; do not point tests at a user's data.
Do not bypass SmartScreen or disable security software to make a build run.

To create the installer, install Inno Setup 6 and compile
`packaging\installer.iss` after the portable build succeeds. Production releases
should be code-signed. The target machine must have the current Microsoft Visual
C++ 2015-2022 Redistributable required by Qt WebEngine.

For a small invitation pilot where a tester cannot open ZIP files, run
`packaging\build-onefile.ps1`. It produces one self-extracting EXE and a SHA256
sidecar under `dist\onefile`. Startup is slower because Qt WebEngine is unpacked
into a temporary directory for each run; persistent user data still stays in
`%LOCALAPPDATA%\FourAIConsult`.
