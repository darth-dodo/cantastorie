# cantastorie

[![CI](https://github.com/darth-dodo/cantastorie/actions/workflows/ci.yml/badge.svg)](https://github.com/darth-dodo/cantastorie/actions/workflows/ci.yml)
[![Deploy](https://github.com/darth-dodo/cantastorie/actions/workflows/deploy.yml/badge.svg)](https://github.com/darth-dodo/cantastorie/actions/workflows/deploy.yml)

Bedtime stories your child steers, in the languages your family speaks. Told aloud, painted in watercolor, and approved by you before a single word reaches little ears.

See [docs/product.md](docs/product.md) for the full product specification.

## Repository Layout

```
cantastorie/
├── src/          # Python authoring pipeline (story generation, audio, images, glosses)
├── static/       # The player: a static web app (vanilla JS + Tailwind)
│   ├── css/      # input.css → compiled output.css (gitignored)
│   └── js/       # ES modules, tested with Vitest
├── tests/        # pytest suites; tests/js/ holds Vitest suites
└── docs/         # Product and technical documentation
```

The player ships as static files with no server; the pipeline runs offline and publishes approved content as static assets.

## Development

Requires [uv](https://docs.astral.sh/uv/), Node.js 20+, and Python 3.12 (uv installs it automatically).

```bash
make install        # uv sync + npm install
make install-hooks  # pre-commit hooks (lint, format, types, secrets, commit style)
make dev            # serve the player at http://localhost:8000
make dev-css        # watch and compile Tailwind CSS (run alongside make dev)
```

### Everyday Targets

```bash
make test           # all tests (pytest + Vitest)
make check          # lint + format check + mypy (strict)
make help           # list every target
```

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), enforced by commitizen via pre-commit.

## CI/CD

Every PR runs lint, format check, strict mypy, pytest, Vitest, a Bandit security scan, and a Tailwind build ([ci.yml](.github/workflows/ci.yml)). Pushes to `main` deploy the player to [darth-dodo.github.io/cantastorie](https://darth-dodo.github.io/cantastorie/) via GitHub Pages ([deploy.yml](.github/workflows/deploy.yml)).

## License

[MIT](LICENSE)
