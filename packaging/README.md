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
`dist\FourAIConsult-0.7.14-portable.zip`. Run
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

To create the recommended installer, install Inno Setup 6 and compile
`packaging\installer.iss` after the portable build succeeds. If the repository
path is long, map it temporarily to a short drive letter before compiling so
the bundled third-party notice filenames remain accessible. Scan the resulting
installer with an up-to-date antivirus product before publishing. Production
releases should be Authenticode-signed. The target machine must have the current
Microsoft Visual C++ 2015-2022 Redistributable required by Qt WebEngine.

`packaging\build-onefile.ps1` is retained for local packaging experiments only.
Do not publish its self-extracting output as the default download: bundling
Python and Qt WebEngine into one unsigned executable can trigger antivirus
machine-learning detections. Use the standard installer or portable ZIP for
public testing.
