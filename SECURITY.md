# Security policy

## Reporting a vulnerability

**Please do not open a public issue.**

Use GitHub's private vulnerability reporting on this repository: the
**Security** tab, then *Report a vulnerability*. It goes only to the maintainer
and does not require exchanging email addresses.

If that is unavailable to you, get in touch through
**[radioterapia.ai](https://radioterapia.ai)** and say only that you have a
security report; the details can then move to a private channel.

You will get a first reply within **7 days**. If a fix is warranted it ships in
a release, and the release notes credit you unless you prefer otherwise.

**Never attach a real document, a real transcription or a log line that
carries a patient's name.** Describe the case in words; it will be rebuilt as a
synthetic document before anything is fixed.

## What is in scope

Scan-to-Text runs on a reception workstation and reads **patients' medical
documents**. The findings that matter most are the ones that expose them or put
one patient's document in another patient's folder:

- Anything that sends data off the machine. The app has no network feature: a
  test fails the build when a network call appears in the app code, and a
  runtime guard refuses any connection that is not to the machine itself. The
  only listener is the local screen API on `127.0.0.1`, with a random port and
  a random token.
- Anything that lets another local program or a web page drive that API: the
  `Host`, `Origin` and token checks in `app/scan_to_text/tela/api.py`.
- Anything that writes, moves or renames a file inside a **source** folder.
  Every write goes through `app/scan_to_text/es.py`, which refuses paths inside
  a source.
- Anything that makes a patient's name or document text reach the **log**. The
  log is the channel used to report problems, so it must stay clean.
- Anything that places a document in the wrong patient's folder, or lets two
  workstations publish the same file at once (`app/scan_to_text/travas.py`).

## What is already known, and is not a finding

- **The package is not code-signed.** Verify the `SHA256SUMS` published with
  each release before installing.
- **The app runs with the permissions of the logged-in user**, and the
  destination folders are protected by the clinic's own file permissions.
  Setting those permissions is the clinic's responsibility.
- **OCR makes mistakes.** A transcription that misreads a word or a number is a
  correctness bug, not a vulnerability. That is why every Markdown ends with
  the warning about isolated numbers and why the original sits next to it.
- **The app is not a medical device and is not validated for clinical use.**

## Supported versions

The latest release. This is a small project with a single maintainer; there are
no backports.
