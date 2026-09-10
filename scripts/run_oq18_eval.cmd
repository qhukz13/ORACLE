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
REM
REM AND A THIRD THING, WHICH WAS A MISDIAGNOSIS (2026-09-09 20:00-20:45):
REM
REM A run was declared "hung" -- 0% CPU on repeated samples, 71 threads in Wait, a py-spy
REM stack parked inside onnxruntime's session.run(). Every one of those observations was
REM real. The conclusion was wrong. It was not deadlocked; it was running at 0.20 chunks/s
REM against the 2.52 chunks/s measured on 2026-08-29, so each 16-chunk batch took ~81 s and
REM a stack sample landed inside run() essentially always.
REM
REM The cause was CPU starvation, self-inflicted: two full `scripts/check.py` runs (pytest +
REM security + vitest, all 24 threads) executed inside the same 21-minute window in which the
REM eval was trying to reach its first checkpoint. The 0% CPU readings came from
REM Win32_Processor LoadPercentage, which is a stale sampled counter and should not have been
REM trusted over the eval's own progress line.
REM
REM Two things follow, and they are the reason this note exists rather than being deleted:
REM
REM   * DO NOT RUN THE TEST SUITE WHILE THIS RUNS. It does not merely slow the pass, it
REM     corrupts the chunks/s and query-latency figures the eval exists to report.
REM   * A stack in native code proves where a thread IS, not that it is STUCK. The
REM     distinguishing evidence was the progress line, and it was 20 minutes away.
REM
REM `--threads` is therefore back at the default 24 -- the configuration the 2.52 chunks/s
REM baseline was measured with. It was briefly dropped to 20 to mitigate a deadlock that was
REM never happening.
REM
REM The 45-minute ExecutionTimeLimit stays, but on the sleep's merits rather than the hang's:
REM the original PT6H meant a firing killed by anything held the slot for six hours while
REM every retry was ignored.
REM
REM --- Why --corpus-cache, added 2026-09-10 --------------------------------------
REM The checkpoint was solving the wrong half of the problem. The 2026-09-10 attempt
REM reached 28% (5,376 of 19,191 vectors) and its .npz was ALREADY unusable, because
REM `corpus_fingerprint()` hashes every embedded chunk's text and this corpus contains
REM this repository. A docs commit an hour into the run took the semantic chunk count
REM from 19,212 to 19,191, so the saved vectors were keyed to a corpus that no longer
REM existed on disk. Measured, not inferred: stored f740705a..., today 10251e03....
REM
REM So a run was hostage to every commit for its whole six hours, which is not a way to
REM work. --corpus-cache freezes the walked-and-chunked corpus to an 8 MB .json.gz on
REM first use and reads it thereafter. Deleting that file is how you deliberately
REM re-walk; nothing else moves the corpus any more.
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
  --corpus-cache D:/ORACLE/scratch/oq18-corpus.json.gz ^
  --save-vectors D:/ORACLE/scratch/oq18-vectors-bge-m3.npz ^
  --load-vectors D:/ORACLE/scratch/oq18-vectors-bge-m3.npz ^
  --out logs/measurements/oq18-translated.json ^
  >> logs\measurements\oq18-translated.txt 2>&1

echo === attempt finished %DATE% %TIME% with exit code %ERRORLEVEL% === >> logs\measurements\oq18-translated.txt
