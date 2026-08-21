# ORACLE — Mobile Client

Control and observe ORACLE from my phone, on my LAN, without opening a hole in the machine.

## 1. What mobile is for

Mobile is **approve, observe, ask** — not a second workstation.

| Do on the phone | Don't |
|---|---|
| Approve/deny a pending T2 action | Approve T3 (blocked server-side) |
| See what ORACLE is doing right now | Compose a complex multi-step plan |
| Ask a question, read the answer | Drive an interactive terminal |
| Cancel a task; **HALT** | Edit files |
| Check pipeline/test results | Manage policy or devices |
| Read logs for a failed task | |

The reason to be strict: a 4-inch screen in a noisy place is a bad venue for irreversible decisions.
The phone's real value is that ORACLE stops being blocked on me being at my desk.

## 2. PWA, not a native app

A Progressive Web App served by `oracled` itself.

**Why:** one codebase and one design system with the desktop frontend; no app stores, no signing, no
release cycle; updates ship with the backend; and it works on any device I happen to be holding.

**Rejected:** native iOS/Android (two more toolchains and a release process for a single-user app);
Tauri Mobile (immature for this, and no benefit when the client is already a thin API consumer);
Telegram bot as the primary UI (no rich approval previews, and it routes my task data through a third
party — a direct violation of local-first).

Trade-off accepted: no reliable background push without solving the certificate problem (§5), so v1
notifications only arrive while the app is open.

## 3. Layout

Single column, bottom tab bar — the four things mobile is for:

```
┌─────────────────────────┐   ┌─────────────────────────┐
│ ORACLE      ● executing │   │ ⚠ APPROVAL       T2     │
│─────────────────────────│   │                         │
│ NOW                     │   │ git push origin fix/auth│
│ investigate Asterim auth│   │ in C:\Projects\Asterim  │
│ ▓▓▓▓▓▓░░ step 4/6  4m12s│   │                         │
│                         │   │ WHY  push the fix branch│
│ WAITING ON YOU       1  │   │ TASK #128               │
│ ⚠ git push → fix/auth   │   │ EFFECT 3 commits → new  │
│                         │   │   remote branch, public │
│ RECENT                  │   │                         │
│ ✓ tests 41 passed  3m   │   │ ⓘ turn is TAINTED       │
│ ✓ indexed notes    8m   │   │ expires 4:38            │
│─────────────────────────│   │                         │
│ 💬    ☑     ⚠1    ⚙    │   │  [ Deny ]    [ Approve ]│
│ Chat Tasks Appr. System │   └─────────────────────────┘
└─────────────────────────┘
```

Approval cards carry the **same information as the desktop** — real command, reason, effect, taint
provenance, expiry. Nothing is abbreviated away, because abbreviation is how people approve things
they didn't understand. If it doesn't fit, it scrolls.

`HALT` lives in the System tab and in a persistent header affordance. It must be reachable in one tap
from anywhere.

## 4. Pairing

```
Desktop: Settings → Devices → Pair          Phone: scan
┌──────────────────────┐
│  ██▀▄█▀██  ▄▀█▄▀█    │   QR payload:
│  ▀█▄██▄▀█  █▄▀██▄    │   { host: "192.168.1.42", port: 8787,
│  █▀▄ ▄██▀  ▀██▄█▀    │     spki: "sha256/9f2c…",
│                      │     code: "48210377", exp: 1755… }
│  code 4821 0377      │
│  expires in 1:47     │
└──────────────────────┘
```

1. Desktop generates a **single-use 8-digit code**, valid 120 s, rate-limited to 5 attempts.
2. Phone scans, connects over TLS, and **pins the SPKI fingerprint** from the QR — trust-on-first-use
   anchored out-of-band by the screen in front of me, not by the network.
3. Phone posts the code to `/devices/pair`; server returns a device token (32 random bytes; stored
   Argon2id-hashed) plus a capability profile.
4. Token is stored in the browser's origin-scoped storage; a changed server certificate causes a hard
   refusal, not a warning.

Manual entry (host + port + code) exists as a fallback when the camera fails.

Every pairing, failed attempt and revocation is audited. Devices are listed with last-seen and
revocable individually from the desktop.

## 5. Network and the certificate problem

Default posture is **loopback only**. LAN mode is an explicit, per-launch opt-in with a visible
indicator in the command bar — I should always know when the agent is listening beyond this machine.

- **TLS always** in LAN mode, with a stable self-signed certificate and pinned SPKI.
- **mDNS** (`_oracle._tcp.local`) advertises presence only — never tokens, never capabilities.
- **Never port-forward.** Exposing this API to the internet is a T4-class idea.

### The open problem — [OQ-06](OPEN_QUESTIONS.md)

Browsers require a *secure context* for service workers. A self-signed certificate is not trusted by
default, which likely blocks **PWA installation** and **Web Push**. Options, none free:

| Option | Cost |
|---|---|
| Install a local CA (mkcert-style) on the phone | Trusting a CA on my personal device; real but manageable risk if the key never leaves the PC |
| Real certificate via DNS-01 for a local hostname | Needs a domain and DNS automation |
| Skip push entirely | Notifications only while the app is open — **the v1 choice** |
| ntfy / self-hosted push bridge | Another service; keeps data local if self-hosted |

**v1 decision: no push.** WS notifications while the app is open, and that is stated plainly rather
than half-built. The pairing/TLS work is unaffected either way, so this question does not block
Phase 8 — it only bounds it.

### Remote access — Post-MVP

If ORACLE is ever needed from outside the LAN, the sanctioned path is **Tailscale/WireGuard**: the
device joins a private network, and ORACLE still sees a LAN peer. No inbound ports, no public
exposure, no change to the security model. Any other approach requires a new threat model and a new ADR.

## 6. Offline and reconnection

- Last-known state renders from cache with a visible "stale, reconnecting" marker — never silently
  stale, because a stale approval count is dangerous.
- Reconnect uses `since_seq` for an exact catch-up ([API.md §2](API.md#connect-and-resume)).
- Approvals that expired while offline show as **expired**, never as still-actionable.
- Mobile subscribes to a **narrow topic set** (`approval.*`, `task.*`, `agent.state`, `message.*`) to
  save battery; verbose streams (`term.output`, `log.entry`, `system.metrics`) are opt-in per view.

## 7. Acceptance criteria

Mirrors [ROADMAP Phase 8](ROADMAP.md#phase-8--mobile--post-mvp):

- Pairing completes in under 30 s from scanning the QR.
- Approving a T2 action works; **T3 is refused server-side** with a clear explanation.
- 60 s of Wi-Fi loss then recovery: no lost or duplicated events.
- An unpaired device on the same LAN gets nothing — verified from an actual second device.
- A changed server certificate causes a refusal to connect.
- Remote HALT works from a locked phone in under 5 s.
