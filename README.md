# Classroom Image Sync

Downloads image attachments from a Google Classroom course to a local folder.
Runs weekly via Windows Task Scheduler and only fetches what is new.

Google Classroom has no bulk image download. This fills that gap.

---

## Coming back to this after a long time? Read this first

The tool **already runs on its own**. You probably do not need to do anything.

1. **`LOCAL_NOTES.md`** (this machine only, gitignored) has the actual course,
   Google Cloud project, file paths and current status. Start there.
2. To check it is still working, see [Is it working?](#is-it-working).
3. To fix something, see [Troubleshooting](#troubleshooting).
4. Only read [Rebuilding from scratch](#rebuilding-from-scratch) if you are
   setting this up on a new machine or a new Google account.

**The single most important thing to know:** this repository being public, with
GitHub Pages serving `docs/`, is what keeps the Google OAuth app authorised. It
is not just source hosting. See [What depends on what](#what-depends-on-what)
before deleting anything.

---

## Where everything lives

Everything sits in the project folder alongside this file.

| Path | What it is | In git? |
| --- | --- | --- |
| `classroom_image_sync.py` | The whole program. One file. | yes |
| `run_sync.ps1` | Wrapper the scheduled task calls | yes |
| `docs/` | Home page + privacy policy served by GitHub Pages | yes — **load-bearing** |
| `config.example.json` | Template for the course config | yes |
| **`config.json`** | **Your course ID and output folder** | **no — identifies a real class** |
| **`client_secret.json`** | **OAuth client credentials from Google** | **no — SECRET** |
| **`state/token.json`** | **Your OAuth refresh token** | **no — SECRET** |
| `state/manifest.json` | Drive file IDs already downloaded | no |
| `images/` | Default output folder, used only when `out_dir` is unset | no |
| `logs/sync.log` | Append-only run log | no |
| `.venv/` | Python virtual environment | no |

### Where the images go

By default they land in `images/` beside the script. Point `out_dir` in
`config.json` at somewhere else — a photo library, an external drive — and they
go there instead:

```json
{
  "course_id": "123456789012",
  "out_dir": "E:/Photos/Classroom"
}
```

Relative paths are relative to the project folder; absolute paths are used as
given. Backslashes must be escaped in JSON (`"E:\\Photos"`) or just use forward
slashes. `--out PATH` overrides it for one run, and `CLASSROOM_OUT_DIR`
overrides `config.json` too. Precedence is `--out` > env > `config.json` >
`images/`.

**Files are filed into `<out_dir>/YYYY/MM` by post date**, so a picture from an
announcement posted 2026-08-27 lands in `<out_dir>/2026/08/`. A post with no
usable date goes to `<out_dir>/undated/`. The year/month split keeps the folder
browsable as the course fills up, and matches the layout most photo libraries
already use — so pointing `out_dir` straight at one merges cleanly instead of
creating a parallel tree.

Moving an existing collection means moving the files *and* rewriting the `path`
values in `state/manifest.json` to match. The manifest is what stops repeat
downloads; if the recorded path no longer exists, that file is fetched again.

### The three sensitive files

| File | What it is | If lost |
| --- | --- | --- |
| `client_secret.json` | Identifies the app to Google. Downloaded from the Cloud console. | Re-download from **Google Auth Platform → Clients**. Not regenerable by hand. |
| `state/token.json` | Proof *you* authorised the app. Contains a refresh token. | Delete it and run the tool manually once; it re-authorises through the browser. |
| `config.json` | The numeric course ID. | Copy `config.example.json` and decode the slug from the Classroom URL (below). |

None are in git, and `.gitignore` enforces that. **Never commit them** — this
repo is public.

Anyone holding `state/token.json` can read your Classroom and Drive content.
If it leaks, revoke access at
[Google Account permissions](https://myaccount.google.com/permissions).

---

## Is it working?

```powershell
cd C:\Users\yuryj\automated-trading\Classroom_Image_Sync

# When did it last run, and did it succeed? (0 = success)
Get-ScheduledTaskInfo -TaskName "Classroom Image Sync" |
    Select-Object LastRunTime, LastTaskResult, NextRunTime

# What did it actually do?
Get-Content logs\sync.log -Tail 15
```

A healthy run ends with a line like `Done. 0 new, 77 already had, 0 unreadable.`
`0 new` is normal — it means nothing was posted since last time.

Run it right now without waiting for the weekly run:

```powershell
Start-ScheduledTask -TaskName "Classroom Image Sync"
```

---

## Running it by hand

```powershell
cd C:\Users\yuryj\automated-trading\Classroom_Image_Sync

.venv\Scripts\python.exe classroom_image_sync.py --dry-run   # preview only
.venv\Scripts\python.exe classroom_image_sync.py             # actually download
```

| Flag | Effect |
| --- | --- |
| `--dry-run` | List what would be fetched, write nothing |
| `--course-id ID` | Target a different course (overrides `config.json`) |
| `--out PATH` | Output folder for this run; overrides `config.json` |
| `--all-files` | Download every attachment, not just images |
| `--no-browser` | Never open a consent window; exit `2` if one is needed |
| `-v` | Debug logging |

Exit codes: `0` success · `1` error · `2` re-authorisation needed.

The scheduled task always passes `--no-browser`, so it can never hang waiting
for a consent window that nobody is there to click.

Output files are named `<local-post-date>_<post-title>_<original-name>` so they
sort chronologically.

---

## What depends on what

Read this before deleting, renaming, or privatising anything.

```
   GitHub repo (PUBLIC)  ──serves──>  https://antipyrrhus.github.io/Classroom_Image_Sync/
   antipyrrhus/                              index.html + privacy.html
   Classroom_Image_Sync                              │
                                                     │ Google requires a live homepage
                                                     │ and privacy policy URL to keep
                                                     ▼ the OAuth app "In production"
                                        Google Auth Platform app
                                                     │
                                                     │ only a published app issues
                                                     ▼ long-lived refresh tokens
                                             state/token.json
                                                     │
                                                     ▼
                                        the weekly job runs unattended
```

**Consequences if you break the top of that chain:** delete the repo, make it
private, disable Pages, or remove `docs/`, and Google can treat the consent
screen as incomplete and drop the app back to *Testing*. In Testing, refresh
tokens expire **7 days after consent**. The weekly job would then fail every
week, quietly, until someone re-authorises by hand.

### Why "In production" matters so much

Testing status expires refresh tokens 7 days from the moment you consent — not
from last use, so running more often does not help. A weekly job sits exactly on
that boundary. Publishing makes the token effectively permanent; it then only
dies after 6 months unused, on password change, or if you revoke access.

Publishing does **not** require Google's verification review. Verification only
matters for distributing an app to other people. For your own account the
"unverified app" warning screen is the entire consequence.

There is no way around this. A service account would need Workspace
domain-wide delegation, and this is a personal Gmail account.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Exit code `2`, log says re-authorisation needed | Token expired or revoked | Run by hand (no `--no-browser`) and approve in the browser |
| Browser consent appears **every** run | App fell back to *Testing* status | Check **Audience** in the Cloud console. Verify the GitHub Pages URLs still load. |
| `Cannot read course ... HTTP 404` | Wrong course ID, or you were removed from the class | Re-derive the ID (below); confirm you can still open the class in a browser |
| Some files logged as `unreadable` | Google: permission to read a post does not imply permission to read its materials | Usually a sharing setting on that attachment. They are deliberately left out of the manifest, so a later run retries them automatically. |
| `No course ID configured` | `config.json` missing | `Copy-Item config.example.json config.json` and fill it in |
| `pip install` fails with an SSL error | Network or certificate problem reaching PyPI | Retry; check no proxy/filtering is in the way |
| Task shows `LastTaskResult` other than 0 | Check `logs\sync.log` for the real error | — |

### Finding the course ID again

The Classroom URL is `classroom.google.com/c/<slug>`. The slug is base64:

```powershell
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("<slug>"))
```

That numeric result goes in `config.json` as `course_id`.

### Re-authorising from scratch

```powershell
Remove-Item state\token.json
.venv\Scripts\python.exe classroom_image_sync.py --dry-run
```

Approve in the browser. You will see **"Google hasn't verified this app"** —
that is expected and correct for a personal app. Choose **Advanced → Go to
Classroom Image Sync (unsafe)**.

---

## Installing packages on this machine

Plain `pip install -r requirements.txt` works. Nothing special is needed.

> **History.** Until the 2026-08-22 clean Windows reinstall this machine could
> not reach `files.pythonhosted.org` at all: Microsoft Defender Network
> Protection blocked it, enforced by a Defender for Endpoint tenant the machine
> was onboarded to, with Tamper Protection locked on so it could not be
> overridden. The workaround was to download wheels in Microsoft Edge (the one
> unblocked process) and `pip install --no-index --find-links=wheels`. The
> reinstall removed that tenant onboarding and the block with it, so the
> `wheels/` folder and its helper files have been deleted. If package installs
> ever start failing with SSL/connection errors again, check
> `Get-MpPreference | Select-Object EnableNetworkProtection` (1 = blocking) —
> that is what it looked like last time.

---

## Known limitations

- **Only attachments.** Images pasted directly into a post's body are not
  attachments and are invisible to the Classroom API. Nothing here can retrieve
  them.
- **Images inside Docs/Slides are not extracted.** Only the document itself is
  an attachment, and it is skipped unless you pass `--all-files`.
- **Announcements, materials and assignments only.** Private comments and
  student submissions are not read.

---

## Rebuilding from scratch

Only needed for a new machine or a new Google account.

### 1. Python environment

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 2. Google Cloud project

Google renamed this area to **Google Auth Platform** in 2025. Older guides say
"OAuth consent screen" and "Credentials", which no longer exist in the nav:

| Old name | Current name |
| --- | --- |
| OAuth consent screen | Get started / Branding |
| Scopes | **Data Access** |
| Publish app | **Audience → Publish app** |
| Credentials → OAuth client ID | **Clients → Create client** |

1. Create a project.
2. **APIs & Services → Library**: enable **Google Classroom API** *and*
   **Google Drive API**.
3. **Get started**: app name, support email, Audience **External** (personal
   Gmail cannot use Internal — that needs Workspace), contact email.
4. **Branding**: set a homepage URL and privacy policy URL. These are
   *mandatory* to publish. This repo's GitHub Pages provides them:
   - `https://antipyrrhus.github.io/Classroom_Image_Sync/`
   - `https://antipyrrhus.github.io/Classroom_Image_Sync/privacy.html`

   Add `antipyrrhus.github.io` under **Authorized domains** (bare domain, no
   `https://`, no path). **Do not upload an app logo** — that forces the app
   into a verification review.
5. **Audience → Publish app.**
6. **Data Access → Add or remove scopes**, pasting all five:
   ```
   https://www.googleapis.com/auth/classroom.courses.readonly
   https://www.googleapis.com/auth/classroom.announcements.readonly
   https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly
   https://www.googleapis.com/auth/classroom.coursework.me.readonly
   https://www.googleapis.com/auth/drive.readonly
   ```
   Google flags these sensitive/restricted. Expected.
7. **Clients → Create client → Desktop app** (not Web — the script uses a
   loopback redirect). Download the JSON, save as `client_secret.json` here.

`drive.readonly` is a *restricted* scope and cannot be narrowed: `drive.file`
only sees files the app itself created, which never includes teacher-posted
attachments.

### 3. Course config

```powershell
Copy-Item config.example.json config.json
```

Fill in `course_id` using the base64 decode above.

### 4. Authorise

```powershell
.venv\Scripts\python.exe classroom_image_sync.py --dry-run
```

### 5. Schedule it

```powershell
$root = "C:\Users\yuryj\automated-trading\Classroom_Image_Sync"
$arg  = '-NoProfile -ExecutionPolicy Bypass -File "' + $root + '\run_sync.ps1"'

$action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Thursday -At 10pm
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
              -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName "Classroom Image Sync" -Action $action `
    -Trigger $trigger -Settings $settings -Force `
    -Description "Weekly download of Google Classroom image attachments"
```

`-StartWhenAvailable` matters on a personal PC: if it is off at the scheduled time the
task runs at the next opportunity rather than being skipped entirely.

To change the day or time, re-run with a different `-DaysOfWeek` / `-At`.
Pick the day from when posts actually appear: a run scheduled just *before* the
weekly post collects the previous week's, leaving the new batch to sit for
almost seven days. A run with nothing new takes about three seconds, so if the
posting day is irregular, running daily costs little.
To remove it: `Unregister-ScheduledTask -TaskName "Classroom Image Sync"`.

---

## Implementation notes

Two Google behaviours the code works around. Both are load-bearing; do not
"simplify" them away.

**Scope aliasing.** Google grants
`classroom.student-submissions.me.readonly` when you request
`classroom.coursework.me.readonly` — the same permission under an older name.
Two consequences: `oauthlib` raises on *any* difference between requested and
granted scopes (hence `OAUTHLIB_RELAX_TOKEN_SCOPE`), and a naive
"are all requested scopes present?" check against the stored token fails
forever, forcing browser consent on every run and silently killing unattended
scheduling. `SCOPE_ALIASES` and `has_required_scopes()` handle this.

**UTC post dates.** `creationTime` is UTC, so slicing the string files an
evening post under the next day. `post_local_date()` converts first.

Also: Drive permits duplicate file names, so a single run can generate colliding
targets. The code tracks names claimed during the run, not just what exists on
disk — otherwise `--dry-run` (which writes nothing) would report a filename
layout the real run would not produce.
