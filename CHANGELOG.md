# CHANGELOG

## v1.4.2 (2026-05-07)

### Bug Fixes

- Add throttling to avoid breaking mass refresh
  ([`03c0a64`](https://github.com/kambiz-aghaiepour/dracs/commit/03c0a6412be687c6fc99db1a32c8fa601d5fa459))

When refreshing large sets of systems using --all or -m model, we iterate over our systems and individually make calls to the Dell API. This can cause errors from the Dell side when too many connections are detected within a short period of time. We now throttle connections by adding a 30 second sleep after each set of 100 systems are processed.


## v1.4.1 (2026-05-07)

### Bug Fixes

- Info printed during updates made more accurate
  ([`f91e4df`](https://github.com/kambiz-aghaiepour/dracs/commit/f91e4dfb9ff493abc1f652eee00af7ae47f7cdae))

When we poll systems with "refresh" we now will see info messages if any associated fields have changes: model, firmware version, bios version, warranty expiration.


## v1.4.0 (2026-05-07)

### Features

- Add verbose flag and change tracking to refresh command
  ([`59347d4`](https://github.com/kambiz-aghaiepour/dracs/commit/59347d4f53faf065bd373fa63114df2710197f3a))

When refreshing a whole model of systems (or all), when a change is detected in bios, firmware, model, or warranty, the information is printed to the terminal. Also, mass updates are not printed to the screen by default unless -v or --verbose is given.


## v1.3.0 (2026-05-07)

### Features

- Add multiple enhancement features
  ([`2e055a0`](https://github.com/kambiz-aghaiepour/dracs/commit/2e055a021e50096b7056c25f7ba15b33f3fde0a1))

For the admin user, add a "Select All" button to allow for full selection for operations. Add the ability to right-click copy one or more values from any of the columns displayed.


## v1.2.1 (2026-05-06)

### Bug Fixes

- Omit timestamp from list output
  ([`25efb91`](https://github.com/kambiz-aghaiepour/dracs/commit/25efb91a60f61ee7616e75b47de9a0c88af009c8))

The timestamp is the epoch time for the warranty expiration. It's useful internally for calculations, but has little to no value in showing up in the list output.


## v1.2.0 (2026-05-06)

### Features

- Add the ability to refresh by model or --all
  ([`db67563`](https://github.com/kambiz-aghaiepour/dracs/commit/db675639b429090a55aca0052254440e0295dae0))

The refresh command line can now refresh all hosts that match a model, or simply all hosts.


## v1.1.0 (2026-05-06)

### Features

- Use python rich for tables
  ([`b6bc781`](https://github.com/kambiz-aghaiepour/dracs/commit/b6bc781482bc18156126b1df05abba69b50b2603))

Replace tabulate with python-rich for enhanced table output with color-coded columns for better readability.


## v1.0.4 (2026-05-06)

### Bug Fixes

- Build and publish for pypi
  ([`d05547e`](https://github.com/kambiz-aghaiepour/dracs/commit/d05547ee0221126d4165a39eb8f2e1a49ea4db79))

Check if the dist/* files are created before attempting to publish.


## v1.0.3 (2026-05-06)

### Bug Fixes

- Small typo in version tag used
  ([`60f796f`](https://github.com/kambiz-aghaiepour/dracs/commit/60f796ff72e342a9a7c5e7670907f3a09905303c))

Need to use "uses: pypa/gh-action-pypi-publish@release/v1"

- Get semantic release and publishing working
  ([`e5164da`](https://github.com/kambiz-aghaiepour/dracs/commit/e5164da603b265e7b46f69d0e2ef7cc7b739fbf1))

Remove the duplicate workflow. Ensure proper build and release including publishing to pypi.

- Tab issue in workflow yml
  ([`5b5790a`](https://github.com/kambiz-aghaiepour/dracs/commit/5b5790a3755ce2a48b227bd18962997696fb9c1d))

Looking for fully automated releasing.


## v1.0.2 (2026-05-06)

### Bug Fixes

- Pypi publishing workflow
  ([`30d289d`](https://github.com/kambiz-aghaiepour/dracs/commit/30d289d7e486cf1df1f2c2481a8d954be94f63ed))

Use pip to install uv.

- Pypi publishing is still not working
  ([`64e0f44`](https://github.com/kambiz-aghaiepour/dracs/commit/64e0f44d5ba6f66bd087ef009331a6c11dafe5b8))

uv should be listed as a requirement.

- Trying to get the pypi build working
  ([`25296f9`](https://github.com/kambiz-aghaiepour/dracs/commit/25296f978bef1c8e951fedc0b35efa40cc89d635))

- Ensure proper build
  ([`05a9993`](https://github.com/kambiz-aghaiepour/dracs/commit/05a9993bf5da7ac6fe8dd81ff8fcef9f5e63fbdf))

pypi publishing was still failing. This should fix it.


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
