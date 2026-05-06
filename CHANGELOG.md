# CHANGELOG


## v1.0.1 (2026-05-06)

### Bug Fixes

- Ensure build is not set to false
  ([`e5ec5d9`](https://github.com/kambiz-aghaiepour/dracs/commit/e5ec5d92cb832e5d73b874ecdfe2493fa1694530))

Looks like the setting in pyproject.toml had a build command set to "false". Change to empty string.

### Chores

- Adjust pyproject for uv and GHA
  ([`86e29c8`](https://github.com/kambiz-aghaiepour/dracs/commit/86e29c86c7567520e325c92c7ff64917cefac8ed))

- Gha adjustments
  ([`17e331f`](https://github.com/kambiz-aghaiepour/dracs/commit/17e331fa99f1ec303ee3cad30ca337f449936a19))

### Refactoring

- Replace raw sqlite3 with SQLAlchemy ORM and support multi-backend database URLs
  ([`4d8b709`](https://github.com/kambiz-aghaiepour/dracs/commit/4d8b70913a995a50333e5c47f8961cf1229ae9ae))

- Split monolithic __init__.py into dedicated modules for CLI, API, SNMP, DB, validation, and
  exceptions
  ([`b78dfd7`](https://github.com/kambiz-aghaiepour/dracs/commit/b78dfd71b646261a01e1c1eaab333386294e9542))


## v1.0.0 (2026-04-23)

### Bug Fixes

- Semantic versioning with uv
  ([`2e5b565`](https://github.com/kambiz-aghaiepour/dracs/commit/2e5b56554aedf3f97333649275a43080d0674033))

### Chores

- Add actual semantic-release.yaml
  ([`a6d60a6`](https://github.com/kambiz-aghaiepour/dracs/commit/a6d60a698b0b7f9fdc7c0b88c2d5b2f1e3bc671d))

- Fix semantic-versioning
  ([`da91dc1`](https://github.com/kambiz-aghaiepour/dracs/commit/da91dc10a9987617ea1abc0e62f6d6aad0949b52))

- Further uv tool fixes
  ([`af371d5`](https://github.com/kambiz-aghaiepour/dracs/commit/af371d5af90f0a5ca29d7c902d8a62da6159f07a))

- Silly unicode
  ([`3afbec8`](https://github.com/kambiz-aghaiepour/dracs/commit/3afbec8a078856ce2e490e2d4d1bb2560df26cec))

- **release**: 1.0.0
  ([`17f410d`](https://github.com/kambiz-aghaiepour/dracs/commit/17f410d11630f9787379036674c48ea4e53ac0c7))

[skip ci]

### Features

- Add batch host discovery from file with concurrent execution and tests
  ([`93a1bb8`](https://github.com/kambiz-aghaiepour/dracs/commit/93a1bb8f24237c04d59c9b6b039ba447e4600886))

closes: https://github.com/kambiz-aghaiepour/dracs/pull/12

- Add GHA CI/CD workflows.
  ([`b08bbe6`](https://github.com/kambiz-aghaiepour/dracs/commit/b08bbe6e069787d131b7d1c9ce4779eb074c2edc))

fixes: https://github.com/kambiz-aghaiepour/dracs/issues/7

- Add semantic versioning so sync-back works.
  ([`b4fdacf`](https://github.com/kambiz-aghaiepour/dracs/commit/b4fdacf5426e02ff9df9c9373eac7d5fcdb0e0c9))
