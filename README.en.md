# Zhaozuo (照做)

> Demonstrate it once. Zhaozuo repeats it—and remains accountable for the result.

[中文](README.md) · [ActionParity protocol](https://github.com/dongsheng123132/action-parity) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Zhaozuo is an open-source teach-by-demonstration tool for third-party Windows applications. It captures privacy-redacted user operations, extracts a reviewable **Compatibility Action Profile**, and replays it under explicit risk, confirmation, and success-evidence policies.

This repository is a **v0.1.0 technical preview** for research, demos, and sandbox testing. It is not ready for unattended production automation.

## Relationship to ActionParity

[ActionParity (影核), also known as the ShadowCore protocol](https://github.com/dongsheng123132/action-parity), is the open standard for applications whose GUI, CLI, MCP, API, automation, and tests share one headless Action Core.

Zhaozuo is the compatibility companion for applications that cannot yet be changed:

```text
Native path: Agent ──► Action Core ──► business result
Legacy path: Agent ──► Zhaozuo ──► Compatibility Action Profile ──► third-party GUI
```

Automating Chrome, WPS, WeChat, or another application through Zhaozuo does **not** make that application ActionParity-conformant. When a reliable native CLI, API, or Action Core becomes available, the profile should prefer it over GUI driving.

## Current capabilities

- Captures global Windows clicks, shortcuts, and input focus;
- records Windows UI Automation identities around the target control;
- redacts typed text into placeholders by default;
- supports segmented demonstrations and restores the most recent action;
- generates draft profiles with stable Action IDs, input contracts, risks, and evidence;
- replays through UIA, window-relative coordinates, then absolute coordinates as a last resort;
- pauses before external effects such as sending, publishing, and social reactions;
- defaults to dry-run and allows Esc to stop real execution.

Full UIA trees, multimodal step understanding, OCR, a profile registry, cross-version regression, and code signing are not implemented yet. The current extractor uses local rules rather than a general multimodal model.

## Windows downloads

GitHub [Releases](https://github.com/dongsheng123132/zhaozuo/releases) provides two Windows x64 artifacts:

- `Zhaozuo-Setup-*.exe`: a per-user installer that stores data in `%LOCALAPPDATA%\Zhaozuo`;
- `Zhaozuo-portable-*.zip`: an extracted portable build that stores data beside the executable under `data\`.

Technical-preview binaries are currently unsigned, so Windows SmartScreen may identify the publisher as unknown. Download only from this repository and verify the files against `SHA256SUMS.txt` from the same release.

## Quick start

Requirements: Windows 10/11 and Python 3.11 or newer.

```powershell
git clone https://github.com/dongsheng123132/zhaozuo.git
cd zhaozuo
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\zhaozuo.exe
```

Validate or inspect a profile without executing it:

```powershell
python -m executor.cli validate profiles/chrome/open-url.action-profile.json --json

python -m executor.cli plan profiles/chrome/open-url.action-profile.json `
  browser.open_url --input url=https://example.com --json
```

## Safety

- Recordings and screenshots stay ignored by Git by default.
- Typed text is redacted unless a future capture mode explicitly says otherwise.
- Real replay requires explicit authorization.
- External effects must be declared and confirmed immediately before they occur.
- An action needs observable evidence; a successful click is not a successful task.
- Use Zhaozuo only on computers, accounts, and applications you own or are authorized to operate.

See [SECURITY.md](SECURITY.md) before reporting a vulnerability and [CONTRIBUTING.md](CONTRIBUTING.md) before sharing profiles or recordings.

## License

Apache License 2.0. See [LICENSE](LICENSE).

Third-party product names and trademarks belong to their respective owners. Their appearance in examples does not imply affiliation or endorsement.
