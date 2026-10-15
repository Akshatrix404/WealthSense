# WealthSense — Complete Setup Guide (From Scratch)

Everything needed to go from "nothing installed" to a running app with
all three components (Android app, ML training pipeline, blockchain +
backend) on your own machine. Windows-focused (PowerShell), with notes
for macOS/Linux where the steps differ.

---

## 0. What you're setting up

| Component | What it is | Needed for |
|---|---|---|
| `android-app/` | The WealthSense Android app  | Running the actual app |
| `ml-training/` | Python scripts that trained the 2 ML models | Only if you want to retrain/re-evaluate — the trained models are already committed |
| `blockchain/` | Solidity smart contract + Hardhat project | Only if you want to redeploy the contract |
| `backend/` | FastAPI bridge between the app and the blockchain | Only if you want the on-chain anchoring feature to actually work |

**Minimum to just run the app and see it work**: Section 1 (prerequisites) +
Section 2 (get the code) + Section 3 (Android app). Everything else is
optional depending on what you want to demo.

---

## 1. Prerequisites

### 1a. Git, JDK 17, Python — Windows

Open PowerShell and check if `winget` works:
```powershell
winget --version
```

**If that works**, install everything in one go:
```powershell
winget install --id Git.Git -e --source winget
winget install --id Microsoft.OpenJDK.17
winget install --id Python.Python.3.12
```

**If `winget` isn't recognized**, install the Microsoft Store "App
Installer" app first (search the Store for "App Installer" — must be
published by Microsoft, not a third-party Android emulator app with a
similar name), or download it directly from
https://learn.microsoft.com/en-us/windows/msix/app-installer/install-update-app-installer
and run:
```powershell
Add-AppxPackage -Path "$env:USERPROFILE\Downloads\Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle"
```
Then close PowerShell, reopen it, and retry `winget --version`. If it
*still* isn't recognized after a restart, use the full path directly:
```powershell
$winget = "C:\Program Files\WindowsApps\Microsoft.DesktopAppInstaller_<version>_x64__8wekyb3d8bbwe\winget.exe"
& $winget install --id Git.Git -e --source winget
& $winget install --id Microsoft.OpenJDK.17
& $winget install --id Python.Python.3.12
```
(Find your exact `<version>` folder name with
`Get-ChildItem "C:\Program Files\WindowsApps" -Filter "Microsoft.DesktopAppInstaller*"`.)

**Close PowerShell completely and reopen it**, then verify:
```powershell
git --version
java -version
python --version
```
If `python --version` fails, try `py --version` instead.

### 1b. Git, JDK 17, Python — macOS

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install git openjdk@17 python@3.12
echo 'export JAVA_HOME=$(/usr/libexec/java_home -v17)' >> ~/.zshrc
source ~/.zshrc
```

### 1c. Git, JDK 17, Python — Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y git openjdk-17-jdk python3.12 python3.12-venv
```

### 1d. Node.js (only needed for `blockchain/`)

Windows:
```powershell
winget install --id OpenJS.NodeJS.LTS
```
macOS: `brew install node`
Linux: `sudo apt install -y nodejs npm`

Verify: `node --version` (should be 18+)

### 1e. Android SDK command-line tools (only needed for `android-app/`)

**Windows:**
```powershell
New-Item -ItemType Directory -Force -Path "$env:LOCALAPPDATA\Android\sdk\cmdline-tools"
cd "$env:LOCALAPPDATA\Android\sdk\cmdline-tools"
Invoke-WebRequest -Uri "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip" -OutFile "cmdline-tools.zip"
Expand-Archive -Path "cmdline-tools.zip" -DestinationPath "."
Rename-Item -Path "cmdline-tools" -NewName "latest"
Remove-Item "cmdline-tools.zip"

[System.Environment]::SetEnvironmentVariable("ANDROID_HOME", "$env:LOCALAPPDATA\Android\sdk", "User")
$paths = @(
    "$env:LOCALAPPDATA\Android\sdk\cmdline-tools\latest\bin",
    "$env:LOCALAPPDATA\Android\sdk\platform-tools",
    "$env:LOCALAPPDATA\Android\sdk\emulator"
)
$currentPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
[System.Environment]::SetEnvironmentVariable("Path", "$currentPath;$($paths -join ';')", "User")
```

