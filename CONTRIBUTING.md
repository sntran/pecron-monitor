# Contributing to Pecron Monitor

Thanks for your interest in contributing! This project is small and straightforward — here's how to help.

## Reporting Bugs

Please include:

1. **Version** — `python pecron_monitor.py --version`
2. **Pecron model** (E300LFP, E1500LFP, F3000LFP, etc.)
3. **Connection method** — BLE, WiFi TCP, Cloud, or all
4. **OS** — macOS, Linux, Raspberry Pi, etc.
5. **Logs** — run with `-v` for verbose output:
   ```bash
   python pecron_monitor.py --status -v 2>&1 | tee debug.log
   ```
6. **Raw data** (if data looks wrong):
   ```bash
   python pecron_monitor.py --raw -v
   ```

## Requesting Features

Open an issue describing what you want and why. If it's a new Pecron model that isn't working, include `--diagnose -v` output.

## Submitting Changes

1. Fork the repo
2. Create a branch (`git checkout -b fix/my-fix`)
3. Make your changes
4. Test with your device if possible
5. Update `CHANGELOG.md` under an `[Unreleased]` section
6. Open a PR against `main`

### Code Style

- Python 3.9+ compatible
- No additional dependencies without good reason (keep `requirements.txt` small)
- Log messages should be clear and actionable
- Control commands (0x0013) must never be sent unless explicitly requested by the user

### Testing

There's no test suite yet (hardware-dependent project). At minimum:
- Verify `python pecron_monitor.py --version` works
- Verify `python pecron_monitor.py --help` shows your new flags
- If you have a device, test `--status` and `--raw`

## Security

- Never commit credentials, auth keys, or device keys
- `config.yaml` is gitignored for this reason
- If you find a security issue, email the maintainer instead of opening a public issue
