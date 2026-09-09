@echo off
REM OQ-18's corpus run, as a resumable batch job (docs/OPEN_QUESTIONS.md#oq-18).
REM
REM ~2.5-3 hours of CPU on all cores. It is a measurement, not a service: it reads the
REM corpus declared in config/collections.yaml, embeds it once with bge-m3, and scores
REM eight retrieval arms against tests/fixtures/retrieval/cases.yaml.
REM
REM Everything it needs is on disk. It does NOT need Ollama: the router model's
REM translations were measured separately and are read from
REM logs/measurements/oq18-translations.json.
REM
REM     schtasks /Query /TN "ORACLE-OQ18-eval"
REM     schtasks /Delete /TN "ORACLE-OQ18-eval" /F
REM
REM ---------------------------------------------------------------------------
REM WHY THIS IS A RETRY LOOP AND NOT A ONE-SHOT  (diagnosed 2026-09-09)
REM
REM Three runs have now died mid-pass, and the first two were "hardened" against the
REM wrong mechanism. The Windows event log settles it:
REM
REM   04:00:47  Power-Troubleshooter 1   returned from a low power state  (WakeToRun worked)
REM   04:05:35  Kernel-Power 187         "User-mode process attempted to change the system
REM                                       state by calling SetSuspendState or SetSystemPowerState"
REM   04:05:36  Kernel-Power 42          "The system is entering sleep. Sleep Reason: Application API"
REM
REM It is not the idle timer: `powercfg` has STANDBYIDLE on AC set to 0 (Never), and
REM 21 of the last 21 sleeps on this machine are "Application API". Something explicitly
REM suspends this box, late at night, and no setting inside our process prevents that --
REM SetThreadExecutionState cannot veto another process's SetSuspendState call. The
REM keep_system_awake() guard added on 2026-08-28 was aimed at an idle timer that was
REM already disabled, which is why it changed nothing.
REM
REM So: stop trying to keep the machine awake, and survive it being slept. The pass
REM checkpoints every 256 chunks and resumes, so a sleep costs only the time asleep. The
REM scheduled task repeats; each firing resumes; the first thing this script does is
REM check whether the run already finished and exit if so.
REM ---------------------------------------------------------------------------

cd /d "C:\Projects\ORACLE"

REM The completion marker is the result file itself -- written only after a full pass.
if exist "logs\measurements\oq18-translated.json" (
  echo === already complete, nothing to do: logs\measurements\oq18-translated.json exists >> logs\measurements\oq18-translated.txt
  exit /b 0
)

REM Append, never truncate. Truncating meant every retry destroyed the evidence of why
REM the previous attempt stopped, which is the only thing that made this diagnosable.
echo. >> logs\measurements\oq18-translated.txt
echo === OQ-18 corpus run, attempt started %DATE% %TIME% === >> logs\measurements\oq18-translated.txt

".venv\Scripts\python.exe" -u scripts\eval_embeddings.py ^
  --models bge-m3 ^
  --translations logs/measurements/oq18-translations.json ^
  --save-vectors D:/ORACLE/scratch/oq18-vectors-bge-m3.npz ^
  --load-vectors D:/ORACLE/scratch/oq18-vectors-bge-m3.npz ^
  --out logs/measurements/oq18-translated.json ^
  >> logs\measurements\oq18-translated.txt 2>&1

echo === attempt finished %DATE% %TIME% with exit code %ERRORLEVEL% === >> logs\measurements\oq18-translated.txt
