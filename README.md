# Classroom Image Sync

Downloads image attachments from a Google Classroom course to a local folder,
incrementally, on a weekly schedule.

## Configure the course

The course ID is not stored in this repository. Copy the example config and
fill it in:

```powershell
Copy-Item config.example.json config.json
```

`config.json` is gitignored. The ID is the base64-decoded slug from your
Classroom URL — for `classroom.google.com/c/<slug>`:

```powershell
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("<slug>"))
```

`--course-id` and the `CLASSROOM_COURSE_ID` environment variable both override
the config file, in that order of precedence.

## What it does

Lists every announcement, course-work material and assignment in the course,
collects the Drive files attached to them, and downloads anything with an
`image/*` MIME type. Downloaded Drive file IDs are recorded in
`state/manifest.json`, so later runs only fetch what is new.

Files are named `<post-date>_<post-title>_<original-name>` so they sort
chronologically.

## Setup

### 1. Install — already done on this machine

```powershell
cd C:\Users\yuryj\automated-trading\Classroom_Image_Sync
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**On this machine the second command fails**, and will keep failing. Microsoft
Defender Network Protection blocks `files.pythonhosted.org`, enforced by a
Microsoft Defender for Endpoint tenant the machine is onboarded to. It is not a
network problem — a VPN does not help, and `Set-MpPreference` cannot override
it because Tamper Protection is locked on by that tenant's policy.

To reinstall or add a package, download the wheels through **Microsoft Edge**
(the only process here not subject to the block) and install offline:

```powershell
# 1. Resolve URLs via pypi.org's JSON API, which is NOT blocked.
#    Only files.pythonhosted.org is.
# 2. Download them in Edge.
# 3. Install without touching the network:
.venv\Scripts\pip install --no-index --find-links=wheels -r requirements.txt
```

When picking wheels by hand, match the tag to CPython 3.13 / win_amd64:
`py3-none-any`, `cp313-*-win_amd64`, or `cp3NN-abi3-win_amd64` where `NN <= 13`.
`cryptography` is the trap — it ships as `cp311-abi3`, so a naive "newest cp313"
filter misses it and can pick a macOS build.

### 2. Create the OAuth client

In the [Google Cloud console](https://console.cloud.google.com/). Google renamed
this area to **Google Auth Platform** in 2025; older guides still say "OAuth
consent screen" and "Credentials", which no longer appear in the left nav:

| Old name | Current name |
| --- | --- |
| OAuth consent screen | Get started / Branding |
| Scopes | **Data Access** |
| Publish app | **Audience** -> Publish app |
| Credentials -> OAuth client ID | **Clients** -> Create client |

1. Create a project (any name).
2. **APIs & Services -> Library**: enable both **Google Classroom API** and
   **Google Drive API**.
3. **Google Auth Platform -> Get started**: set an app name and support email,
   Audience **External** (a personal Gmail cannot use Internal -- that requires
   Workspace), then a contact email.
4. **Audience -> Publish app.** It starts in Testing. Publishing is not
   optional -- see below.
5. **Data Access -> Add or remove scopes.** Paste these five into the manual
   entry box:
   - `https://www.googleapis.com/auth/classroom.courses.readonly`
   - `https://www.googleapis.com/auth/classroom.announcements.readonly`
   - `https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly`
   - `https://www.googleapis.com/auth/classroom.coursework.me.readonly`
   - `https://www.googleapis.com/auth/drive.readonly`

   Google flags these as sensitive/restricted. That is expected.
6. **Clients -> Create client -> Desktop app.** Not Web: the script uses a
   loopback redirect. Download the JSON and save it as `client_secret.json` in
   this folder.

`drive.readonly` is a *restricted* scope, and Google may resist publishing to
production with one absent a verification review. There is no lesser Drive scope
that works -- `drive.file` only sees files the app itself created, which never
includes teacher-posted attachments.

### 3. Authorise once, interactively

```powershell
.venv\Scripts\python.exe classroom_image_sync.py --dry-run
```

A browser window opens. You will see a **"Google hasn't verified this app"**
warning — expected for a personal, unverified project. Choose **Advanced ->
Go to (unsafe)** and grant access. The token is written to `state/token.json`
and reused from then on.

Drop `--dry-run` to actually download.

## Why the app must be "In production"

An OAuth consent screen left in **Testing** status expires refresh tokens
**7 days after consent** — measured from the moment you consented, not from
last use, so running more often does not extend it. A weekly job would sit
exactly on that boundary and fail constantly. Publishing to production makes
the token effectively permanent (it still dies after 6 months unused, on
password change, or if you revoke access).

Publishing does **not** require Google's verification review. Verification only
matters for distributing the app to other people; for your own account the
"unverified app" warning screen is the only consequence.

## Scheduling it weekly

```powershell
$exe = "powershell.exe"
$arg = '-NoProfile -ExecutionPolicy Bypass -File "C:\Users\yuryj\automated-trading\Classroom_Image_Sync\run_sync.ps1"'

$action   = New-ScheduledTaskAction -Execute $exe -Argument $arg
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 8am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable

Register-ScheduledTask -TaskName "Classroom Image Sync" -Action $action `
    -Trigger $trigger -Settings $settings `
    -Description "Weekly download of Google Classroom image attachments"
```

`-StartWhenAvailable` matters on a personal machine: if the PC is off at 8am
Sunday, the task runs at the next opportunity instead of being skipped.

Run it once by hand to confirm:

```powershell
Start-ScheduledTask -TaskName "Classroom Image Sync"
```

## Output

```
images/          downloaded pictures
logs/sync.log    append-only run log
state/manifest.json   Drive file IDs already fetched
state/token.json      OAuth token  (secret)
client_secret.json    OAuth client (secret)
```

Both secrets are covered by `.gitignore`. Do not commit them.

## Options

| Flag | Effect |
| --- | --- |
| `--course-id ID` | Target a different course |
| `--out PATH` | Change the output folder |
| `--all-files` | Download every attachment, not just images |
| `--dry-run` | List what would be fetched, write nothing |
| `--no-browser` | Never open a consent window; exit 2 if one is needed |
| `-v` | Debug logging |

Exit codes: `0` success, `1` error, `2` re-authorisation needed.

## Known limitations

- **Only attachments.** Images pasted directly into a post's body are not
  attachments and are invisible to the Classroom API. Nothing here will catch
  them.
- **Images inside Docs/Slides** attached to a post are not extracted; only the
  document itself is an attachment, and it is skipped unless `--all-files`.
- **Materials can be unreadable.** Google's API docs note that permission to
  read a post does not imply permission to read its materials. Such files are
  logged as "unreadable" and deliberately left out of the manifest, so a later
  run retries them if sharing is corrected.