**Close PowerShell, reopen it.** If `sdkmanager --version` doesn't work
after that (a real PATH-registration quirk hit during development), use
the full path instead for every command below:
```powershell
$sdkmanager = "$env:LOCALAPPDATA\Android\sdk\cmdline-tools\latest\bin\sdkmanager.bat"
& $sdkmanager --version
```

Accept licenses and install components:
```powershell
& $sdkmanager --licenses
& $sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0" "emulator" "system-images;android-34;google_apis;x86_64"
```

**macOS/Linux**: same idea, swap the download URL for
`commandlinetools-mac-11076708_latest.zip` or
`commandlinetools-linux-11076708_latest.zip`, and use `~/Android/sdk`
instead of `$env:LOCALAPPDATA\Android\sdk`.

### 1f. Emulator setup (Windows-specific virtualization step)

Check what your Windows edition supports:
```powershell
Get-WindowsOptionalFeature -Online | Where-Object { $_.FeatureName -like "*Hyper*" -or $_.FeatureName -like "*Virtual*" }
```

If you see `HypervisorPlatform` in the list (common on Windows
Home, where full Hyper-V isn't available), enable that:
```powershell
dism.exe /Online /Enable-Feature:HypervisorPlatform /All /NoRestart
Restart-Computer
```

After restart, create and boot an emulator:
```powershell
$avdmanager = "$env:LOCALAPPDATA\Android\sdk\cmdline-tools\latest\bin\avdmanager.bat"
& $avdmanager create avd -n wealthsense_emulator -k "system-images;android-34;google_apis;x86_64" --device "pixel_6"
# if asked "custom hardware profile", type: no

$emulator = "$env:LOCALAPPDATA\Android\sdk\emulator\emulator.exe"
& $emulator -avd wealthsense_emulator
```
Leave this running in its own window.

---

## 2. Get the code

You have two files: `wealthsense.zip` and `wealthsense-history.bundle`.
**Use the bundle** — it has full git history (every commit, showing
exactly what was forked vs. built new); the zip is just a flat snapshot.

```powershell
cd ~
git clone "C:\path\to\wealthsense-history.bundle" wealthsense
cd wealthsense
git log --oneline
```
You should see ~20 commits, starting with `Initial import...` at the bottom.

---

## 3. Android app

```powershell
cd wealthsense\android-app
.\gradlew.bat assembleDebug
```

First run downloads a lot (Gradle, dependencies) — **10-25+ minutes**,
don't interrupt it. Watch for `BUILD SUCCESSFUL` or `BUILD FAILED`.

Once it builds, with your emulator running (Section 1f):
```powershell
$adb = "$env:LOCALAPPDATA\Android\sdk\platform-tools\adb.exe"
& $adb devices
# should list your emulator

.\gradlew.bat installStandardDebug
& $adb shell monkey -p com.wealthsense.app -c android.intent.category.LAUNCHER 1
```

Switch to the emulator window — the app should open, walk you through
adding a test bank account, then land on the Home screen.

### Try the built-in demo feature

Tap the **`•••`** menu (top right of Home) → **"Send Test SMS"**. This
generates a realistic fake bank SMS and runs it through the actual
parsing/categorization/anomaly-detection pipeline — no real SMS or
manual data entry needed to see the app work. Press it a few times to
build up transaction history; after ~30 days worth of simulated history
(or real usage), the "Spend Forecast" card on Home will show a real
prediction instead of the cold-start message.

**macOS/Linux**: same commands, replace `.\gradlew.bat` with
`./gradlew` and adjust the SDK paths per Section 1e's platform note.

---

## 4. ML training pipeline (optional — models are already trained and committed)

```powershell
cd wealthsense\ml-training
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
```

Run any script, in this order if starting from scratch:
```powershell
.\venv\Scripts\python.exe generate_forecast_data.py       # synthetic dataset
.\venv\Scripts\python.exe generate_anomaly_labels.py       # labeled anomaly copy
.\venv\Scripts\python.exe eda.py                             # sanity-check plots
.\venv\Scripts\python.exe train_forecaster.py               # trains the LSTM
.\venv\Scripts\python.exe evaluate_forecaster.py             # real test-set MAE/RMSE
.\venv\Scripts\python.exe export_forecaster_tflite.py        # -> exports/forecaster.tflite
.\venv\Scripts\python.exe train_anomaly_detector.py         # trains Isolation Forest
.\venv\Scripts\python.exe export_anomaly_detector_onnx.py   # -> exports/isolation_forest.onnx
```

If you retrain and get different model files, copy the new exports into
the Android app's assets before rebuilding:
```powershell
Copy-Item exports\forecaster.tflite ..\android-app\app\src\main\assets\forecaster.tflite -Force
Copy-Item exports\isolation_forest.onnx ..\android-app\app\src\main\assets\isolation_forest.onnx -Force
```

**macOS/Linux**: `python3 -m venv venv`, `./venv/bin/pip install ...`,
`./venv/bin/python3 script.py`.

---

## 5. Blockchain (optional — needed for on-chain anchoring to work live)

```powershell
cd wealthsense\blockchain
npm install
cp .env.example .env
```

Edit `.env`:
- `AMOY_RPC_URL` — a free Polygon Amoy RPC URL (the default public one
  in `.env.example` works, or get a more reliable one from Alchemy/Infura)
- `AMOY_PRIVATE_KEY` — a **testnet-only wallet's** private key. Never a
  real-funds wallet. Get free test MATIC from
  https://faucet.polygon.technology (select "Amoy").

```powershell
npx hardhat compile
npx hardhat test
```
Expect `3 passing`.

Deploy to the live testnet:
```powershell
npx hardhat run scripts/deploy.js --network amoy
```
This prints a contract address and a Polygonscan link — **save the
address**, the backend needs it next.

---

## 6. Backend (optional — needed for on-chain anchoring to work live)

```powershell
cd wealthsense\backend
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
- `RPC_URL` — same as `blockchain/.env`'s `AMOY_RPC_URL`
- `PRIVATE_KEY` — same testnet wallet
- `CONTRACT_ADDRESS` — the address from the deploy step above

If the contract changed, re-copy the ABI:
```powershell
Copy-Item ..\blockchain\artifacts\contracts\TransactionLedger.sol\TransactionLedger.json abi\TransactionLedger.json -Force
```

Run it:
```powershell
.\venv\Scripts\uvicorn.exe main:app --port 8000
```

Test it:
```powershell
curl -X POST http://127.0.0.1:8000/anchor -H "Content-Type: application/json" -d "{\"amount\": 15000, \"category\": \"Food & Drinks\", \"timestamp\": 1735689600}"
```
Should return a `tx_hash` and `explorer_url` you can open on Polygonscan.

### Point the Android app at your running backend

Edit `android-app/app/src/main/java/com/wealthsense/app/di/NetworkModule.kt`,
find `BASE_URL`:
- Testing on an **emulator**, backend running on your **host machine**:
  `http://10.0.2.2:8000/` (already the default — `localhost` from an
  emulator's perspective is the emulator itself, not your PC)
- Testing on a **physical device**: use your PC's LAN IP, e.g.
  `http://192.168.1.42:8000/`
- Deployed somewhere real: that server's actual URL

Then rebuild the app (Section 3).

---

## 7. Full end-to-end demo checklist

1. Backend running (Section 6), contract deployed (Section 5)
2. App built and installed (Section 3), `NetworkModule.BASE_URL` pointed
   at your running backend
3. Open the app → **`•••`** menu → **"Send Test SMS"** a few times
4. Watch for: transaction appearing in Recent Transactions, an anomaly
   notification (fires on the occasional large simulated spike), and
   eventually a green **"On-chain"** badge on a transaction once
   `ChainAnchorWorker` succeeds (may take a few seconds — it's real
   network + blockchain confirmation time)
5. Tap the on-chain badge → opens the real Polygonscan transaction page

---

## 8. If something breaks

Every step in this guide was worked through for real at least once
during development, including several genuine bugs that were found and
fixed this way (a missing `gradle-wrapper.jar`, a Room migration typo, an
ONNX API mismatch). If you hit a build error, **the exact error text is
the useful part** — Gradle/Python/Hardhat error output almost always
names the actual file and line.
