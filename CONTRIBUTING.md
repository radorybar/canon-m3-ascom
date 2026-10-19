# Contributing

Thanks for your interest in canon-m3-ascom. Contributions are welcome through **issues** and **pull requests**.
Every change to `main` goes through a pull request that the maintainer reviews and approves.

## Issues

- **Bug reports:** use the bug template and include the output of
  `python -m canon_m3_ascom.ptpdiag` (and `--shoot 1` if capture fails), camera firmware, CHDK build,
  Windows and N.I.N.A. versions.
- **Other cameras:** reports from other CHDK cameras are very welcome; include the `ptpdiag` output.
- **Feature requests:** describe the use case first.
- **Security issues:** do not open a public issue, see [SECURITY.md](SECURITY.md).

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Keep the change focused; open an issue first for anything larger.
3. Run the tests: `.venv\Scripts\python.exe -m pytest` (no camera needed; the USB-level emulator
   covers the `ptp` backend).
4. If you tested with a real camera, say which one and what you ran.
5. Open the pull request against `main` and fill in the template.

Pull requests are merged only after the maintainer's approval. GitHub Actions workflows from forks run
only after the maintainer approves them.

## Code style

- Python 3.12, standard library style, type hints where they help.
- Match the surrounding code; comments explain *why*, not *what*.
- Docs and commit messages in English.

## License

By contributing you agree that your contributions are licensed under the project's license,
GPL-2.0-or-later (see [LICENSE](LICENSE)).
