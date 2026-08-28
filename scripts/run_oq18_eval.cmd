@echo off
REM OQ-18's corpus run, as a batch job (docs/OPEN_QUESTIONS.md#oq-18).
REM
REM ~2.5-3 hours of CPU on all cores. It is a measurement, not a service: it reads the
REM corpus declared in config/collections.yaml, embeds it once with bge-m3, and scores
REM eight retrieval arms against tests/fixtures/retrieval/cases.yaml.
REM
REM Run by hand, or by the scheduled task registered 2026-08-26:
REM     schtasks /Query /TN "ORACLE-OQ18-eval"
REM     schtasks /Delete /TN "ORACLE-OQ18-eval" /F
REM
REM Everything it needs is on disk. It does NOT need Ollama: the router model's
REM translations were measured separately and are read from
REM logs/measurements/oq18-translations.json.

REM Hardened 2026-08-28 after two runs died mid-pass with nothing to show:
REM   -u + line buffering   the log updates live; an unchanging file now IS a stuck job
REM   --load-vectors        the pass checkpoints every 256 chunks and a re-run resumes
REM   keep_system_awake     the first run lost 12 h to the machine sleeping (01:44-13:37)

cd /d "C:\Projects\ORACLE"

echo === OQ-18 corpus run, started %DATE% %TIME% === > logs\measurements\oq18-translated.txt

".venv\Scripts\python.exe" -u scripts\eval_embeddings.py ^
  --models bge-m3 ^
  --translations logs/measurements/oq18-translations.json ^
  --save-vectors D:/ORACLE/scratch/oq18-vectors-bge-m3.npz ^
  --load-vectors D:/ORACLE/scratch/oq18-vectors-bge-m3.npz ^
  --out logs/measurements/oq18-translated.json ^
  >> logs\measurements\oq18-translated.txt 2>&1

echo === finished %DATE% %TIME% with exit code %ERRORLEVEL% === >> logs\measurements\oq18-translated.txt
